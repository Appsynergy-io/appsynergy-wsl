#requires -Version 7.4
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-Sha256 {
    param([Parameter(Mandatory)][string]$Path)
    (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Read-BoundedFile {
    param([string]$Path, [long]$MaximumBytes = 1MB)
    $file = Get-Item -Force -LiteralPath $Path
    if ($file.Attributes.HasFlag([IO.FileAttributes]::ReparsePoint)) { throw 'Reparse-point input is not allowed' }
    $stream = [IO.File]::Open($file.FullName, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    $output = [IO.MemoryStream]::new()
    try {
        if ($stream.Length -gt $MaximumBytes) { throw 'Input size limit exceeded' }
        $buffer = [byte[]]::new(64KB)
        while (($count = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
            if ($output.Length + $count -gt $MaximumBytes) { throw 'Input size limit exceeded' }
            $output.Write($buffer, 0, $count)
        }
        return ,$output.ToArray()
    } finally { $output.Dispose(); $stream.Dispose() }
}

function ConvertFrom-BoundedJsonText {
    param([string]$Text)
    if ($Text.Length -gt 1MB) { throw 'JSON size limit exceeded' }
    $Text = $Text.TrimStart([char]0xFEFF)
    $document = [Text.Json.JsonDocument]::Parse($Text)
    try { Test-JsonProperty $document.RootElement } finally { $document.Dispose() }
    ConvertFrom-Json -InputObject $Text -AsHashtable -Depth 32
}

function Read-BoundedJson {
    param([Parameter(Mandatory)][string]$Path)
    $bytes = Read-BoundedFile $Path
    ConvertFrom-BoundedJsonText ([Text.UTF8Encoding]::new($false, $true).GetString($bytes))
}

function Receive-BoundedFile {
    param([Parameter(Mandatory)][uri]$Uri, [Parameter(Mandatory)][string]$Path,
          [Parameter(Mandatory)][long]$MaximumBytes)
    if ($Uri.Scheme -ne 'https' -or $Uri.Host -ne 'github.com' -or $MaximumBytes -le 0 -or $MaximumBytes -gt 32GB) {
        throw 'Invalid download origin or limit'
    }
    $client = [Net.Http.HttpClient]::new()
    $cancellation = [Threading.CancellationTokenSource]::new([TimeSpan]::FromMinutes(20))
    $client.DefaultRequestHeaders.UserAgent.ParseAdd('AppSynergy-WSL/0.1')
    $response = $null
    $output = $null
    try {
        $request = [Net.Http.HttpRequestMessage]::new([Net.Http.HttpMethod]::Get, $Uri)
        $response = $client.SendAsync($request, [Net.Http.HttpCompletionOption]::ResponseHeadersRead,
                                     $cancellation.Token).GetAwaiter().GetResult()
        $null = $response.EnsureSuccessStatusCode()
        if ($response.RequestMessage.RequestUri.Scheme -ne 'https' -or
            $response.Content.Headers.ContentLength -gt $MaximumBytes) { throw 'Download exceeds trusted size limit' }
        $inputStream = $response.Content.ReadAsStream()
        $output = [IO.File]::Open($Path, [IO.FileMode]::CreateNew)
        $buffer = [byte[]]::new(1MB)
        $total = 0L
        while (($count = $inputStream.ReadAsync($buffer, 0, $buffer.Length, $cancellation.Token).GetAwaiter().GetResult()) -gt 0) {
            $total += $count
            if ($total -gt $MaximumBytes) { throw 'Download exceeds trusted size limit' }
            $output.Write($buffer, 0, $count)
        }
    } finally {
        if ($null -ne $output) { $output.Dispose() }
        if ($null -ne $response) { $response.Dispose() }
        $cancellation.Dispose()
        $client.Dispose()
    }
}

function Test-JsonProperty {
    param([Text.Json.JsonElement]$Element)
    if ($Element.ValueKind -eq [Text.Json.JsonValueKind]::Object) {
        $names = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
        foreach ($property in $Element.EnumerateObject()) {
            if (-not $names.Add($property.Name)) { throw 'Duplicate JSON property' }
            Test-JsonProperty $property.Value
        }
    } elseif ($Element.ValueKind -eq [Text.Json.JsonValueKind]::Array) {
        foreach ($entry in $Element.EnumerateArray()) { Test-JsonProperty $entry }
    }
}

function Get-VerifiedManifest {
    param([Parameter(Mandatory)][string]$Directory,
          [string]$PublicKey = (Join-Path $PSScriptRoot 'trust/release.pem'),
          [switch]$MetadataOnly)
    $manifestPath = Join-Path $Directory 'manifest.json'
    $signaturePath = Join-Path $Directory 'manifest.sig'
    $manifestBytes = Read-BoundedFile $manifestPath
    $signatureBytes = Read-BoundedFile $signaturePath -MaximumBytes 384
    if ($signatureBytes.Length -ne 384) { throw 'Invalid manifest signature size' }
    $rsa = [Security.Cryptography.RSA]::Create()
    try {
        $rsa.ImportFromPem([Text.UTF8Encoding]::new($false, $true).GetString((Read-BoundedFile $PublicKey -MaximumBytes 64KB)))
        $valid = $rsa.VerifyData($manifestBytes, $signatureBytes,
            [Security.Cryptography.HashAlgorithmName]::SHA256, [Security.Cryptography.RSASignaturePadding]::Pkcs1)
        if (-not $valid) { throw 'Manifest signature verification failed' }
    } finally { $rsa.Dispose() }
    $manifest = ConvertFrom-BoundedJsonText ([Text.UTF8Encoding]::new($false, $true).GetString($manifestBytes))
    if ($manifest.schema -ne 1 -or $manifest.source -cnotmatch '^[0-9a-f]{40}$' -or
        $manifest.inputs_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
        $manifest.version -notmatch '^\d+\.\d+\.\d+$') { throw 'Invalid manifest identity' }
    if ($manifest.build.repository -cne 'Appsynergy-io/appsynergy-wsl' -or
        $manifest.build.event -cne 'push' -or $manifest.build.ref -cne 'refs/heads/main') {
        throw 'Candidate is not a trusted-main build'
    }
    $required = @('rootfs.tar.zst', 'kernel', 'modules.vhdx', 'tools.zip', 'inventory.json', 'source.tar.zst')
    if (@($manifest.artifacts.Keys).Count -ne $required.Count) { throw 'Unexpected artifact inventory' }
    foreach ($name in $required) {
        if (-not $manifest.artifacts.ContainsKey($name)) { throw "Missing artifact: $name" }
        $entry = $manifest.artifacts[$name]
        if ($entry.sha256 -cnotmatch '^[0-9a-f]{64}$' -or $entry.size -le 0 -or $entry.size -gt 32GB) {
            throw "Invalid artifact metadata: $name"
        }
        if (-not $MetadataOnly) {
            $path = Join-Path $Directory $name
            $file = Get-Item -Force -LiteralPath $path
            if ($file.Attributes.HasFlag([IO.FileAttributes]::ReparsePoint) -or
                $file.Length -ne $entry.size -or (Get-Sha256 $path) -cne $entry.sha256) {
                throw "Artifact hash or size mismatch: $name"
            }
        }
    }
    $manifest['_verified_sha256'] = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($manifestBytes)).ToLowerInvariant()
    $manifest
}

function Get-WslManagedValue {
    param([AllowEmptyString()][string]$Text)
    $section = ''
    $values = @{}
    foreach ($line in ($Text -split '\r?\n')) {
        if ($line -match '^\s*\[([^\]]+)\]\s*(?:[#;].*)?$') { $section = $Matches[1].Trim() }
        elseif ($section -ieq 'wsl2' -and $line -match '^\s*(kernel|kernelModules)\s*=(.*)$') {
            $key = $Matches[1].ToLowerInvariant()
            if ($values.ContainsKey($key)) { throw "Duplicate managed WSL setting: $key" }
            $values[$key] = $Matches[2].Trim()
        }
    }
    $values
}

function Get-VerifiedPromotion {
    param([Parameter(Mandatory)][string]$Directory, [Parameter(Mandatory)][hashtable]$Manifest,
          [string]$PublicKey = (Join-Path $PSScriptRoot 'trust/release.pem'))
    $path = Join-Path $Directory 'promotion.json'
    $signature = Join-Path $Directory 'promotion.json.rsa.sig'
    $promotionBytes = Read-BoundedFile $path
    $promotionSignature = Read-BoundedFile $signature -MaximumBytes 384
    if ($promotionSignature.Length -ne 384) { throw 'Invalid promotion signature size' }
    $rsa = [Security.Cryptography.RSA]::Create()
    try {
        $rsa.ImportFromPem([Text.UTF8Encoding]::new($false, $true).GetString((Read-BoundedFile $PublicKey -MaximumBytes 64KB)))
        if (-not $rsa.VerifyData($promotionBytes, $promotionSignature,
            [Security.Cryptography.HashAlgorithmName]::SHA256, [Security.Cryptography.RSASignaturePadding]::Pkcs1)) {
            throw 'Promotion signature verification failed'
        }
    } finally { $rsa.Dispose() }
    $proof = ConvertFrom-BoundedJsonText ([Text.UTF8Encoding]::new($false, $true).GetString($promotionBytes))
    if ($proof.schema -ne 2 -or $proof.version -cne $Manifest.version -or $proof.source -cne $Manifest.source -or
        $proof.publication_commit -cnotmatch '^[0-9a-f]{40}$' -or
        $proof.manifest_sha256 -cne $Manifest._verified_sha256) {
        throw 'Release provenance does not match this exact manifest'
    }
    $proof
}

function ConvertTo-WslKernelConfiguration {
    param([AllowEmptyString()][string]$Text, [Parameter(Mandatory)][hashtable]$Values)
    $null = Get-WslManagedValue $Text
    $newline = if ($Text.Contains("`r`n")) { "`r`n" } else { "`n" }
    $lines = [Collections.Generic.List[string]]::new()
    $section = ''
    $inserted = $false
    foreach ($line in ($Text -split '\r?\n')) {
        if ($line -match '^\s*\[([^\]]+)\]\s*(?:[#;].*)?$') {
            $section = $Matches[1].Trim()
            $lines.Add($line)
            if ($section -ieq 'wsl2') {
                if ($inserted) { throw 'Duplicate [wsl2] section requires manual repair' }
                foreach ($key in @('kernel', 'kernelmodules')) {
                    if ($Values.ContainsKey($key)) { $lines.Add("$key=$($Values[$key])") }
                }
                $inserted = $true
            }
        } elseif (-not ($section -ieq 'wsl2' -and $line -match '^\s*(kernel|kernelModules)\s*=')) {
            $lines.Add($line)
        }
    }
    if (-not $inserted) {
        $lines.Add('[wsl2]')
        foreach ($key in @('kernel', 'kernelmodules')) {
            if ($Values.ContainsKey($key)) { $lines.Add("$key=$($Values[$key])") }
        }
    }
    $lines -join $newline
}

function Write-AtomicText {
    param([Parameter(Mandatory)][string]$Path, [AllowEmptyString()][string]$Text)
    $temporary = "$Path.$([Guid]::NewGuid().ToString('N')).tmp"
    try {
        [IO.File]::WriteAllText($temporary, $Text, [Text.UTF8Encoding]::new($false))
        [IO.File]::Move($temporary, $Path, $true)
    } finally {
        if (Test-Path -LiteralPath $temporary) { [IO.File]::Delete($temporary) }
    }
}

function Copy-VerifiedCandidate {
    [CmdletBinding(SupportsShouldProcess)]
    param([string]$SourceDirectory, [string]$VersionsDirectory,
          [string]$PublicKey = (Join-Path $PSScriptRoot 'trust/release.pem'), [string]$ExpectedManifestSha256)
    $manifest = Get-VerifiedManifest $SourceDirectory -PublicKey $PublicKey
    $hash = $manifest._verified_sha256
    if ($ExpectedManifestSha256 -and $hash -cne $ExpectedManifestSha256) { throw 'Candidate changed before staging' }
    $target = Join-Path $VersionsDirectory "$($manifest.version)-$($hash.Substring(0, 16))"
    if (-not $PSCmdlet.ShouldProcess($target, 'Preserve and verify exact candidate bytes')) { return }
    if (-not (Test-Path -LiteralPath $target)) {
        $null = New-Item -ItemType Directory -Path $VersionsDirectory -Force
        $temporary = Join-Path $VersionsDirectory "copy-$([Guid]::NewGuid().ToString('N'))"
        $null = New-Item -ItemType Directory -Path $temporary
        try {
            foreach ($name in @('manifest.json', 'manifest.sig') + @($manifest.artifacts.Keys)) {
                Copy-Item -LiteralPath (Join-Path $SourceDirectory $name) -Destination (Join-Path $temporary $name)
            }
            $null = Get-VerifiedManifest $temporary -PublicKey $PublicKey
            if ((Get-Sha256 (Join-Path $temporary 'manifest.json')) -cne $hash) { throw 'Candidate changed during staging' }
            foreach ($name in @('promotion.json', 'promotion.json.rsa.sig')) {
                if (Test-Path -LiteralPath (Join-Path $SourceDirectory $name)) {
                    Copy-Item -LiteralPath (Join-Path $SourceDirectory $name) -Destination (Join-Path $temporary $name)
                }
            }
            [IO.Directory]::Move($temporary, $target)
        } finally {
            if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Recurse -Force }
        }
    }
    $null = Get-VerifiedManifest $target -PublicKey $PublicKey
    if ((Get-Sha256 (Join-Path $target 'manifest.json')) -cne $hash) { throw 'Conflicting immutable candidate directory' }
    $target
}

function Copy-KernelFallback {
    [CmdletBinding(SupportsShouldProcess)]
    param([hashtable]$Values, [string]$Directory)
    $defaults = @{}
    if (-not $Values.ContainsKey('kernel') -or -not $Values.ContainsKey('kernelmodules')) {
        $roots = @((Join-Path ([Environment]::GetFolderPath('ProgramFiles')) 'WSL/tools'))
        if (-not (Test-Path -LiteralPath (Join-Path $roots[0] 'kernel'))) {
            foreach ($package in @(Get-AppxPackage -Name '*WindowsSubsystemForLinux*' -ErrorAction SilentlyContinue)) {
                $roots += Join-Path $package.InstallLocation 'tools'
            }
        }
        foreach ($root in $roots) {
            if (Test-Path -LiteralPath (Join-Path $root 'kernel')) {
                $defaults.kernel = Join-Path $root 'kernel'
                foreach ($name in @('modules.vhd', 'modules.vhdx')) {
                    if (Test-Path -LiteralPath (Join-Path $root $name)) { $defaults.kernelmodules = Join-Path $root $name; break }
                }
                break
            }
        }
    }
    $sources = @{}
    foreach ($key in @('kernel', 'kernelmodules')) {
        if ($Values.ContainsKey($key)) { $sources[$key] = $Values[$key].Trim('"').Replace('\\', '\') }
        elseif ($defaults.ContainsKey($key)) { $sources[$key] = $defaults[$key] }
        else { throw "Cannot locate previous $key bytes; activation is blocked before configuration changes" }
        $file = Get-Item -Force -LiteralPath $sources[$key]
        if ($file.Attributes.HasFlag([IO.FileAttributes]::ReparsePoint) -or $file.Length -le 0 -or $file.Length -gt 32GB) {
            throw 'Invalid previous kernel/module file'
        }
    }
    if (-not $PSCmdlet.ShouldProcess($Directory, 'Preserve previous known-good kernel/module pair')) { return }
    $requiredBytes = 2GB
    foreach ($source in $sources.Values) { $requiredBytes += (Get-Item -Force -LiteralPath $source).Length }
    if ([IO.DriveInfo]::new([IO.Path]::GetPathRoot([IO.Path]::GetFullPath($Directory))).AvailableFreeSpace -lt $requiredBytes) {
        throw 'Insufficient space to preserve the previous kernel/module pair'
    }
    $null = New-Item -ItemType Directory -Path $Directory
    $fallback = @{}
    $artifacts = @()
    foreach ($key in @('kernel', 'kernelmodules')) {
        $name = if ($key -eq 'kernel') { 'kernel' } else { 'modules' + [IO.Path]::GetExtension($sources[$key]) }
        $target = Join-Path $Directory $name
        $hash = Get-Sha256 $sources[$key]
        Copy-Item -LiteralPath $sources[$key] -Destination $target
        if ((Get-Sha256 $target) -cne $hash) { throw 'Previous pair changed while being preserved' }
        $fallback[$key] = $target.Replace('\', '\\')
        $artifacts += @{ original = $sources[$key]; backup = $target; sha256 = $hash }
    }
    @{ values = $fallback; artifacts = $artifacts }
}

function Invoke-WslChecked {
    param([string[]]$Arguments, [int]$TimeoutSeconds = 120)
    $start = [Diagnostics.ProcessStartInfo]::new((Join-Path ([Environment]::SystemDirectory) 'wsl.exe'))
    $start.UseShellExecute = $false
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    foreach ($argument in $Arguments) { $start.ArgumentList.Add($argument) }
    $process = [Diagnostics.Process]::Start($start)
    try {
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            $process.Kill($true)
            throw "WSL operation timed out after $TimeoutSeconds seconds"
        }
        $output = ($stdout.GetAwaiter().GetResult() + $stderr.GetAwaiter().GetResult()).Replace("`0", '')
        if ($process.ExitCode -ne 0) { throw "WSL operation failed ($($process.ExitCode)): $output" }
        $output.Trim()
    } finally { $process.Dispose() }
}

function Test-AppSynergyHost {
    if (-not $IsWindows) { throw 'Windows 11 x64 is required' }
    if (-not [Environment]::Is64BitProcess -or [Runtime.InteropServices.RuntimeInformation]::OSArchitecture -ne [Runtime.InteropServices.Architecture]::X64) { throw 'Run x64 PowerShell on Windows x64' }
    $os = Get-CimInstance Win32_OperatingSystem
    if ([int]$os.BuildNumber -lt 22000 -or $os.OSArchitecture -notmatch '64') { throw 'Windows 11 x64 is required' }
    if ($ExecutionContext.SessionState.LanguageMode -ne 'FullLanguage') { throw 'Enterprise PowerShell language policy blocks this tool; request IT approval' }
    foreach ($setting in @('AllowWSL', 'AllowKernelUserSetting')) {
        $value = Get-ItemPropertyValue -LiteralPath 'HKLM:\Software\Policies\WSL' -Name $setting -ErrorAction SilentlyContinue
        if ($null -ne $value -and $value -eq 0) { throw "Enterprise WSL policy $setting is disabled; request IT approval" }
    }
    foreach ($type in @([Runtime.Intrinsics.X86.Sse2], [Runtime.Intrinsics.X86.Sse3],
                       [Runtime.Intrinsics.X86.Ssse3], [Runtime.Intrinsics.X86.Sse41],
                       [Runtime.Intrinsics.X86.Sse42], [Runtime.Intrinsics.X86.Avx],
                       [Runtime.Intrinsics.X86.Avx2], [Runtime.Intrinsics.X86.Bmi1],
                       [Runtime.Intrinsics.X86.Bmi2], [Runtime.Intrinsics.X86.Fma],
                       [Runtime.Intrinsics.X86.Lzcnt], [Runtime.Intrinsics.X86.Popcnt])) {
        if (-not $type::IsSupported) { throw "CPU/OS lacks x86-64-v3 feature: $type" }
    }
    $ecx = [Runtime.Intrinsics.X86.X86Base]::CpuId(1, 0).Item3
    foreach ($bit in @(13, 22, 26, 27, 29)) {
        if (($ecx -band (1 -shl $bit)) -eq 0) { throw "CPU/OS lacks required CPUID.1:ECX feature bit $bit" }
    }
    $extendedEcx = [Runtime.Intrinsics.X86.X86Base]::CpuId(-2147483647, 0).Item3
    if (($extendedEcx -band 1) -eq 0) { throw 'CPU lacks LAHF/SAHF in 64-bit mode' }
    $null = Invoke-WslChecked @('--status')
    $version = Invoke-WslChecked @('--version')
    if ($version -notmatch '(\d+\.\d+\.\d+)') { throw 'A current Store/MSI WSL installation is required' }
    if ([version]$Matches[1] -lt [version]'2.5.7') { throw 'WSL 2.5.7 or newer is required for custom kernel module VHD support' }
    $drive = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($env:LOCALAPPDATA))
    if ($drive.AvailableFreeSpace -lt 20GB) { throw 'At least 20 GiB free space is required' }
    [ordered]@{ WindowsBuild = $os.BuildNumber; Edition = $os.Caption; Wsl = $version; Cpu = 'x86-64-v3' }
}

function Invoke-KernelRollback {
    param([Parameter(Mandatory)][string]$ConfigPath, [Parameter(Mandatory)][hashtable]$Journal)
    if ((Test-Path -LiteralPath $ConfigPath) -and (Get-Item -Force -LiteralPath $ConfigPath).Attributes.HasFlag([IO.FileAttributes]::ReparsePoint)) {
        throw 'Managed configuration became a reparse point; refusing to overwrite it'
    }
    $current = if (Test-Path -LiteralPath $ConfigPath) { [IO.File]::ReadAllText($ConfigPath) } else { '' }
    $managed = Get-WslManagedValue $current
    $restore = $Journal.previous
    if ($Journal.ContainsKey('previousArtifacts')) {
        foreach ($entry in $Journal.previousArtifacts) {
            if (-not (Test-Path -LiteralPath $entry.original) -or (Get-Sha256 $entry.original) -cne $entry.sha256) {
                foreach ($backup in $Journal.previousArtifacts) {
                    if ((Get-Sha256 $backup.backup) -cne $backup.sha256) { throw 'Preserved fallback bytes were altered' }
                }
                $restore = $Journal.fallback
                break
            }
        }
    }
    $pairs = @($restore, $Journal.previous, $Journal.candidate)
    if ($Journal.ContainsKey('fallback')) { $pairs += $Journal.fallback }
    $recognized = $false
    $alreadyRestored = $false
    for ($index = 0; $index -lt $pairs.Count; $index++) {
        $pair = $pairs[$index]
        $equal = $managed.Count -eq $pair.Count
        foreach ($key in $pair.Keys) {
            if (-not $managed.ContainsKey($key) -or $managed[$key] -cne $pair[$key]) { $equal = $false }
        }
        if ($equal) { $recognized = $true; if ($index -eq 0) { $alreadyRestored = $true } }
    }
    if ($alreadyRestored) { return }
    if (-not $recognized) { throw 'Managed settings were edited since activation; refusing to overwrite them' }
    $restored = ConvertTo-WslKernelConfiguration $current $restore
    Write-AtomicText $ConfigPath $restored
    if ($Journal.ContainsKey('previousExists') -and -not $Journal.previousExists -and
        -not @($restored -split '\r?\n' | Where-Object { $_ -notmatch '^\s*$|^\s*\[wsl2\]\s*$' }).Count) {
        [IO.File]::Delete($ConfigPath)
    }
}

Export-ModuleMember -Function Get-Sha256, Read-BoundedJson, Get-VerifiedManifest, Get-WslManagedValue,
    ConvertTo-WslKernelConfiguration, Write-AtomicText, Invoke-WslChecked, Test-AppSynergyHost, Invoke-KernelRollback, Receive-BoundedFile, Get-VerifiedPromotion,
    Copy-VerifiedCandidate, Copy-KernelFallback

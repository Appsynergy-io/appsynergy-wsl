# Run from Windows PowerShell 5.1 or PowerShell 7.4+: irm https://raw.githubusercontent.com/Appsynergy-io/appsynergy-wsl/main/windows/bootstrap.ps1 | iex
# Scope the bootstrap so it does not change the caller's preferences or functions.
& {
    Set-StrictMode -Version Latest
    $ErrorActionPreference = 'Stop'
    if ([Environment]::OSVersion.Platform -ne 'Win32NT' -or -not [Environment]::Is64BitProcess) {
        throw 'Run this command in 64-bit Windows PowerShell on Windows 11 x64.'
    }

    # This entry path deliberately uses Windows PowerShell 5.1/.NET Framework APIs.
    # The modern verifier below runs only after handoff to the private runtime.
    function Expand-BootstrapRuntime {
        param([string]$Archive, [string]$Destination)
        $zip = [IO.Compression.ZipFile]::OpenRead($Archive)
        try {
            $names = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
            $total = 0L
            if ($zip.Entries.Count -gt 10000) { throw 'Runtime archive entry limit exceeded' }
            foreach ($entry in $zip.Entries) {
                $name = $entry.FullName.TrimEnd('/')
                $total += $entry.Length
                if (-not $name -or $name -match '[\\:<>"|?*]' -or $name.StartsWith('/') -or
                    @($name.Split('/') | Where-Object { $_ -in @('', '.', '..') -or $_.EndsWith('.') -or $_.EndsWith(' ') }).Count -gt 0 -or
                    -not $names.Add($name) -or $total -gt 1GB -or
                    (($entry.ExternalAttributes -shr 16) -band 61440) -eq 40960) {
                    throw 'Unsafe runtime archive contents'
                }
            }
            if (-not $names.Contains('pwsh.exe')) { throw 'Runtime executable is missing' }
        } finally { $zip.Dispose() }
        [IO.Compression.ZipFile]::ExtractToDirectory($Archive, $Destination)
    }

    function Invoke-BootstrapRuntime {
        param([string]$Body, [switch]$AllowProcessScripts)
        Add-Type -AssemblyName System.Net.Http
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $directory = Join-Path ([IO.Path]::GetTempPath()) "appsynergy-runtime-$([Guid]::NewGuid().ToString('N'))"
        $null = New-Item -ItemType Directory -Path $directory
        $client = [Net.Http.HttpClient]::new()
        $cancel = [Threading.CancellationTokenSource]::new([TimeSpan]::FromMinutes(10))
        $response = $null
        $output = $null
        try {
            Write-Output 'Preparing a verified private PowerShell runtime. Your installed PowerShell and permanent execution policy will not change.'
            $uri = 'https://github.com/PowerShell/PowerShell/releases/download/v7.4.22/PowerShell-7.4.22-win-x64.zip'
            $response = $client.GetAsync($uri, [Net.Http.HttpCompletionOption]::ResponseHeadersRead, $cancel.Token).GetAwaiter().GetResult()
            $null = $response.EnsureSuccessStatusCode()
            if ($response.RequestMessage.RequestUri.Scheme -ne 'https' -or $response.Content.Headers.ContentLength -gt 128MB) { throw 'Invalid runtime download' }
            $archive = Join-Path $directory 'runtime.zip'
            $output = [IO.File]::Open($archive, [IO.FileMode]::CreateNew)
            $inputStream = $response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
            $buffer = New-Object byte[] 65536
            $total = 0L
            while (($count = $inputStream.ReadAsync($buffer, 0, $buffer.Length, $cancel.Token).GetAwaiter().GetResult()) -gt 0) {
                $total += $count
                if ($total -gt 128MB) { throw 'Runtime download size limit exceeded' }
                $output.Write($buffer, 0, $count)
            }
            $output.Dispose()
            $output = $null
            if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ine '5f89f51860a1a3b07937945663afd7cbd44387361ff9985f5758939cb9bd6359') {
                throw 'PowerShell runtime hash verification failed'
            }
            $runtime = Join-Path $directory 'runtime'
            Expand-BootstrapRuntime $archive $runtime
            $executable = Join-Path $runtime 'pwsh.exe'
            $signature = Get-AuthenticodeSignature -LiteralPath $executable
            if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch '(?:^|, )O=Microsoft Corporation(?:,|$)') {
                throw 'PowerShell runtime Microsoft signature verification failed'
            }
            $scriptPath = Join-Path $directory 'bootstrap.ps1'
            [IO.File]::WriteAllText($scriptPath, "& {`n$Body`n}", [Text.UTF8Encoding]::new($false))
            # Continue the exact already-invoked script, without fetching mutable code
            # again or passing an oversized encoded command. Keep interactive stdin.
            $quotedPath = $scriptPath.Replace("'", "''")
            $runtimeArguments = @('-NoLogo', '-NoProfile')
            if ($AllowProcessScripts) { $runtimeArguments += @('-ExecutionPolicy', 'RemoteSigned') }
            & $executable @runtimeArguments -Command "& ([scriptblock]::Create([IO.File]::ReadAllText('$quotedPath')))"
            if ($LASTEXITCODE -ne 0) { throw "Installer stopped with exit code $LASTEXITCODE" }
        } finally {
            if ($null -ne $output) { $output.Dispose() }
            if ($null -ne $response) { $response.Dispose() }
            $cancel.Dispose()
            $client.Dispose()
            Remove-Item -LiteralPath $directory -Recurse -Force
        }
    }

    function Get-BootstrapPolicyAction {
        param([string]$Effective, [string]$Machine, [string]$User)
        if ($Effective -eq 'AllSigned' -or ($Effective -eq 'Restricted' -and
            ($Machine -ne 'Undefined' -or $User -ne 'Undefined'))) { return 'Blocked' }
        if ($Effective -eq 'Restricted') { return 'Consent' }
        return 'Continue'
    }

    $allowProcessScripts = $false
    $policyAction = Get-BootstrapPolicyAction (Get-ExecutionPolicy) (Get-ExecutionPolicy -Scope MachinePolicy) (Get-ExecutionPolicy -Scope UserPolicy)
    if ($policyAction -eq 'Blocked') {
        throw 'Your configured execution policy does not permit these installer scripts. Ask your administrator to approve deployment; the installer will not override this policy.'
    }
    if ($policyAction -eq 'Consent') {
        $consent = Read-Host 'Windows blocks script files by default. Allow verified installer scripts in a temporary child process only (RemoteSigned)? No permanent policy change. [y/N]'
        if ($consent -inotmatch '^(y|yes)$') { Write-Output 'Installation cancelled; no settings changed.'; return }
        $allowProcessScripts = $true
    }
    if ($PSVersionTable.PSVersion -lt [version]'7.4' -or $allowProcessScripts) {
        Invoke-BootstrapRuntime -Body $MyInvocation.MyCommand.ScriptBlock.ToString() -AllowProcessScripts:$allowProcessScripts
        return
    }

    function Receive-BootstrapVerifier {
        param([string]$Path)
        $uri = 'https://raw.githubusercontent.com/Appsynergy-io/appsynergy-wsl/f9cb2604168fa28121721f3b7296035c1c1d60ae/windows/AppSynergy.psm1'
        $client = [Net.Http.HttpClient]::new()
        $client.Timeout = [TimeSpan]::FromSeconds(60)
        try {
            $client.MaxResponseContentBufferSize = 1MB
            $bytes = $client.GetByteArrayAsync($uri).GetAwaiter().GetResult()
            $hash = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant()
            if ($hash -cne '44feedbf409b404e9a297adb4117836601ff4c712f6f93a5d1a3db8765836834') {
                throw 'Bootstrap verifier hash mismatch'
            }
            [IO.File]::WriteAllBytes($Path, $bytes)
        } finally { $client.Dispose() }
    }

    function Expand-VerifiedToolArchive {
        param([string]$Archive, [hashtable]$Entry, [string]$Destination)
        if ($Entry.size -le 0 -or $Entry.size -gt 16MB -or
            (Get-Item -LiteralPath $Archive).Length -ne $Entry.size -or
            (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant() -cne $Entry.sha256) {
            throw 'Installer archive hash or size mismatch'
        }
        $zip = [IO.Compression.ZipFile]::OpenRead($Archive)
        try {
            $allowed = @('LICENSE', 'windows/AppSynergy.ps1', 'windows/AppSynergy.psm1',
                         'windows/Install.ps1', 'windows/Acceptance.ps1', 'windows/bootstrap.ps1', 'windows/trust/release.pem')
            $names = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
            $total = 0L
            foreach ($file in $zip.Entries) {
                $total += $file.Length
                if ($file.FullName -cnotin $allowed -or -not $names.Add($file.FullName) -or
                    $file.Length -gt 4MB -or $total -gt 16MB) { throw 'Unsafe installer archive contents' }
            }
            foreach ($required in @('windows/Install.ps1', 'windows/AppSynergy.ps1', 'windows/AppSynergy.psm1', 'windows/trust/release.pem')) {
                if (-not $names.Contains($required)) { throw "Missing installer file: $required" }
            }
            # A fresh destination and explicit regular-file writes prevent ZIP links,
            # traversal and overwrites. Inspect every entry before writing any entry.
            $null = New-Item -ItemType Directory -Path $Destination
            foreach ($file in $zip.Entries) {
                $target = Join-Path $Destination $file.FullName
                $null = New-Item -ItemType Directory -Path (Split-Path $target) -Force
                [IO.Compression.ZipFileExtensions]::ExtractToFile($file, $target, $false)
            }
        } finally { $zip.Dispose() }
    }

    $work = Join-Path ([IO.Path]::GetTempPath()) "appsynergy-bootstrap-$([Guid]::NewGuid().ToString('N'))"
    $verifier = $null
    $null = New-Item -ItemType Directory -Path $work
    try {
        $modulePath = Join-Path $work 'AppSynergy.psm1'
        Receive-BootstrapVerifier $modulePath
        $verifier = Import-Module $modulePath -PassThru -Force
        $key = Join-Path $work 'release.pem'
        $publicKey = @'
-----BEGIN PUBLIC KEY-----
MIIBojANBgkqhkiG9w0BAQEFAAOCAY8AMIIBigKCAYEAmjjqudkHGNcmStCJouJZ
7g4r7F6WjbWUxdIpBHtYfcYIybnVHbGIWtWj9ELbdlovuvpI6P/yaIRRBXKTKEY1
TQ6ULAfBZ74I0MX6ypmKjPVDWLuoHu83ZXaS+tOx9DbRr7vQ05eSWvNmm3kViXPu
ar2BKhdxCMDnsphUJXtwvHFMpb4hBS9lymDUC6J9v9qyN5A1cggmm6cgxGtvkb7k
/FtPcANH0rBC+XDnYRK2gFWHp1sXeJA0FS/UVHi7JDQRLdHFS7x/ADlOs0bxGlp0
fFbr4poB3UBPWxu8LMoRj8pvH1b5VsTNz34WD47doVvmpYDSqh0fDyBKmeyQ76CT
itdPz1EYM0+h8RKqQGtYxhlc8ZLElcq0glBH1e7bravAhmrSLwfEt8RQbm0v08TX
jcFpYObyPy8l9Hb1ZjOusz4UDNsm7gqX8xsgOLjAicfW+6FXtHOevxwTe76w83v3
Ch1iztM5rGg/rhjRILDvDcYRkRsqg+2cqiYPUoSw3qDRAgMBAAE=
-----END PUBLIC KEY-----
'@
        [IO.File]::WriteAllText($key, $publicKey)
        Write-Output 'AppSynergy WSL2 - downloading and verifying the latest accepted release.'
        $latest = 'https://github.com/Appsynergy-io/appsynergy-wsl/releases/latest/download'
        try {
            Receive-BoundedFile "$latest/manifest.json" (Join-Path $work 'manifest.json') 1MB
        } catch {
            throw 'No accepted release could be downloaded. Check the project release status and your network connection. Testing candidates are never installed automatically.'
        }
        Receive-BoundedFile "$latest/manifest.sig" (Join-Path $work 'manifest.sig') 384
        $manifest = Get-VerifiedManifest $work -PublicKey $key -MetadataOnly
        $origin = "https://github.com/Appsynergy-io/appsynergy-wsl/releases/download/appsynergy-wsl-v$($manifest.version)"
        foreach ($name in @('promotion.json', 'promotion.json.rsa.sig', 'acceptance.json')) {
            Receive-BoundedFile "$origin/$name" (Join-Path $work $name) 1MB
        }
        $null = Get-VerifiedPromotion $work $manifest -PublicKey $key
        if ($manifest.artifacts['tools.zip'].size -gt 16MB) { throw 'Installer archive exceeds size limit' }
        $archive = Join-Path $work 'tools.zip'
        Receive-BoundedFile "$origin/tools.zip" $archive $manifest.artifacts['tools.zip'].size
        $destination = Join-Path $work 'tools'
        Expand-VerifiedToolArchive $archive $manifest.artifacts['tools.zip'] $destination
        & (Join-Path $destination 'windows/Install.ps1') -Version $manifest.version
    } finally {
        if ($null -ne $verifier) { Remove-Module $verifier -ErrorAction SilentlyContinue }
        Remove-Item -LiteralPath $work -Recurse -Force
    }
}

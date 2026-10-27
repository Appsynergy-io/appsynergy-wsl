#requires -Version 7.4
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot '../windows/AppSynergy.psm1') -Force
$testDirectory = Join-Path ([IO.Path]::GetTempPath()) "appsynergy-test-$([Guid]::NewGuid().ToString('N'))"
$null = New-Item -ItemType Directory -Path $testDirectory
function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw "Assertion failed: $Message" }
}
function Assert-Throw {
    param([scriptblock]$Action, [string]$Message)
    $failed = $false
    try { & $Action } catch { $failed = $true }
    Assert-True $failed $Message
}
try {
    $installerDirectory = Join-Path $testDirectory 'installer'
    $null = New-Item -ItemType Directory -Path $installerDirectory
    Copy-Item (Join-Path $PSScriptRoot '../windows/Install.ps1') $installerDirectory
    $stub = @'
param($Operation, $Version, $CandidateDirectory, $Distribution, $LinuxUser, [switch]$AllowShutdown)
Add-Content (Join-Path $PSScriptRoot 'calls') $Operation
if ($Operation -eq 'Check') { return @{ Version = 'appsynergy-wsl-v0.1.0' } }
if ($Operation -eq 'Stage' -and (Test-Path (Join-Path $PSScriptRoot 'fail'))) { throw 'Invalid signature fixture' }
if ($Operation -in @('Stage', 'Install', 'Activate') -and -not $CandidateDirectory -and $Version -cne '0.1.0') { throw 'Wrong version' }
if ($Operation -eq 'Activate' -and -not $AllowShutdown) { throw 'Missing explicit consent' }
'@
    Write-AtomicText (Join-Path $installerDirectory 'AppSynergy.ps1') $stub
    $installer = Join-Path $installerDirectory 'Install.ps1'
    $calls = Join-Path $installerDirectory 'calls'
    $null = & $installer -LinuxUser developer
    Assert-True (((Get-Content $calls) -join ',') -ceq 'Status,Check,Stage,Install') 'default installation never activates'
    Remove-Item $calls
    $null = & $installer -Version 0.1.0 -LinuxUser developer -AllowShutdown
    Assert-True (((Get-Content $calls) -join ',') -ceq 'Status,Stage,Install,Activate') 'explicit consent activates after successful installation'
    Remove-Item $calls
    $null = & $installer -CandidateDirectory $testDirectory -LinuxUser developer
    Assert-True (((Get-Content $calls) -join ',') -ceq 'Status,Install') 'candidate install avoids release lookup and staging'
    Remove-Item $calls
    Write-AtomicText (Join-Path $installerDirectory 'fail') 'fixture'
    Assert-Throw { & $installer -Version 0.1.0 -LinuxUser developer -AllowShutdown } 'authentication failure stops installation'
    Assert-True (((Get-Content $calls) -join ',') -ceq 'Status,Stage') 'failed staging cannot import or activate'
    Remove-Item $calls
    $null = & $installer -LinuxUser developer -WhatIf
    Assert-True (-not (Test-Path $calls)) 'WhatIf performs no operations'
    Remove-Item (Join-Path $installerDirectory 'fail')
    function Invoke-InteractiveInstallerFixture {
        param([string[]]$Answers)
        $start = [Diagnostics.ProcessStartInfo]::new((Join-Path $PSHOME 'pwsh'))
        foreach ($argument in @('-NoProfile', '-File', $installer)) { $start.ArgumentList.Add($argument) }
        $start.UseShellExecute = $false
        $start.RedirectStandardInput = $true
        $start.RedirectStandardOutput = $true
        $start.RedirectStandardError = $true
        $process = [Diagnostics.Process]::Start($start)
        try {
            $output = $process.StandardOutput.ReadToEndAsync()
            $errors = $process.StandardError.ReadToEndAsync()
            foreach ($answer in $Answers) { $process.StandardInput.WriteLine($answer) }
            $process.StandardInput.Close()
            if (-not $process.WaitForExit(15000)) { $process.Kill($true); throw 'Interactive installer fixture timed out' }
            if ($process.ExitCode -ne 0) { throw ($errors.GetAwaiter().GetResult()) }
            $null = $output.GetAwaiter().GetResult()
        } finally { $process.Dispose() }
    }
    Invoke-InteractiveInstallerFixture @('INVALID', 'developer', 'no')
    Assert-True (((Get-Content $calls) -join ',') -ceq 'Status,Check,Stage,Install') 'flag-free flow validates username and respects declined activation'
    Remove-Item $calls
    Invoke-InteractiveInstallerFixture @('developer', 'yes')
    Assert-True (((Get-Content $calls) -join ',') -ceq 'Status,Check,Stage,Install,Activate') 'flag-free flow activates only after explicit yes'
    # Exercise the real bootstrap archive verifier without network or Windows side effects.
    $bootstrapText = Get-Content -Raw (Join-Path $PSScriptRoot '../windows/bootstrap.ps1')
    $bootstrapAst = [Management.Automation.Language.Parser]::ParseInput($bootstrapText, [ref]$null, [ref]$null)
    $policyFunction = $bootstrapAst.Find({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Get-BootstrapPolicyAction' }, $true)
    . ([scriptblock]::Create($policyFunction.Extent.Text))
    Assert-True ((Get-BootstrapPolicyAction Restricted Undefined Undefined) -eq 'Consent') 'default Windows policy requires explicit process-only consent'
    Assert-True ((Get-BootstrapPolicyAction Restricted Restricted Undefined) -eq 'Blocked') 'machine policy cannot be overridden'
    Assert-True ((Get-BootstrapPolicyAction Restricted Undefined Restricted) -eq 'Blocked') 'user group policy cannot be overridden'
    Assert-True ((Get-BootstrapPolicyAction AllSigned Undefined Undefined) -eq 'Blocked') 'explicit signature-only restriction preserved'
    Assert-True ((Get-BootstrapPolicyAction RemoteSigned Undefined Undefined) -eq 'Continue') 'permitted scripts need no policy change'
    $runtimeFunction = $bootstrapAst.Find({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Expand-BootstrapRuntime' }, $true)
    . ([scriptblock]::Create($runtimeFunction.Extent.Text))
    $runtimeZip = Join-Path $testDirectory 'runtime-fixture.zip'
    $zip = [IO.Compression.ZipFile]::Open($runtimeZip, [IO.Compression.ZipArchiveMode]::Create)
    try {
        $writer = [IO.StreamWriter]::new($zip.CreateEntry('pwsh.exe').Open())
        try { $writer.Write('fixture - never executed') } finally { $writer.Dispose() }
    } finally { $zip.Dispose() }
    Expand-BootstrapRuntime $runtimeZip (Join-Path $testDirectory 'runtime-safe')
    Assert-True (Test-Path (Join-Path $testDirectory 'runtime-safe/pwsh.exe')) 'runtime archive extracted'
    $zip = [IO.Compression.ZipFile]::Open($runtimeZip, [IO.Compression.ZipArchiveMode]::Update)
    try { $null = $zip.CreateEntry('../escape.dll') } finally { $zip.Dispose() }
    Assert-Throw { Expand-BootstrapRuntime $runtimeZip (Join-Path $testDirectory 'runtime-unsafe') } 'runtime traversal rejected before extraction'
    Assert-True (-not (Test-Path (Join-Path $testDirectory 'runtime-unsafe'))) 'unsafe runtime leaves no extraction directory'
    $expandFunction = $bootstrapAst.Find({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Expand-VerifiedToolArchive' }, $true)
    . ([scriptblock]::Create($expandFunction.Extent.Text))
    $zipPath = Join-Path $testDirectory 'tools-fixture.zip'
    $zip = [IO.Compression.ZipFile]::Open($zipPath, [IO.Compression.ZipArchiveMode]::Create)
    try {
        foreach ($name in @('windows/Install.ps1', 'windows/AppSynergy.ps1', 'windows/AppSynergy.psm1', 'windows/trust/release.pem')) {
            $writer = [IO.StreamWriter]::new($zip.CreateEntry($name).Open())
            try { $writer.Write('fixture') } finally { $writer.Dispose() }
        }
    } finally { $zip.Dispose() }
    $entry = @{ size = (Get-Item $zipPath).Length; sha256 = (Get-Sha256 $zipPath) }
    Expand-VerifiedToolArchive $zipPath $entry (Join-Path $testDirectory 'safe-tools')
    Assert-True (Test-Path (Join-Path $testDirectory 'safe-tools/windows/Install.ps1')) 'authenticated tools extracted'
    $entry.sha256 = '0' * 64
    Assert-Throw { Expand-VerifiedToolArchive $zipPath $entry (Join-Path $testDirectory 'bad-tools') } 'tampered installer rejected'
    Assert-True (-not (Test-Path (Join-Path $testDirectory 'bad-tools'))) 'no extraction before hash verification'
    $zip = [IO.Compression.ZipFile]::Open($zipPath, [IO.Compression.ZipArchiveMode]::Update)
    try { $null = $zip.CreateEntry('../escape.ps1') } finally { $zip.Dispose() }
    $entry = @{ size = (Get-Item $zipPath).Length; sha256 = (Get-Sha256 $zipPath) }
    Assert-Throw { Expand-VerifiedToolArchive $zipPath $entry (Join-Path $testDirectory 'unsafe-tools') } 'signed archive traversal rejected'
    Assert-True (-not (Test-Path (Join-Path $testDirectory 'unsafe-tools'))) 'all archive paths checked before extraction'
    $text = "# keep comment`r`n[wsl2]`r`nmemory=8GB`r`nkernel=C:\\old`r`nkernelModules=C:\\old.vhdx`r`n[experimental]`r`nautoMemoryReclaim=gradual`r`n"
    $values = @{ kernel = 'C:\\new'; kernelmodules = 'C:\\new.vhdx' }
    $changed = ConvertTo-WslKernelConfiguration $text $values
    Assert-True ($changed.Contains("memory=8GB`r`n")) 'unrelated setting survives'
    Assert-True ($changed.Contains('# keep comment')) 'comment survives'
    Assert-True ($changed.Contains('autoMemoryReclaim=gradual')) 'other section survives'
    Assert-True ((Get-WslManagedValue $changed).kernel -ceq 'C:\\new') 'candidate kernel installed'
    Assert-Throw { ConvertTo-WslKernelConfiguration "[wsl2]`nkernel=a`nkernel=b" $values } 'duplicate kernel settings rejected'
    Assert-Throw { ConvertTo-WslKernelConfiguration "[wsl2]`n[wsl2]" $values } 'duplicate sections rejected'
    $configPath = Join-Path $testDirectory '.wslconfig'
    Write-AtomicText $configPath $changed.Replace('memory=8GB', 'memory=12GB')
    $journal = @{ previous = (Get-WslManagedValue $text); candidate = $values }
    Invoke-KernelRollback $configPath $journal
    $restored = [IO.File]::ReadAllText($configPath)
    Assert-True ($restored.Contains('memory=12GB')) 'rollback preserves intervening unrelated edits'
    Assert-True ((Get-WslManagedValue $restored).kernel -ceq 'C:\\old') 'rollback restores prior pair'
    Invoke-KernelRollback $configPath $journal
    Assert-True ([IO.File]::ReadAllText($configPath) -ceq $restored) 'repeated rollback is idempotent'
    Write-AtomicText $configPath ($restored.Replace('C:\\old', 'C:\\user-selected'))
    Assert-Throw { Invoke-KernelRollback $configPath $journal } 'rollback refuses changed managed settings'
    $oldKernel = Join-Path $testDirectory 'old-kernel'
    $oldModules = Join-Path $testDirectory 'old-modules.vhdx'
    Write-AtomicText $oldKernel 'old kernel fixture'
    Write-AtomicText $oldModules 'old module fixture'
    $previous = @{ kernel = $oldKernel; kernelmodules = $oldModules }
    $preserved = Copy-KernelFallback $previous (Join-Path $testDirectory 'fallback')
    $recoverable = @{ previous = $previous; candidate = $values; fallback = $preserved.values; previousArtifacts = $preserved.artifacts }
    Remove-Item -LiteralPath $oldKernel, $oldModules
    Write-AtomicText $configPath $changed
    Invoke-KernelRollback $configPath $recoverable
    Assert-True ((Get-WslManagedValue ([IO.File]::ReadAllText($configPath))).kernel -ceq $preserved.values.kernel) 'Windows rollback uses preserved exact bytes when original pair is unavailable'
    Invoke-KernelRollback $configPath $recoverable
    Write-AtomicText $preserved.artifacts[0].backup 'tampered fallback'
    Write-AtomicText $configPath $changed
    Assert-Throw { Invoke-KernelRollback $configPath $recoverable } 'tampered fallback is rejected'
    $rsa = [Security.Cryptography.RSA]::Create(3072)
    try {
        $publicKey = Join-Path $testDirectory 'public.pem'
        Write-AtomicText $publicKey $rsa.ExportSubjectPublicKeyInfoPem()
        $artifacts = @{}
        foreach ($name in @('rootfs.tar.zst', 'kernel', 'modules.vhdx', 'tools.zip', 'inventory.json', 'source.tar.zst')) {
            $path = Join-Path $testDirectory $name
            Write-AtomicText $path 'test fixture'
            $artifacts[$name] = @{ sha256 = (Get-Sha256 $path); size = (Get-Item $path).Length }
        }
        $manifest = @{ schema = 1; source = ('a' * 40); inputs_sha256 = ('b' * 64); version = '0.1.0';
                       build = @{ repository = 'Appsynergy-io/appsynergy-wsl'; event = 'push'; ref = 'refs/heads/main'; run_id = 1; run_attempt = 1 };
                       artifacts = $artifacts }
        $manifestPath = Join-Path $testDirectory 'manifest.json'
        Write-AtomicText $manifestPath ($manifest | ConvertTo-Json -Depth 16)
        $signature = $rsa.SignData([IO.File]::ReadAllBytes($manifestPath), [Security.Cryptography.HashAlgorithmName]::SHA256,
                                  [Security.Cryptography.RSASignaturePadding]::Pkcs1)
        [IO.File]::WriteAllBytes((Join-Path $testDirectory 'manifest.sig'), $signature)
        $null = Get-VerifiedManifest $testDirectory -PublicKey $publicKey
        $module = Get-Module AppSynergy
        $reader = (Get-Command Read-BoundedJson).ScriptBlock
        $unverified = $manifest.Clone()
        $unverified.version = '9.9.9'
        & $module {
            param($value)
            $script:UnverifiedFixture = $value
            function script:Read-BoundedJson { param([string]$Path) if ([IO.Path]::GetFileName($Path) -cne 'manifest.json') { throw 'Unexpected fixture read' }; $script:UnverifiedFixture }
        } $unverified
        try {
            $authenticated = Get-VerifiedManifest $testDirectory -PublicKey $publicKey
            Assert-True ($authenticated.version -ceq '0.1.0') 'metadata must be parsed from the exact bytes whose signature was verified'
        } finally {
            & $module { param($body) Set-Item -Path Function:script:Read-BoundedJson -Value $body } $reader
        }
        $owned = Copy-VerifiedCandidate $testDirectory (Join-Path $testDirectory 'owned') -PublicKey $publicKey
        Assert-True ((Get-Sha256 (Join-Path $owned 'kernel')) -ceq $artifacts.kernel.sha256) 'import and activation consume a verified owned copy'
        $ownedAgain = Copy-VerifiedCandidate $testDirectory (Join-Path $testDirectory 'owned') -PublicKey $publicKey
        Assert-True ($ownedAgain -ceq $owned) 'candidate staging is idempotent'
        $proof = @{ schema = 2; source = $manifest.source; version = $manifest.version; publication_commit = ('d' * 40);
                    manifest_sha256 = (Get-Sha256 $manifestPath) }
        $proofPath = Join-Path $testDirectory 'promotion.json'
        Write-AtomicText $proofPath ($proof | ConvertTo-Json)
        $proofSignature = $rsa.SignData([IO.File]::ReadAllBytes($proofPath), [Security.Cryptography.HashAlgorithmName]::SHA256,
                                       [Security.Cryptography.RSASignaturePadding]::Pkcs1)
        [IO.File]::WriteAllBytes((Join-Path $testDirectory 'promotion.json.rsa.sig'), $proofSignature)
        $verified = Get-VerifiedManifest $testDirectory -PublicKey $publicKey
        $null = Get-VerifiedPromotion $testDirectory $verified -PublicKey $publicKey
        $verified._verified_sha256 = '0' * 64
        Assert-Throw { Get-VerifiedPromotion $testDirectory $verified -PublicKey $publicKey } 'promotion binds exact manifest bytes'
        Write-AtomicText (Join-Path $testDirectory 'kernel') 'bad fixture!'
        Assert-Throw { Get-VerifiedManifest $testDirectory -PublicKey $publicKey } 'tampered bytes rejected'
        Write-AtomicText $manifestPath (($manifest | ConvertTo-Json -Depth 16) + ' ')
        Assert-Throw { Get-VerifiedManifest $testDirectory -PublicKey $publicKey -MetadataOnly } 'signature authenticates exact manifest bytes'
    } finally { $rsa.Dispose() }
    Write-AtomicText (Join-Path $testDirectory 'duplicate.json') '{"schema":1,"schema":2}'
    Assert-Throw { Read-BoundedJson (Join-Path $testDirectory 'duplicate.json') } 'duplicate JSON rejected'
    Assert-Throw { Receive-BoundedFile -Uri 'http://github.com/file' -Path (Join-Path $testDirectory 'never') -MaximumBytes 1MB } 'HTTP rejected before network access'
    'PASS PowerShell signature, tamper, configuration-preservation and Windows-independent rollback tests'
} finally { Remove-Item -LiteralPath $testDirectory -Recurse -Force }

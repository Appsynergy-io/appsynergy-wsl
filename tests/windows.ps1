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
        Write-AtomicText (Join-Path $testDirectory 'acceptance.json') '{"fixture":true}'
        $proof = @{ schema = 1; source = $manifest.source; version = $manifest.version; acceptance_commit = ('d' * 40);
                    manifest_sha256 = (Get-Sha256 $manifestPath); maintainer_acceptance_sha256 = (Get-Sha256 (Join-Path $testDirectory 'acceptance.json')) }
        $proofPath = Join-Path $testDirectory 'promotion.json'
        Write-AtomicText $proofPath ($proof | ConvertTo-Json)
        $proofSignature = $rsa.SignData([IO.File]::ReadAllBytes($proofPath), [Security.Cryptography.HashAlgorithmName]::SHA256,
                                       [Security.Cryptography.RSASignaturePadding]::Pkcs1)
        [IO.File]::WriteAllBytes((Join-Path $testDirectory 'promotion.json.rsa.sig'), $proofSignature)
        $verified = Get-VerifiedManifest $testDirectory -PublicKey $publicKey
        $null = Get-VerifiedPromotion $testDirectory $verified -PublicKey $publicKey
        Write-AtomicText (Join-Path $testDirectory 'acceptance.json') '{"changed":true}'
        Assert-Throw { Get-VerifiedPromotion $testDirectory $verified -PublicKey $publicKey } 'promotion binds exact acceptance bytes'
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

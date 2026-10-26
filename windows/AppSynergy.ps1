#requires -Version 7.4
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][ValidateSet('Status', 'Check', 'Stage', 'Install', 'Activate', 'Rollback')][string]$Operation,
    [string]$Version,
    [string]$CandidateDirectory,
    [ValidatePattern('^[A-Za-z][A-Za-z0-9_-]{0,63}$')][string]$Distribution = 'AppSynergy',
    [ValidatePattern('^[a-z][a-z0-9_-]{0,30}$')][string]$LinuxUser,
    [switch]$AllowShutdown
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'AppSynergy.psm1') -Force
if (-not $IsWindows) { throw 'Run this tool in PowerShell 7.4+ on Windows 11 x64' }
$base = Join-Path $env:LOCALAPPDATA 'AppSynergy/WSL'
$config = Join-Path $env:USERPROFILE '.wslconfig'
$journalPath = Join-Path $base 'activation.json'
$wslExecutable = Join-Path ([Environment]::SystemDirectory) 'wsl.exe'

function Assert-SafeDirectory {
    param([string]$Path)
    $current = [IO.Path]::GetFullPath($Path)
    while ($current) {
        if ((Test-Path -LiteralPath $current) -and
            (Get-Item -Force -LiteralPath $current).Attributes.HasFlag([IO.FileAttributes]::ReparsePoint)) {
            throw "Reparse-point path is not allowed: $current"
        }
        $current = [IO.Path]::GetDirectoryName($current)
    }
}

if ($Operation -eq 'Status') {
    Test-AppSynergyHost
    Invoke-WslChecked @('--list', '--verbose')
    if (Test-Path -LiteralPath $journalPath) { Read-BoundedJson $journalPath }
    return
}
if ($Operation -eq 'Check') {
    $release = Invoke-RestMethod -Uri 'https://api.github.com/repos/Appsynergy-io/appsynergy-wsl/releases/latest' -TimeoutSec 30
    if ($release.tag_name -notmatch '^appsynergy-wsl-v\d+\.\d+\.\d+$' -or $release.draft -or $release.prerelease) {
        throw 'No supported AppSynergy WSL release is available'
    }
    [ordered]@{ Version = $release.tag_name; Url = $release.html_url; Activated = (Test-Path -LiteralPath $journalPath) }
    return
}
Assert-SafeDirectory $base
if ((Test-Path -LiteralPath $config) -and (Get-Item -Force -LiteralPath $config).Attributes.HasFlag([IO.FileAttributes]::ReparsePoint)) { throw 'A reparse-point .wslconfig requires manual review before activation or rollback' }
if (-not $PSCmdlet.ShouldProcess($base, $Operation)) { return }
$null = New-Item -ItemType Directory -Path $base -Force
$lockPath = Join-Path $base 'operation.lock'
$operationLock = [IO.File]::Open($lockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
try {
    if ($Operation -eq 'Stage') {
        if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw 'Stage requires -Version X.Y.Z' }
        $target = Join-Path $base "versions/$Version"
        Assert-SafeDirectory $target
        if (Test-Path -LiteralPath $target) {
            $existingManifest = Get-VerifiedManifest $target
            Get-VerifiedPromotion $target $existingManifest
            return
        }
        $temporary = Join-Path $base "stage-$([Guid]::NewGuid().ToString('N'))"
        $null = New-Item -ItemType Directory -Path $temporary
        $origin = "https://github.com/Appsynergy-io/appsynergy-wsl/releases/download/appsynergy-wsl-v$Version"
        try {
            foreach ($name in @('manifest.json', 'manifest.sig')) {
                Receive-BoundedFile -Uri "$origin/$name" -Path (Join-Path $temporary $name) -MaximumBytes 1MB
            }
            $manifest = Get-VerifiedManifest $temporary -MetadataOnly
            if ($manifest.version -cne $Version) { throw 'Signed version does not match requested version' }
            foreach ($name in @('promotion.json', 'promotion.json.rsa.sig', 'acceptance.json')) {
                Receive-BoundedFile -Uri "$origin/$name" -Path (Join-Path $temporary $name) -MaximumBytes 1MB
            }
            $null = Get-VerifiedPromotion $temporary $manifest
            $drive = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($base))
            $needed = ($manifest.artifacts.Values | Measure-Object -Property size -Sum).Sum + 2GB
            if ($drive.AvailableFreeSpace -lt $needed) { throw 'Insufficient space for all signed candidate artifacts' }
            foreach ($name in $manifest.artifacts.Keys) {
                Receive-BoundedFile -Uri "$origin/$name" -Path (Join-Path $temporary $name) -MaximumBytes $manifest.artifacts[$name].size
            }
            $null = Get-VerifiedManifest $temporary
            $null = New-Item -ItemType Directory -Path (Split-Path $target) -Force
            [IO.Directory]::Move($temporary, $target)
            "Verified release staged at $target. Activation requires a separate command."
        } finally {
            if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Recurse -Force }
        }
        return
    }
    if ($Operation -eq 'Rollback') {
        if (-not $AllowShutdown) { throw 'Rollback changes the account-wide WSL kernel; pass -AllowShutdown explicitly' }
        $journal = Read-BoundedJson $journalPath
        if ($journal.state -eq 'rolled-back') { 'Previous configuration was already restored.'; return }
        Invoke-KernelRollback $config $journal
        # Configuration restoration precedes WSL calls, so a broken kernel cannot block rollback.
        $null = Invoke-WslChecked @('--shutdown')
        $journal['state'] = 'rolled-back'
        Write-AtomicText $journalPath ($journal | ConvertTo-Json -Depth 16)
        'Previous kernel configuration restored. Existing user files were preserved.'
        return
    }
    $null = Test-AppSynergyHost
    $supportedRelease = -not $CandidateDirectory
    if (-not $CandidateDirectory) {
        if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw 'Specify -Version or -CandidateDirectory' }
        $CandidateDirectory = Join-Path $base "versions/$Version"
    }
    Assert-SafeDirectory $CandidateDirectory
    $manifest = Get-VerifiedManifest $CandidateDirectory
    if ($supportedRelease) { $null = Get-VerifiedPromotion $CandidateDirectory $manifest }
    $versionsDirectory = Join-Path $base 'versions'
    Assert-SafeDirectory $versionsDirectory
    $CandidateDirectory = Copy-VerifiedCandidate $CandidateDirectory $versionsDirectory -ExpectedManifestSha256 $manifest._verified_sha256
    $manifest = Get-VerifiedManifest $CandidateDirectory
    $manifestHash = $manifest._verified_sha256
    if ($supportedRelease) { $null = Get-VerifiedPromotion $CandidateDirectory $manifest }
    if ($Operation -eq 'Install') {
        if (-not $LinuxUser) { throw 'Install requires -LinuxUser; passwd will prompt interactively' }
        $names = (Invoke-WslChecked @('--list', '--quiet')) -split '\r?\n' | ForEach-Object { $_.Trim() }
        if ($names -contains $Distribution) { throw 'Distribution already exists; refusing to overwrite it' }
        $location = Join-Path $base "distributions/$Distribution"
        Assert-SafeDirectory $location
        if (Test-Path -LiteralPath $location) { throw 'Distribution directory already exists' }
        $null = New-Item -ItemType Directory -Path $location
        $null = Invoke-WslChecked @('--import', $Distribution, $location, (Join-Path $CandidateDirectory 'rootfs.tar.zst'), '--version', '2') -TimeoutSeconds 1200
        & $wslExecutable -d $Distribution -u root -- appsynergy-first-run $LinuxUser
        if ($LASTEXITCODE -ne 0) { throw "Setup interrupted. Resume: wsl.exe -d $Distribution -u root -- appsynergy-first-run $LinuxUser" }
        if ($supportedRelease) { $null = Invoke-WslChecked @('-d', $Distribution, '-u', 'root', '--', 'appsynergy-enable-channel') }
        "Installed $Distribution. Activate the kernel explicitly before accepting the candidate."
        return
    }
    if ($Operation -eq 'Activate') {
        if (-not $AllowShutdown) { throw 'Kernel selection affects ALL account WSL2 distributions. Pass -AllowShutdown explicitly' }
        if (Test-Path -LiteralPath $journalPath) {
            $existing = Read-BoundedJson $journalPath
            if ($existing.state -eq 'pending') { throw 'Interrupted activation exists; roll it back before activating again' }
            if ($existing.state -eq 'active' -and $existing.manifest_sha256 -ceq $manifestHash) {
                $currentValues = Get-WslManagedValue ([IO.File]::ReadAllText($config))
                foreach ($key in @('kernel', 'kernelmodules')) {
                    if (-not $currentValues.ContainsKey($key) -or $currentValues[$key] -cne $existing.candidate[$key]) {
                        throw 'Active journal and account kernel settings differ; review Status before proceeding'
                    }
                }
                $null = Invoke-WslChecked @('-d', $Distribution, '-u', 'root', '--', 'appsynergy-health-check') -TimeoutSeconds 180
                'This candidate is already active.'
                return
            }
        }
        $before = if (Test-Path -LiteralPath $config) { [IO.File]::ReadAllText($config) } else { '' }
        $candidate = @{
            kernel = [IO.Path]::GetFullPath((Join-Path $CandidateDirectory 'kernel')).Replace('\', '\\')
            kernelmodules = [IO.Path]::GetFullPath((Join-Path $CandidateDirectory 'modules.vhdx')).Replace('\', '\\')
        }
        $fallbackDirectory = Join-Path $base "fallback/$([Guid]::NewGuid().ToString('N'))"
        Assert-SafeDirectory (Split-Path $fallbackDirectory)
        # Explicit activation consent above permits stopping sessions. Stop the
        # old VM before copying its mounted module VHD, so Windows file sharing
        # cannot prevent preserving the previous pair.
        $null = Invoke-WslChecked @('--shutdown')
        $preserved = Copy-KernelFallback (Get-WslManagedValue $before) $fallbackDirectory
        $journal = @{ fallback = $preserved.values; previousArtifacts = $preserved.artifacts; schema = 1; state = 'pending'; previous = (Get-WslManagedValue $before); candidate = $candidate;
                      previousExists = (Test-Path -LiteralPath $config);
                      version = $manifest.version; manifest_sha256 = $manifestHash }
        Write-AtomicText (Join-Path $base 'previous.wslconfig') $before
        Write-AtomicText $journalPath ($journal | ConvertTo-Json -Depth 16)
        Write-AtomicText $config (ConvertTo-WslKernelConfiguration $before $candidate)
        try {
            $null = Invoke-WslChecked @('--shutdown')
            $null = Invoke-WslChecked @('-d', $Distribution, '-u', 'root', '--', 'appsynergy-health-check') -TimeoutSeconds 180
            if ($supportedRelease) { $null = Invoke-WslChecked @('-d', $Distribution, '-u', 'root', '--', 'appsynergy-enable-channel') }
            $journal.state = 'active'
            Write-AtomicText $journalPath ($journal | ConvertTo-Json -Depth 16)
            "Activated verified kernel/module pair $($manifest.version). Previous pair remains available."
        } catch {
            Invoke-KernelRollback $config $journal
            $journal.state = 'rolled-back'
            Write-AtomicText $journalPath ($journal | ConvertTo-Json -Depth 16)
            $null = Invoke-WslChecked @('--shutdown')
            throw "Candidate failed health checks; previous configuration restored. $($_.Exception.Message)"
        }
    }
} finally { $operationLock.Dispose() }

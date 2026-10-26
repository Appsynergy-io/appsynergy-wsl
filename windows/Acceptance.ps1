#requires -Version 7.4
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][ValidateSet('Start', 'Setup', 'Resume', 'Finish', 'Cleanup')][string]$Phase,
    [string]$CandidateDirectory,
    [string]$StateDirectory,
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9-]{0,38}$')][string]$Maintainer,
    [switch]$AllowShutdown,
    [switch]$AllowFailedBootTest
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'AppSynergy.psm1') -Force
if (-not $IsWindows) { throw 'Maintainer acceptance requires Windows 11 Enterprise x64' }
$ownedBase = Join-Path $env:LOCALAPPDATA 'AppSynergy/WSL-Acceptance'
$tool = Join-Path $PSScriptRoot 'AppSynergy.ps1'
$wslExecutable = Join-Path ([Environment]::SystemDirectory) 'wsl.exe'
function Get-BootIdentifier {
    (Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().ToString('o')
}
function Write-ProgressState {
    Write-AtomicText (Join-Path $StateDirectory 'progress.json') ($script:state | ConvertTo-Json -Depth 24)
}
function Complete-Scenario {
    param([string]$Name, [string]$Diagnostic)
    $script:state.report.scenarios[$Name] = @{ result = 'PASS'; diagnostic = $Diagnostic.Replace($env:USERPROFILE, '<USERPROFILE>') }
    Write-ProgressState
}
function Invoke-TestLinux {
    param([string]$Command, [int]$Timeout = 120)
    Invoke-WslChecked @('-d', $script:state.distribution, '-u', 'root', '--', 'bash', '-euc', $Command) -TimeoutSeconds $Timeout
}
if ($Phase -eq 'Start') {
    if (-not $CandidateDirectory) { throw 'Start requires -CandidateDirectory containing a signed trusted-main candidate' }
    if (-not $Maintainer) { throw 'Start requires -Maintainer with your GitHub handle; Windows usernames are not published' }
    $hostInfo = Test-AppSynergyHost
    $os = Get-CimInstance Win32_OperatingSystem
    if ($os.Caption -notmatch 'Enterprise') { throw 'Required acceptance target is Windows 11 Enterprise' }
    $manifest = Get-VerifiedManifest $CandidateDirectory
    $id = [Guid]::NewGuid().ToString('N')
    $StateDirectory = Join-Path $ownedBase $id
    if (-not $PSCmdlet.ShouldProcess($StateDirectory, 'Create isolated maintainer acceptance resources')) { return }
    $null = New-Item -ItemType Directory -Path $StateDirectory
    $scenario = @{}
    foreach ($name in @('install-wsl2', 'first-run', 'first-run-interruption-retry', 'default-user-sudo', 'systemd',
                        'network', 'signed-full-update', 'development-tools', 'windows-interop', 'kernel-modules',
                        'shutdown-persistence', 'reboot-persistence', 'backup-import', 'kernel-update', 'failed-boot-rollback')) {
        $scenario[$name] = @{ result = 'NOT RUN'; diagnostic = '' }
    }
    $state = @{ schema = 1; id = $id; distribution = "AppSynergy-Test-$id"; recovery = "AppSynergy-Recovery-$id";
                candidate = [IO.Path]::GetFullPath($CandidateDirectory); user = 'acceptance'; phase = 'setup';
                originalConfig = ''; originalConfigExists = $false; setupInterruptionObserved = $false;
                report = @{ schema = 1; kind = 'maintainer-acceptance'; source = $manifest.source; build = $manifest.build;
                    manifest_sha256 = (Get-Sha256 (Join-Path $CandidateDirectory 'manifest.json')); inputs_sha256 = $manifest.inputs_sha256;
                    artifacts = $manifest.artifacts; maintainer = $Maintainer;
                    host = @{ edition = 'Enterprise'; build = [int]$os.BuildNumber; wsl_version = $hostInfo.Wsl;
                              architecture = 'x64'; cpu_baseline = 'x86-64-v3'; kernel_release = '' };
                    scenarios = $scenario; reboot = @{ before_boot_id = ''; after_boot_id = ''; resumed = $false } } }
    $configPath = Join-Path $env:USERPROFILE '.wslconfig'
    if (Test-Path -LiteralPath $configPath) {
        $state.originalConfig = [IO.File]::ReadAllText($configPath)
        $state.originalConfigExists = $true
    }
    Write-ProgressState
    $location = Join-Path $StateDirectory 'distribution'
    $null = New-Item -ItemType Directory -Path $location
    $null = Invoke-WslChecked @('--import', $state.distribution, $location, (Join-Path $CandidateDirectory 'rootfs.tar.zst'), '--version', '2') -TimeoutSeconds 1200
    $listing = Invoke-WslChecked @('--list', '--verbose')
    if ($listing -notmatch "(?m)$([regex]::Escape($state.distribution))\s+\S+\s+2\s*$") { throw 'Imported distribution was not observed as WSL2' }
    Complete-Scenario 'install-wsl2' 'Uniquely named distribution imported and listed as WSL2'
    "Acceptance progress: $StateDirectory"
    'Next run Setup with this -StateDirectory and -AllowShutdown. Setup requires an intentional interruption at the first password prompt, followed by retry.'
    return
}
if (-not $StateDirectory) { throw 'Specify the test-owned -StateDirectory printed by Start' }
$StateDirectory = [IO.Path]::GetFullPath($StateDirectory)
$prefix = [IO.Path]::GetFullPath($ownedBase) + [IO.Path]::DirectorySeparatorChar
if (-not $StateDirectory.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -or
    (Split-Path $StateDirectory -Leaf) -notmatch '^[0-9a-f]{32}$') { throw 'State directory is not harness-owned' }
$state = Read-BoundedJson (Join-Path $StateDirectory 'progress.json')
if ($state.id -cne (Split-Path $StateDirectory -Leaf) -or $state.distribution -cne "AppSynergy-Test-$($state.id)" -or
    $state.recovery -cne "AppSynergy-Recovery-$($state.id)") { throw 'Invalid ownership marker' }
if (-not $PSCmdlet.ShouldProcess($StateDirectory, "Run acceptance phase $Phase")) { return }
if ($Phase -eq 'Cleanup') {
    if (-not $AllowShutdown) { throw 'Cleanup requires -AllowShutdown to restore account-wide kernel settings' }
    $journalPath = Join-Path $env:LOCALAPPDATA 'AppSynergy/WSL/activation.json'
    if (Test-Path -LiteralPath $journalPath) {
        $journal = Read-BoundedJson $journalPath
        if ($journal.manifest_sha256 -ceq $state.report.manifest_sha256 -and $journal.state -eq 'active') {
            $journal.previous = Get-WslManagedValue $state.originalConfig
            $journal.previousExists = $state.originalConfigExists
            Invoke-KernelRollback (Join-Path $env:USERPROFILE '.wslconfig') $journal
            $journal.state = 'rolled-back'
            Write-AtomicText $journalPath ($journal | ConvertTo-Json -Depth 16)
        }
    }
    $null = Invoke-WslChecked @('--shutdown')
    $names = (Invoke-WslChecked @('--list', '--quiet')) -split '\r?\n' | ForEach-Object { $_.Trim() }
    foreach ($name in @($state.distribution, $state.recovery)) {
        if ($names -contains $name) { $null = Invoke-WslChecked @('--unregister', $name) }
    }
    # Keep progress/evidence for review; remove only harness-owned distro/backup data.
    foreach ($name in @('distribution', 'recovery', 'backup.tar')) {
        $path = Join-Path $StateDirectory $name
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force }
    }
    'Harness-owned distributions removed and previous managed kernel settings restored.'
    return
}
$manifest = Get-VerifiedManifest $state.candidate
if ((Get-Sha256 (Join-Path $state.candidate 'manifest.json')) -cne $state.report.manifest_sha256) { throw 'Candidate changed; start fresh acceptance' }
if ($Phase -eq 'Setup') {
    if (-not $AllowShutdown) { throw 'Setup requires explicit -AllowShutdown for account-wide kernel activation' }
    if ($state.phase -ne 'setup') { throw 'Setup has already completed; resume the recorded phase' }
    $setupStatus = Invoke-TestLinux 'if test -f /var/lib/appsynergy-wsl/complete; then echo complete; elif test -f /var/lib/appsynergy-wsl/user; then echo pending; else echo new; fi'
    if ($setupStatus -eq 'new') {
        'Interrupt the FIRST passwd prompt with Ctrl+C, then rerun Setup with this StateDirectory.'
        & $wslExecutable -d $state.distribution -u root -- appsynergy-first-run $state.user
        if ($LASTEXITCODE -eq 0) { throw 'No setup interruption observed; this scenario is unproven' }
        $setupStatus = Invoke-TestLinux 'test -f /var/lib/appsynergy-wsl/user; test ! -f /var/lib/appsynergy-wsl/complete; echo pending'
    }
    if ($setupStatus -eq 'pending') {
        $state.setupInterruptionObserved = $true
        Write-ProgressState
        & $wslExecutable -d $state.distribution -u root -- appsynergy-first-run $state.user
        if ($LASTEXITCODE -ne 0) { throw 'First-run retry failed; rerun Setup to retry' }
    }
    if (-not $state.setupInterruptionObserved) { throw 'No interrupted setup checkpoint was observed' }
    Complete-Scenario 'first-run-interruption-retry' 'Observed failed setup with pending journal, then successful same-user retry'
    $null = Invoke-TestLinux 'test -f /var/lib/appsynergy-wsl/complete; test -s /etc/machine-id'
    Complete-Scenario 'first-run' 'Setup journal completed and unique machine identity exists'
    $null = Invoke-WslChecked @('--terminate', $state.distribution)
    if ((Invoke-WslChecked @('-d', $state.distribution, '--', 'id', '-un')) -cne $state.user) { throw 'Default user mismatch' }
    & $wslExecutable -d $state.distribution -- sudo -k
    & $wslExecutable -d $state.distribution -- sudo -n true
    if ($LASTEXITCODE -eq 0) { throw 'Sudo unexpectedly succeeded without a password' }
    & $wslExecutable -d $state.distribution -- sudo -v
    if ($LASTEXITCODE -ne 0) { throw 'Password-authenticated sudo failed' }
    Complete-Scenario 'default-user-sudo' 'Observed configured default user and successful interactive sudo authentication'
    & $tool -Operation Activate -CandidateDirectory $state.candidate -Distribution $state.distribution -AllowShutdown
    Complete-Scenario 'kernel-update' 'Explicit kernel/module activation completed with successful health check'
    $state.report.host.kernel_release = Invoke-TestLinux 'uname -r'
    $inventory = Read-BoundedJson (Join-Path $state.candidate 'inventory.json')
    $configurationHash = (Invoke-TestLinux 'zcat /proc/config.gz | sha256sum').Split(' ')[0]
    if ($configurationHash -cne $inventory.kernel_config_sha256) { throw 'Running kernel configuration does not match the exact candidate' }
    $null = Invoke-TestLinux 'appsynergy-health-check; modprobe dummy; test -d /sys/module/dummy; unshare --mount --pid --fork true; systemd-run --wait --pipe -p MemoryMax=64M /usr/bin/true'
    Complete-Scenario 'kernel-modules' "AppSynergy kernel health, ABI-matched module loading, namespaces and cgroups passed; config SHA256 $configurationHash"
    $null = Invoke-TestLinux 'systemd-run --unit appsynergy-acceptance --wait --pipe /usr/bin/true; systemctl is-system-running --wait'
    Complete-Scenario 'systemd' 'Transient service lifecycle completed and system manager reports running'
    $null = Invoke-TestLinux 'getent hosts archlinux.org; curl --fail --max-time 30 --head https://archlinux.org'
    Complete-Scenario 'network' 'DNS resolution and HTTPS request succeeded'
    $null = Invoke-TestLinux 'pacman -Syu --noconfirm' -Timeout 1200
    Complete-Scenario 'signed-full-update' 'Signed pacman full-system update completed'
    $null = Invoke-TestLinux 'git --version; printf "int main(void){return 0;}\n" > /tmp/appsynergy-acceptance.c; cc /tmp/appsynergy-acceptance.c -o /tmp/appsynergy-acceptance; /tmp/appsynergy-acceptance; rm /tmp/appsynergy-acceptance.c /tmp/appsynergy-acceptance'
    Complete-Scenario 'development-tools' 'Git and C compilation/execution succeeded'
    $null = Invoke-WslChecked @('-d', $state.distribution, '--', 'cmd.exe', '/d', '/c', 'exit', '0')
    $markerPath = Join-Path $StateDirectory 'interop.txt'
    Write-AtomicText $markerPath $state.id
    $linuxPath = Invoke-WslChecked @('-d', $state.distribution, '--', 'wslpath', '-u', $markerPath)
    if ((Invoke-WslChecked @('-d', $state.distribution, '--', 'cat', $linuxPath)) -cne $state.id) { throw 'Windows filesystem sharing mismatch' }
    Complete-Scenario 'windows-interop' 'Windows executable invocation and Windows filesystem sharing succeeded'
    $null = Invoke-TestLinux "printf '%s' '$($state.id)' > /var/lib/appsynergy-wsl/acceptance-marker"
    $null = Invoke-WslChecked @('--shutdown')
    if ((Invoke-TestLinux 'cat /var/lib/appsynergy-wsl/acceptance-marker') -cne $state.id) { throw 'Shutdown persistence failed' }
    Complete-Scenario 'shutdown-persistence' 'Instance identity marker persisted through WSL shutdown'
    $state.report.reboot.before_boot_id = Get-BootIdentifier
    $state.phase = 'await-reboot'
    Write-ProgressState
    'Manually reboot Windows. Then run Resume with the same -StateDirectory. No automatic reboot is performed.'
    return
}
if ($Phase -eq 'Resume') {
    if ($state.phase -ne 'await-reboot') { throw 'No pending reboot checkpoint' }
    $boot = Get-BootIdentifier
    if ($boot -ceq $state.report.reboot.before_boot_id) { throw 'A Windows reboot has not occurred' }
    if ((Invoke-TestLinux 'cat /var/lib/appsynergy-wsl/acceptance-marker') -cne $state.id) { throw 'Reboot persistence failed' }
    $null = Invoke-TestLinux 'appsynergy-health-check'
    $state.report.reboot.after_boot_id = $boot
    $state.report.reboot.resumed = $true
    $state.phase = 'finish'
    Complete-Scenario 'reboot-persistence' 'Different Windows boot identity observed; instance data and kernel health survived'
    'Reboot persistence passed. Run Finish with -AllowShutdown -AllowFailedBootTest to test backup and failed-boot recovery.'
    return
}
if ($Phase -eq 'Finish') {
    if ($state.phase -ne 'finish') { throw 'Finish requires a completed reboot checkpoint' }
    if (-not $AllowShutdown -or -not $AllowFailedBootTest) { throw 'Finish requires -AllowShutdown and -AllowFailedBootTest; this deliberately interrupts account-wide WSL boot' }
    $backup = Join-Path $StateDirectory 'backup.tar'
    $null = Invoke-WslChecked @('--terminate', $state.distribution)
    $null = Invoke-WslChecked @('--export', $state.distribution, $backup) -TimeoutSeconds 1200
    $recoveryPath = Join-Path $StateDirectory 'recovery'
    $null = New-Item -ItemType Directory -Path $recoveryPath -Force
    $null = Invoke-WslChecked @('--import', $state.recovery, $recoveryPath, $backup, '--version', '2') -TimeoutSeconds 1200
    if ((Invoke-WslChecked @('-d', $state.recovery, '-u', 'root', '--', 'cat', '/var/lib/appsynergy-wsl/acceptance-marker')) -cne $state.id) { throw 'Backup/import recovery mismatch' }
    $null = Invoke-WslChecked @('--unregister', $state.recovery)
    Complete-Scenario 'backup-import' 'Exported backup imported into a separate owned distribution and preserved instance data'
    $journal = Read-BoundedJson (Join-Path $env:LOCALAPPDATA 'AppSynergy/WSL/activation.json')
    $activeKernel = $journal.candidate.kernel.Replace('\\', '\')
    $savedKernel = "$activeKernel.acceptance-saved"
    try {
        $null = Invoke-WslChecked @('--shutdown')
        Move-Item -LiteralPath $activeKernel -Destination $savedKernel
        $failed = $false
        try { $null = Invoke-TestLinux 'true' -Timeout 30 } catch { $failed = $true }
        if (-not $failed) { throw 'Missing-kernel boot failure was not observed' }
        & $tool -Operation Rollback -AllowShutdown
        $null = Invoke-TestLinux 'true'
        Complete-Scenario 'failed-boot-rollback' 'Observed candidate boot failure, restored previous settings directly from Windows, then booted successfully'
    } finally {
        if (Test-Path -LiteralPath $savedKernel) { Move-Item -LiteralPath $savedKernel -Destination $activeKernel }
    }
    $null = Get-VerifiedManifest $state.candidate
    & $tool -Operation Activate -CandidateDirectory $state.candidate -Distribution $state.distribution -AllowShutdown
    foreach ($entry in $state.report.scenarios.Values) {
        if ($entry.result -cne 'PASS') { throw 'Acceptance incomplete; report will not be exported' }
    }
    $state.phase = 'complete'
    Write-ProgressState
    $reportPath = Join-Path $StateDirectory 'acceptance.json'
    Write-AtomicText $reportPath ($state.report | ConvertTo-Json -Depth 24)
    "Maintainer acceptance record: $reportPath. Review and redact before an evidence-only PR; this is not independent attestation."
}

#requires -Version 7.4
[CmdletBinding(SupportsShouldProcess, DefaultParameterSetName = 'Release')]
param(
    [Parameter(ParameterSetName = 'Release')][ValidatePattern('^\d+\.\d+\.\d+$')][string]$Version,
    [Parameter(Mandatory, ParameterSetName = 'Candidate')][string]$CandidateDirectory,
    [ValidatePattern('^[a-z][a-z0-9_-]{0,30}$')][string]$LinuxUser,
    [ValidatePattern('^[A-Za-z][A-Za-z0-9_-]{0,63}$')][string]$Distribution = 'AppSynergy',
    [switch]$AllowShutdown
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$tool = Join-Path $PSScriptRoot 'AppSynergy.ps1'
if (-not $PSCmdlet.ShouldProcess($Distribution, 'Verify and install AppSynergy WSL2')) { return }
# The underlying operations enforce Windows prerequisites and authenticate every
# artifact. Do not duplicate or weaken their signature and promotion checks here.
$null = & $tool -Operation Status
$interactive = -not $LinuxUser
if ($interactive) {
    do { $LinuxUser = Read-Host 'Choose your Linux username (lowercase letters, digits, _ or -)' }
    until ($LinuxUser -cmatch '^[a-z][a-z0-9_-]{0,30}$')
}
$selection = @{}
if ($PSCmdlet.ParameterSetName -eq 'Candidate') {
    $selection.CandidateDirectory = (Resolve-Path -LiteralPath $CandidateDirectory).Path
} else {
    if (-not $Version) {
        $release = & $tool -Operation Check
        $Version = $release.Version -creplace '^appsynergy-wsl-v', ''
        if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw 'No supported release version was returned' }
    }
    & $tool -Operation Stage -Version $Version
    $selection.Version = $Version
}
& $tool -Operation Install @selection -Distribution $Distribution -LinuxUser $LinuxUser
if ($interactive -and -not $AllowShutdown) {
    $answer = Read-Host 'Activate the AppSynergy kernel for ALL your WSL2 distributions and shut down their running sessions now? [y/N]'
    $AllowShutdown = $answer -imatch '^(y|yes)$'
}
if ($AllowShutdown) {
    & $tool -Operation Activate @selection -Distribution $Distribution -AllowShutdown
} else {
    Write-Output 'Installed. AppSynergy kernel activation is still required; it affects ALL WSL2 distributions for this Windows account.'
    Write-Output 'After saving work in every WSL session, use AppSynergy.ps1 -Operation Activate with the same version/candidate and distribution, plus -AllowShutdown.'
}

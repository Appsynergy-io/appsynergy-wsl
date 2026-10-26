#requires -Version 7.4
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot '../.cache/PSScriptAnalyzer/PSScriptAnalyzer.psd1')
$findings = @(Invoke-ScriptAnalyzer -Path (Join-Path $PSScriptRoot '../windows') -Recurse -Severity Warning,Error)
$findings += @(Invoke-ScriptAnalyzer -Path (Join-Path $PSScriptRoot '../tests/windows.ps1') -Severity Warning,Error)
if ($findings.Count -gt 0) {
    $findings | Format-Table -AutoSize
    exit 1
}
& (Join-Path $PSScriptRoot '../tests/windows.ps1')

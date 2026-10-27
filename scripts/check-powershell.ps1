#requires -Version 7.4
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot '../.cache/PSScriptAnalyzer/PSScriptAnalyzer.psd1')
# PSSA 1.25.0 races on shared CommandInfo metadata between parallel rules
# (PowerShell/PSScriptAnalyzer#2206). Run every warning/error rule serially;
# configurable-rule defaults and all diagnostics remain intact. Never retry or
# suppress a rule crash: it must fail the gate.
$rules = @(Get-ScriptAnalyzerRule -Severity Warning,Error | Sort-Object RuleName)
if (-not $rules.Count) { throw 'PSScriptAnalyzer returned no rules' }
$findings = @()
foreach ($rule in $rules) {
    $findings += @(Invoke-ScriptAnalyzer -Path (Join-Path $PSScriptRoot '../windows') -Recurse -Severity Warning,Error -IncludeRule $rule.RuleName)
    $findings += @(Invoke-ScriptAnalyzer -Path (Join-Path $PSScriptRoot '../tests/windows.ps1') -Severity Warning,Error -IncludeRule $rule.RuleName)
}
$findings += @(Invoke-ScriptAnalyzer -Path (Join-Path $PSScriptRoot '../windows/bootstrap.ps1') -Settings @{
    IncludeRules = @('PSUseCompatibleSyntax')
    Rules = @{ PSUseCompatibleSyntax = @{ Enable = $true; TargetVersions = @('5.1', '7.4') } }
})
if ($findings.Count -gt 0) {
    $findings | Format-Table -AutoSize
    exit 1
}
& (Join-Path $PSScriptRoot '../tests/windows.ps1')

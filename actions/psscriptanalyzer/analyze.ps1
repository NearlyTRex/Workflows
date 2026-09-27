# Analyzes every tracked PowerShell file and reports each finding as a GitHub
# annotation. Exits 1 when anything fails the check.
$ErrorActionPreference = 'Stop'

if (-not (Get-Module -ListAvailable PSScriptAnalyzer)) {
    Write-Output '::error::PSScriptAnalyzer is not installed on this runner'
    exit 1
}

$files = @(git ls-files '*.ps1' '*.psm1' '*.psd1')
if ($LASTEXITCODE) { exit $LASTEXITCODE }
if (-not $files) {
    Write-Output 'No PowerShell files to check'
    exit 0
}

# The repo's own settings decide what is reported; without them, only errors,
# since the default rules' style warnings would flood a repo that never opted
# into them. Either way, whatever is reported fails the check.
$settingsFile = 'PSScriptAnalyzerSettings.psd1'
if (Test-Path $settingsFile) {
    $settings = (Resolve-Path $settingsFile).Path
} else {
    $settings = @{ Severity = @('Error', 'ParseError') }
}

function Format-Property([string] $text) {
    $text.Replace('%', '%25').Replace("`r", '%0D').Replace("`n", '%0A').Replace(':', '%3A').Replace(',', '%2C')
}

function Format-Message([string] $text) {
    $text.Replace('%', '%25').Replace("`r", '%0D').Replace("`n", '%0A')
}

$failed = 0
foreach ($file in $files) {
    foreach ($record in Invoke-ScriptAnalyzer -Path $file -Settings $settings) {
        $failed++
        $properties = "file=$(Format-Property $file),line=$($record.Line),col=$($record.Column),title=$(Format-Property $record.RuleName)"
        Write-Output "::error $properties::$(Format-Message $record.Message)"
    }
}

Write-Output "$($files.Count) PowerShell files checked, $failed findings"
if ($failed) { exit 1 }

# Installs the fixture silently and checks its file landed.
$ErrorActionPreference = "Stop"
$installer = Get-ChildItem "tests\fixtures\installer\dist\Fixture-*-Setup.exe" | Select-Object -First 1
$target = Join-Path $env:RUNNER_TEMP "WorkflowsFixture"
$proc = Start-Process $installer.FullName -Wait -PassThru `
    -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/DIR=$target"
if ($proc.ExitCode -ne 0) { throw "installer exited with $($proc.ExitCode)" }
if (-not (Test-Path (Join-Path $target "readme.txt"))) { throw "readme.txt was not installed" }
Write-Host "Fixture installed to $target"

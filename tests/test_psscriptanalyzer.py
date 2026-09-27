"""analyze.ps1, run as the action runs it: pwsh in the repo, with the runner's PSScriptAnalyzer.

GitHub's runner images ship both, so in CI these always run; elsewhere they are
skipped when pwsh isn't installed.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "actions" / "psscriptanalyzer" / "analyze.ps1"

pytestmark = pytest.mark.skipif(shutil.which("pwsh") is None and not os.environ.get("CI"),
                                reason="needs pwsh with PSScriptAnalyzer")


def run(tmp_path, files):
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    (tmp_path / "untracked.ps1").write_text("function Broken {\n")
    result = subprocess.run(["pwsh", "-NoProfile", "-File", str(SCRIPT)], cwd=tmp_path,
                            capture_output=True, text=True)
    return result.returncode, result.stdout


def test_clean_files_pass(tmp_path):
    status, out = run(tmp_path, {"a.ps1": 'Write-Host "ok"\n', "m/b.psm1": "function Get-B { 1 }\n"})
    assert status == 0, out
    assert "2 PowerShell files checked, 0 findings" in out


def test_without_settings_only_errors_fail(tmp_path):
    status, out = run(tmp_path, {
        "style.ps1": 'Write-Host "a warning, not an error"\n',
        "broken.ps1": "function Broken {\n  if ($true) {\n",
        "sub/secret.ps1": '$p = ConvertTo-SecureString "plain" -AsPlainText -Force\n',
    })
    assert status == 1
    assert "::error file=broken.ps1,line=" in out and "title=MissingEndCurlyBrace::" in out
    assert "::error file=sub/secret.ps1,line=1,col=6,title=PSAvoidUsingConvertToSecureStringWithPlainText::" in out
    assert "style.ps1" not in out and "untracked.ps1" not in out


def test_repo_settings_choose_the_rules(tmp_path):
    status, out = run(tmp_path, {
        "PSScriptAnalyzerSettings.psd1": "@{ IncludeRules = @('PSAvoidUsingWriteHost') }\n",
        "style.ps1": 'Write-Host "now an error"\n',
    })
    assert status == 1
    assert "::error file=style.ps1,line=1,col=1,title=PSAvoidUsingWriteHost::" in out


def test_no_powershell_files(tmp_path):
    status, out = run(tmp_path, {"a.sh": "echo hi\n"})
    assert status == 0 and "No PowerShell files to check" in out

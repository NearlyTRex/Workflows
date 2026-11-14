"""entrypoint.sh, with a fake gitleaks on PATH that prints its arguments and exits with FAKE_STATUS."""

import os
import subprocess

import pytest

from script_helpers import ROOT

SCRIPT = ROOT / "actions" / "gitleaks" / "entrypoint.sh"

# The script and the fake gitleaks are shell scripts
pytestmark = [pytest.mark.integration, pytest.mark.requires_binary("sh")]


def run(tmp_path, *args, status=0):
    fake = tmp_path / "bin" / "gitleaks"
    fake.parent.mkdir(exist_ok=True)
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\nexit "$FAKE_STATUS"\n')
    fake.chmod(0o755)
    env = dict(os.environ, PATH=f"{fake.parent}{os.pathsep}{os.environ['PATH']}", FAKE_STATUS=str(status))
    return subprocess.run(["sh", str(SCRIPT), *args], cwd=tmp_path, env=env, capture_output=True, text=True, check=False)


def test_without_a_range_the_whole_history_is_scanned(tmp_path):
    result = run(tmp_path)

    assert result.returncode == 0
    assert result.stdout.splitlines() == ["git", "--redact", "--no-banner", "--exit-code", "1", "."]


def test_a_range_limits_the_scan_to_its_commits(tmp_path):
    result = run(tmp_path, "abc123..def456")

    assert result.returncode == 0
    assert result.stdout.splitlines() == [
        "git", "--redact", "--no-banner", "--exit-code", "1", "--log-opts=abc123..def456", "."]


def test_an_empty_range_scans_the_whole_history(tmp_path):
    result = run(tmp_path, "")

    assert result.stdout.splitlines()[-1] == "."
    assert not any(line.startswith("--log-opts") for line in result.stdout.splitlines())


@pytest.mark.parametrize("args", [(), ("a..b",)])
def test_a_leak_fails_the_scan_with_gitleaks_status(tmp_path, args):
    assert run(tmp_path, *args, status=1).returncode == 1

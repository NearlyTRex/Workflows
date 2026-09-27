import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("scan_range", ROOT / "actions" / "scan-range" / "scan_range.py")
scan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan)

ZERO = "0" * 40


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Three commits; returns their shas, oldest first."""
    monkeypatch.chdir(tmp_path)

    def run(*args):
        return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()

    run("init", "-q")
    shas = []
    for n in range(3):
        (tmp_path / f"f{n}").write_text(str(n))
        run("add", ".")
        run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", f"c{n}")
        shas.append(run("rev-parse", "HEAD"))
    return shas


@pytest.mark.parametrize("event,pr_base,pr_head,before,after,expected", [
    ("pull_request", "a", "b", "", "m", "a..b"),
    ("pull_request", "", "b", "", "m", ""),        # missing base
    ("push", "", "", "a", "b", "a..b"),
    ("push", "", "", ZERO, "b", ""),               # new branch
    ("push", "", "", "", "b", ""),                 # no before at all
    ("push", "", "", "gone", "b", ""),             # force push: before no longer exists
    ("schedule", "", "", "", "b", ""),
    ("workflow_dispatch", "", "", "a", "b", ""),
])
def test_scan_range(event, pr_base, pr_head, before, after, expected):
    assert scan.scan_range(event, pr_base, pr_head, before, after,
                           exists=lambda sha: sha != "gone") == expected


def test_commit_exists(repo):
    assert scan.commit_exists(repo[0])
    assert not scan.commit_exists("1" * 40)


def test_push_writes_the_range(repo, tmp_path, capsys):
    out = tmp_path / "out"
    env = {"EVENT": "push", "BEFORE": repo[0], "AFTER": repo[2], "GITHUB_OUTPUT": str(out)}
    assert scan.main(env) == 0
    assert out.read_text() == f"log-opts={repo[0]}..{repo[2]}\n"
    assert "(2 commits)" in capsys.readouterr().out


def test_unknown_push_scans_everything(repo, tmp_path, capsys):
    out = tmp_path / "out"
    env = {"EVENT": "push", "BEFORE": "1" * 40, "AFTER": repo[2], "GITHUB_OUTPUT": str(out)}
    assert scan.main(env) == 0
    assert out.read_text() == "log-opts=\n"
    assert "whole history (3 commits)" in capsys.readouterr().out

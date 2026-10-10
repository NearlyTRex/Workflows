import datetime
import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("commit_patch", ROOT / "actions" / "commit-patch" / "commit_patch.py")
commit_patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(commit_patch)
spec = importlib.util.spec_from_file_location("diff_paths", ROOT / "actions" / "diff-paths" / "diff_paths.py")
diff_paths = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diff_paths)

TODAY = datetime.date(2026, 10, 10)


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def origin(tmp_path):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "data.csv").write_text("1,a\n")
    git(seed, "init", "-q", "-b", "main")
    git(seed, "add", ".")
    git(seed, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    origin = tmp_path / "origin.git"
    git(tmp_path, "clone", "-q", "--bare", str(seed), str(origin))
    return origin


def make_patch(tmp_path, origin, monkeypatch, content):
    """What the generate job does: change the data in its own clone and diff it."""
    work = tmp_path / "generate"
    git(tmp_path, "clone", "-q", str(origin), str(work))
    (work / "data.csv").write_text(content)
    monkeypatch.chdir(work)
    diff_paths.main({"PATHS": "data.csv", "PATCH": "", "RUNNER_TEMP": str(tmp_path),
                     "GITHUB_OUTPUT": str(tmp_path / "output")})


def commit_clone(tmp_path, origin, monkeypatch):
    """What the commit job does: a fresh clone of the branch."""
    work = tmp_path / "commit"
    git(tmp_path, "clone", "-q", str(origin), str(work))
    monkeypatch.chdir(work)
    return work


def test_patch_is_committed_and_pushed(tmp_path, origin, monkeypatch, capsys):
    make_patch(tmp_path, origin, monkeypatch, "1,a\n2,b\n")
    commit_clone(tmp_path, origin, monkeypatch)
    env = {"PATCH": "", "RUNNER_TEMP": str(tmp_path), "MESSAGE": "Update: {date}", "BRANCH": "main",
           "GITHUB_STEP_SUMMARY": str(tmp_path / "summary")}
    assert commit_patch.main(env, TODAY) == 0
    assert git(origin, "log", "-1", "--format=%s|%an|%ae", "main").strip() == (
        f"Update: 2026-10-10|{commit_patch.BOT_NAME}|{commit_patch.BOT_EMAIL}")
    assert git(origin, "show", "main:data.csv") == "1,a\n2,b\n"
    assert "to main: Update: 2026-10-10" in (tmp_path / "summary").read_text()
    assert "Pushed " in capsys.readouterr().out


def test_branch_that_moved_elsewhere_still_takes_the_patch(tmp_path, origin, monkeypatch):
    make_patch(tmp_path, origin, monkeypatch, "1,a\n2,b\n")
    work = commit_clone(tmp_path, origin, monkeypatch)
    (work / "README.md").write_text("moved\n")
    git(work, "add", "README.md")
    git(work, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "unrelated")
    patch = tmp_path / "changes.patch"
    assert commit_patch.main({"PATCH": str(patch), "MESSAGE": "Update", "BRANCH": "main"}) == 0
    assert git(origin, "show", "main:README.md") == "moved\n"


def test_patch_that_no_longer_applies_fails(tmp_path, origin, monkeypatch):
    make_patch(tmp_path, origin, monkeypatch, "1,a\n2,b\n")
    work = commit_clone(tmp_path, origin, monkeypatch)
    (work / "data.csv").write_text("1,changed\n")
    git(work, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "conflict")
    with pytest.raises(subprocess.CalledProcessError):
        commit_patch.main({"PATCH": "", "RUNNER_TEMP": str(tmp_path), "MESSAGE": "Update", "BRANCH": "main"})
    assert git(origin, "log", "-1", "--format=%s", "main").strip() == "init"

import pytest

from script_helpers import load_script
from git_helpers import git

pytestmark = pytest.mark.requires_binary("git")

diff_paths = load_script("actions/diff-paths/diff_paths.py")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    (repo / "data" / "a.json").write_text("{}\n")
    (repo / "other.txt").write_text("before\n")
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    monkeypatch.chdir(repo)
    return repo


def env_for(tmp_path, paths, patch=""):
    return {"PATHS": paths, "PATCH": patch, "RUNNER_TEMP": str(tmp_path),
            "GITHUB_OUTPUT": str(tmp_path / "output"), "GITHUB_STEP_SUMMARY": str(tmp_path / "summary")}


def test_changes_under_paths_are_written_as_a_patch(repo, tmp_path, capsys):
    (repo / "data" / "a.json").write_text('{"a": 1}\n')
    (repo / "data" / "new.bin").write_bytes(b"\0\1\2")
    (repo / "other.txt").write_text("after\n")
    assert diff_paths.main(env_for(tmp_path, "data\n")) == 0
    assert (tmp_path / "output").read_text() == "changed=true\n"
    patch = (tmp_path / "changes.patch").read_text(errors="replace")
    assert "data/a.json" in patch and "GIT binary patch" in patch and "other.txt" not in patch
    assert "2 files changed" in (tmp_path / "summary").read_text()
    assert "2 files changed" in capsys.readouterr().out


def test_no_changes_writes_no_patch(repo, tmp_path):
    (repo / "other.txt").write_text("after\n")
    patch = tmp_path / "custom.patch"
    env = env_for(tmp_path, "data other-missing-is-fine.txt", str(patch))
    del env["GITHUB_STEP_SUMMARY"]
    (repo / "other-missing-is-fine.txt").write_text("")
    git(repo, "add", "other-missing-is-fine.txt")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "more")
    assert diff_paths.main(env) == 0
    assert (tmp_path / "output").read_text() == "changed=false\n"
    assert not patch.exists()


def test_empty_paths_fail(repo, tmp_path, capsys):
    assert diff_paths.main(env_for(tmp_path, " \n ")) == 1
    assert "::error::paths is empty" in capsys.readouterr().out

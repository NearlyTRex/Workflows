import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("canary", ROOT / "scripts" / "canary.py")
canary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(canary)

SHA = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True,
                     check=True).stdout.strip()
OLD_PIN = "a" * 40
BRANCH = "workflows-canary/" + SHA[:12]


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "test@example.com")
    monkeypatch.setattr(canary.time, "sleep", lambda seconds: None)


def consumer(tmp_path, name="App", files=None, pinned=True):
    """A checkout pinned to an older library commit, with a bare repo as its origin."""
    origin = tmp_path / f"{name}.git"
    git(tmp_path, "init", "--quiet", "--bare", "-b", "main", str(origin))
    repo = tmp_path / name
    for path, text in (files or {"pyproject.toml": '[project]\nname = "x"\nversion = "1.0.0"\n'}).items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    git(repo, "init", "--quiet", "-b", "main")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "Start")
    if pinned:
        canary.init_repo.main([str(repo), "--ref", "HEAD"])
        for workflow in (repo / ".github" / "workflows").glob("*.yml"):
            workflow.write_text(workflow.read_text().replace(f"{SHA} # HEAD", f"{OLD_PIN} # v0.0.1"))
        git(repo, "add", ".")
        git(repo, "commit", "--quiet", "-m", "Workflows")
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "--quiet", "origin", "main")
    return repo


class FakeGh:
    """Records gh calls, and answers them: the runs come from `polls`, one list per poll."""

    def __init__(self, monkeypatch, polls=None, open_pr="", missing_commit=False):
        self.calls = []
        self.polls = polls if polls is not None else [[("CI", "pull_request", "completed", "success"),
                                                       ("Security", "pull_request", "completed", "success"),
                                                       ("Security", "workflow_dispatch", "completed", "success")]]
        self.open_pr = open_pr
        self.missing_commit = missing_commit
        real_run = subprocess.run

        def run(command, **kwargs):
            if command[0] != "gh":
                return real_run(command, **kwargs)
            self.calls.append(command)
            return subprocess.CompletedProcess(command, *self.answer(command, kwargs.get("cwd")), "not found")
        monkeypatch.setattr(canary.subprocess, "run", run)

    def answer(self, command, cwd):
        if command[1] == "api":
            return (1, "") if self.missing_commit else (0, "")
        if command[1:3] == ["pr", "list"]:
            return 0, self.open_pr
        if command[1:3] == ["run", "list"]:
            branch = command[command.index("--branch") + 1]
            commit = git(cwd, "ls-remote", "origin", f"refs/heads/{branch}").split()[0]
            poll = self.polls.pop(0) if len(self.polls) > 1 else self.polls[0]
            runs = [{"workflowName": w, "event": e, "status": s, "conclusion": c, "url": f"https://x/{w}",
                     "headSha": commit} for w, e, s, c in poll]
            runs.append({"workflowName": "CI", "event": "push", "status": "in_progress", "conclusion": "",
                         "url": "https://x/old", "headSha": "0" * 40})
            return 0, json.dumps(runs)
        return 0, ""

    def commands(self, *prefix):
        return [c for c in self.calls if c[1:1 + len(prefix)] == list(prefix)]


def test_pushes_a_canary_and_reports_the_runs(tmp_path, monkeypatch, capsys):
    repo = consumer(tmp_path)
    before = {p: p.read_text() for p in (repo / ".github" / "workflows").glob("*.yml")}
    gh = FakeGh(monkeypatch)

    assert canary.main(["HEAD", str(repo)]) == 0

    git(repo, "fetch", "--quiet", "origin", f"{BRANCH}:refs/remotes/origin/{BRANCH}")
    pushed = git(repo, "show", f"origin/{BRANCH}:.github/workflows/ci.yml")
    assert f"@{SHA} # HEAD" in pushed and OLD_PIN not in pushed
    assert git(repo, "log", "-1", "--format=%s", f"origin/{BRANCH}") == f"Workflows canary: HEAD ({SHA[:12]})"
    # The checkout itself is untouched, and the temporary worktree is gone.
    assert {p: p.read_text() for p in before} == before
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert len(git(repo, "worktree", "list").splitlines()) == 1

    create = gh.commands("pr", "create")[0]
    assert create[create.index("--base") + 1] == "main" and "--draft" in create
    assert f"NearlyTRex/Workflows@{SHA}" in create[create.index("--body") + 1]
    assert gh.commands("workflow", "run") == [["gh", "workflow", "run", "security.yml", "--ref", BRANCH]]
    assert gh.commands("api") == [["gh", "api", f"repos/NearlyTRex/Workflows/commits/{SHA}", "--silent"]]

    out = capsys.readouterr().out
    assert f"App: pushed {BRANCH} with a draft pull request, and a full Security run" in out
    assert "ok   Security (workflow_dispatch): success  https://x/Security" in out
    assert "old" not in out
    assert f"--cleanup {repo}" in out


def test_a_second_run_reuses_the_branch_and_pull_request(tmp_path, monkeypatch):
    repo = consumer(tmp_path)
    FakeGh(monkeypatch)
    assert canary.main(["HEAD", str(repo), "--no-wait"]) == 0
    gh = FakeGh(monkeypatch, open_pr="https://github.com/o/App/pull/3")
    assert canary.main(["HEAD", str(repo), "--no-wait"]) == 0
    assert gh.commands("pr", "create") == [] and gh.commands("run", "list") == []


def test_failing_runs_fail_the_canary(tmp_path, monkeypatch, capsys):
    repo = consumer(tmp_path)
    FakeGh(monkeypatch, polls=[[("CI", "pull_request", "completed", "failure"),
                                ("Security", "workflow_dispatch", "completed", "success")]])
    assert canary.main(["HEAD", str(repo)]) == 1
    assert "FAIL CI (pull_request): failure" in capsys.readouterr().out


def test_waits_until_every_run_has_started_and_finished(tmp_path, monkeypatch):
    repo = consumer(tmp_path)
    gh = FakeGh(monkeypatch, polls=[
        [],
        [("CI", "pull_request", "completed", "success")],     # the manual Security run isn't listed yet
        [("CI", "pull_request", "completed", "success"), ("Security", "workflow_dispatch", "in_progress", "")],
        [("CI", "pull_request", "completed", "success"), ("Security", "workflow_dispatch", "completed", "success")],
    ])
    assert canary.main(["HEAD", str(repo)]) == 0
    assert len(gh.commands("run", "list")) == 4


def test_gives_up_after_the_timeout(tmp_path, monkeypatch, capsys):
    repo = consumer(tmp_path)
    FakeGh(monkeypatch, polls=[[("CI", "pull_request", "in_progress", "")]])
    clock = iter(range(0, 10 ** 6, 600))
    monkeypatch.setattr(canary.time, "monotonic", lambda: next(clock))
    assert canary.main(["HEAD", str(repo), "--timeout", "30"]) == 1
    assert "App's runs didn't finish in time" in capsys.readouterr().err


def test_repos_without_a_manual_security_run(tmp_path, monkeypatch, capsys):
    repo = consumer(tmp_path)
    security = repo / ".github" / "workflows" / "security.yml"
    security.write_text(security.read_text().replace("  workflow_dispatch:\n", ""))
    git(repo, "commit", "--quiet", "-am", "No manual runs")
    git(repo, "push", "--quiet", "origin", "main")
    gh = FakeGh(monkeypatch, polls=[[("CI", "pull_request", "completed", "success")]])
    assert canary.main(["HEAD", str(repo)]) == 0
    assert gh.commands("workflow", "run") == []
    assert "a draft pull request\n" in capsys.readouterr().out


def test_one_repo_failing_to_push_does_not_stop_the_others(tmp_path, monkeypatch, capsys):
    unpinned = consumer(tmp_path, "Unpinned", {"a.txt": "x"}, pinned=False)
    (tmp_path / "Plain").mkdir()
    repo = consumer(tmp_path)
    FakeGh(monkeypatch)
    assert canary.main(["HEAD", str(unpinned), str(tmp_path / "Plain"), str(repo)]) == 1
    captured = capsys.readouterr()
    assert "Unpinned: error: Unpinned has no library pins to move to HEAD" in captured.err
    assert "Plain: error: " in captured.err and "Plain is not a git checkout" in captured.err
    assert "ok   CI (pull_request)" in captured.out
    assert len(git(unpinned, "worktree", "list").splitlines()) == 1


def test_repos_are_found_next_to_the_library(monkeypatch):
    assert canary.repo_path(ROOT.name) == ROOT
    with pytest.raises(canary.CanaryError, match="not a git checkout"):
        canary.repo_path("no-such-repo-anywhere")


def test_an_origin_without_a_default_branch(tmp_path, monkeypatch, capsys):
    repo = consumer(tmp_path)
    git(tmp_path / "App.git", "symbolic-ref", "HEAD", "refs/heads/nothing-here")
    FakeGh(monkeypatch)
    assert canary.main(["HEAD", str(repo)]) == 1
    assert "can't tell App's default branch" in capsys.readouterr().err


def test_the_ref_must_exist_and_be_pushed(tmp_path, monkeypatch, capsys):
    repo = consumer(tmp_path)
    FakeGh(monkeypatch)
    assert canary.main(["no-such-ref", str(repo)]) == 1
    assert "no-such-ref is not a commit" in capsys.readouterr().err
    FakeGh(monkeypatch, missing_commit=True)
    assert canary.main(["HEAD", str(repo)]) == 1
    assert "is not on GitHub yet; push it first" in capsys.readouterr().err


def test_a_git_failure_is_reported(tmp_path, monkeypatch, capsys):
    repo = consumer(tmp_path)
    FakeGh(monkeypatch)
    (tmp_path / "App.git" / "hooks" / "pre-receive").write_text("#!/bin/sh\necho refused >&2\nexit 1\n")
    (tmp_path / "App.git" / "hooks" / "pre-receive").chmod(0o755)
    assert canary.main(["HEAD", str(repo), "--no-wait"]) == 1
    assert "git push" in capsys.readouterr().err
    assert len(git(repo, "worktree", "list").splitlines()) == 1


def test_cleanup_deletes_the_canary_branches(tmp_path, monkeypatch, capsys):
    repo = consumer(tmp_path)
    FakeGh(monkeypatch)
    canary.main(["HEAD", str(repo), "--no-wait"])
    git(repo, "push", "--quiet", "origin", "main:refs/heads/workflows-canary/older")
    git(repo, "push", "--quiet", "origin", "main:refs/heads/feature")
    capsys.readouterr()

    assert canary.main(["--cleanup", str(repo)]) == 0
    assert f"deleted {BRANCH}, workflows-canary/older" in capsys.readouterr().out
    assert git(repo, "ls-remote", "--heads", "origin").count("refs/heads/") == 2

    assert canary.main(["--cleanup", str(repo)]) == 0
    assert "no canary branches" in capsys.readouterr().out
    assert canary.main(["--cleanup", str(tmp_path / "missing")]) == 1


def test_needs_a_repo():
    with pytest.raises(SystemExit):
        canary.main(["HEAD"])
    with pytest.raises(SystemExit):
        canary.main(["--cleanup"])

"""open_pr.py, with a fake gh on PATH that logs its calls."""

import json
import os
import sys

import pytest

from script_helpers import load_script

open_pr = load_script("actions/open-pr/open_pr.py")

# Logs its arguments as one JSON line. pr view answers FAKE_PR_URL, or fails when it is unset;
# pr create prints the new pull request's URL.
FAKE = """\
import json, os, sys
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1:3] == ["pr", "view"]:
    url = os.environ.get("FAKE_PR_URL")
    if url is None:
        sys.exit(1)
    print(url)
if sys.argv[1:3] == ["pr", "create"]:
    print("https://github.com/owner/repo/pull/9")
"""


@pytest.fixture
def gh(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    tool = bin_dir / "gh"
    tool.write_text(f"#!{sys.executable}\n{FAKE}")
    tool.chmod(0o755)
    log = tmp_path / "log"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.delenv("FAKE_PR_URL", raising=False)
    return lambda: [json.loads(line) for line in log.read_text().splitlines()]


def env_for(tmp_path, **extra):
    return {"HEAD_BRANCH": "scheduled-update/main", "BASE": "main", "TITLE": "Update: 2026-10-10",
            "BODY": "Body", "GITHUB_OUTPUT": str(tmp_path / "output"), **extra}


def test_opens_a_pull_request_with_assignees(tmp_path, gh, capsys):
    assert open_pr.main(env_for(tmp_path, ASSIGNEES="alice, bob\ncarol")) == 0
    assert gh()[-1] == ["pr", "create", "--base", "main", "--head", "scheduled-update/main",
                        "--title", "Update: 2026-10-10", "--body", "Body",
                        "--assignee", "alice", "--assignee", "bob", "--assignee", "carol"]
    assert (tmp_path / "output").read_text() == "url=https://github.com/owner/repo/pull/9\n"
    assert "Opened https://github.com/owner/repo/pull/9" in capsys.readouterr().out


def test_a_closed_pull_request_gets_a_new_one(tmp_path, gh, monkeypatch):
    # gh prints nothing for a closed pull request, since the jq filter drops it.
    monkeypatch.setenv("FAKE_PR_URL", "")
    env = env_for(tmp_path)
    del env["GITHUB_OUTPUT"]
    assert open_pr.main(env) == 0
    assert gh()[-1][:2] == ["pr", "create"] and "--assignee" not in gh()[-1]


def test_an_open_pull_request_is_updated(tmp_path, gh, monkeypatch, capsys):
    url = "https://github.com/owner/repo/pull/7"
    monkeypatch.setenv("FAKE_PR_URL", url)
    assert open_pr.main(env_for(tmp_path, ASSIGNEES="alice")) == 0
    assert gh()[-1] == ["pr", "edit", url, "--title", "Update: 2026-10-10", "--body", "Body"]
    assert (tmp_path / "output").read_text() == f"url={url}\n"
    assert f"Updated the open pull request: {url}" in capsys.readouterr().out

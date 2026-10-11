"""tag_exists.py and release_pr.py, with fake gh and git on PATH that log their calls."""

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tag_exists = load("tag_exists", ROOT / "actions" / "tag-exists" / "tag_exists.py")
release_pr = load("release_pr", ROOT / "actions" / "release-pr" / "release_pr.py")

# Logs its arguments as one JSON line, then answers from the FAKE_* variables.
FAKE = """\
import json, os, sys
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps([os.path.basename(sys.argv[0])] + sys.argv[1:]) + "\\n")
if sys.argv[1] == "api":
    sys.exit(0 if sys.argv[2] == f"repos/owner/repo/git/ref/tags/{os.environ.get('FAKE_TAG')}" else 1)
if sys.argv[1:3] == ["pr", "view"]:
    url = os.environ.get("FAKE_PR_URL")
    if url is None:
        sys.exit(1)
    print(url)
"""


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("gh", "git"):
        tool = bin_dir / name
        tool.write_text(f"#!{sys.executable}\n{FAKE}")
        tool.chmod(0o755)
    log = tmp_path / "log"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.delenv("FAKE_TAG", raising=False)
    monkeypatch.delenv("FAKE_PR_URL", raising=False)
    return lambda: [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


@pytest.mark.parametrize("existing,expected,message", [
    ("v1.2.0", "true", "v1.2.0 already exists"),
    ("v1.1.0", "false", "v1.2.0 does not exist yet"),
])
def test_tag_exists(tmp_path, monkeypatch, capsys, fakes, existing, expected, message):
    monkeypatch.setenv("FAKE_TAG", existing)
    output = tmp_path / "output"
    assert tag_exists.main({"TAG": "v1.2.0", "REPO": "owner/repo", "GITHUB_OUTPUT": str(output)}) == 0
    assert output.read_text() == f"exists={expected}\n"
    assert message in capsys.readouterr().out
    assert fakes() == [["gh", "api", "repos/owner/repo/git/ref/tags/v1.2.0", "--silent"]]


def test_tag_exists_reads_the_process_environment(tmp_path, monkeypatch, fakes):
    output = tmp_path / "output"
    monkeypatch.setenv("TAG", "v1.0.0")
    monkeypatch.setenv("REPO", "owner/repo")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    assert tag_exists.main() == 0
    assert output.read_text() == "exists=false\n"


RELEASE = {"TAG": "v1.2.0", "VERSION": "1.2.0", "VERSION_FILE": "VERSION", "BASE": "main", "REPO": "owner/repo"}
COMMITS = [
    ["git", "config", "user.name", "github-actions[bot]"],
    ["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"],
    ["git", "switch", "-c", "release/v1.2.0"],
    ["git", "commit", "-m", "Release v1.2.0", "--", "VERSION"],
    ["git", "push", "--force", "origin", "release/v1.2.0"],
]
VIEW = ["gh", "pr", "view", "release/v1.2.0", "--json", "url,state", "--jq", 'select(.state == "OPEN") | .url']


def test_opens_a_pull_request(fakes, capsys):
    assert release_pr.main(RELEASE) == 0
    calls = fakes()
    assert calls[1:6] == COMMITS
    assert calls[6] == VIEW
    assert calls[7] == ["gh", "pr", "create", "--base", "main", "--head", "release/v1.2.0", "--title", "Release v1.2.0",
                        "--body", ("Sets the version to 1.2.0 in `VERSION`. Merging this tags v1.2.0 and publishes "
                                   "the release.")]


@pytest.mark.parametrize("url", ["https://github.com/owner/repo/pull/7", ""])
def test_an_open_pull_request_is_updated_not_reopened(fakes, monkeypatch, capsys, url):
    # gh prints nothing when the branch's pull request is closed, since the jq filter drops it.
    monkeypatch.setenv("FAKE_PR_URL", url)
    assert release_pr.main(RELEASE) == 0
    calls = fakes()
    if url:
        assert calls[-2] == VIEW
        assert calls[-1][:4] == ["gh", "pr", "edit", url] and calls[-1][4:6] == ["--title", "Release v1.2.0"]
        assert f"Updated the open pull request: {url}" in capsys.readouterr().out
    else:
        assert calls[-1][:3] == ["gh", "pr", "create"]


def test_an_existing_tag_stops_the_release(fakes, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_TAG", "v1.2.0")
    assert release_pr.main(RELEASE) == 1
    assert "::error::Tag v1.2.0 already exists" in capsys.readouterr().out
    assert [call[0] for call in fakes()] == ["gh"]


def test_release_pr_reads_the_process_environment(fakes, monkeypatch):
    for name, value in RELEASE.items():
        monkeypatch.setenv(name, value)
    assert release_pr.main() == 0

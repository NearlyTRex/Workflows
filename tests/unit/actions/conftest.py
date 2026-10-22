"""A fake gh and git on PATH, shared by the release actions' tests."""

import json
import os
import sys

import pytest

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

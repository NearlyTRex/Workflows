"""Scratch git repositories for the tests of scripts that read what git tracks."""

import subprocess


def git(cwd, *args):
    """Run git in cwd, failing the test on an error, and return what it printed."""
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def repo_with(root, files):
    """A repository at root tracking files, {path: text or bytes}."""
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)

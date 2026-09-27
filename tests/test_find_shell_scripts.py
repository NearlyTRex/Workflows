import importlib.util
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "find_shell_scripts", ROOT / "actions" / "shellcheck" / "find_shell_scripts.py")
find = importlib.util.module_from_spec(spec)
spec.loader.exec_module(find)

FILES = {
    "build.sh": "echo hi\n",                      # by extension, no shebang
    "lib.bash": "f() { :; }\n",
    "hooks/pre-commit": "#!/bin/sh\nexit 0\n",
    "entrypoint": "#!/usr/bin/env bash\n",
    "strict": "#!/bin/bash -e\n",
    "dash-script": "#!/bin/dash\n",
    "run.sh.in": "#!/usr/bin/env bash\n@X@\n",     # a template, by shebang
    "zsh-script": "#!/usr/bin/env zsh\n",         # shellcheck can't read zsh
    "fish-script": "#!/usr/bin/fish\n",
    "tool.py": "#!/usr/bin/env python3\n",
    "bashful.txt": "#!/usr/bin/bashful\n",        # not a shell, just starts like one
    "notes.md": "sh is mentioned here\n",
    "data.bin": "\x00\x01\x02",
}


def repo_with(tmp_path, files):
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)


def test_finds_scripts_by_extension_and_shebang(tmp_path, monkeypatch, capsys):
    repo_with(tmp_path, FILES)
    (tmp_path / "untracked.sh").write_text("")
    monkeypatch.chdir(tmp_path)
    assert find.main() == 0
    out, err = capsys.readouterr()
    assert out.split("\0")[:-1] == ["build.sh", "dash-script", "entrypoint", "hooks/pre-commit",
                                    "lib.bash", "run.sh.in", "strict"]
    assert "7 shell scripts found" in err


def test_unreadable_paths_are_skipped(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.symlink("does-not-exist", "dangling")
    (tmp_path / "a-directory").mkdir()
    assert not find.is_shell_script("dangling")
    assert not find.is_shell_script("a-directory")

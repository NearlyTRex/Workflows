import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("check_format", ROOT / "actions" / "clang-format" / "check_format.py")
check_format = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_format)

# Installed from requirements-dev.txt next to the interpreter running the tests.
CLANG_FORMAT = str(Path(sys.executable).parent / "clang-format")
FORMATTED = "int main() { return 0; }\n"


def repo_with(tmp_path, files):
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)


def test_formatted_sources_pass(tmp_path, monkeypatch, capsys):
    repo_with(tmp_path, {".clang-format": "BasedOnStyle: LLVM\n", "src/a.cpp": FORMATTED,
                         "include/a.hpp": "#pragma once\n", "notes.txt": "int  x ;"})
    monkeypatch.chdir(tmp_path)
    assert check_format.main([CLANG_FORMAT]) == 0
    assert "2 files checked, 0 not formatted" in capsys.readouterr().out


def test_unformatted_sources_are_annotated(tmp_path, monkeypatch, capsys):
    repo_with(tmp_path, {".clang-format": "BasedOnStyle: LLVM\n", "ok.c": FORMATTED,
                         "src/bad.cpp": "int main(){return 0;}\n", "bad.h": "int  x ;\n",
                         "vendor/lib.cpp": "int  y ;\n", ".clang-format-ignore": "vendor/*\n"})
    (tmp_path / "untracked.cpp").write_text("int  z ;\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(check_format, "BATCH", 2)
    assert check_format.main([CLANG_FORMAT]) == 1
    out = capsys.readouterr().out
    assert "::error file=src/bad.cpp,line=1," in out and "::error file=bad.h,line=1," in out
    assert "vendor/lib.cpp" not in out.split("files checked")[1] and "untracked.cpp" not in out
    assert "4 files checked, 2 not formatted" in out
    assert "Fix with: clang-format -i bad.h src/bad.cpp" in out


def test_other_failures_are_shown_as_is(tmp_path, monkeypatch, capsys):
    repo_with(tmp_path, {".clang-format": "NoSuchOption: true\n", "a.cpp": FORMATTED})
    monkeypatch.chdir(tmp_path)
    assert check_format.main([CLANG_FORMAT]) == 1
    out = capsys.readouterr().out
    assert "NoSuchOption" in out and "::error" not in out
    assert "1 files checked, 0 not formatted" in out

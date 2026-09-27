import importlib.util
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("check_toml", ROOT / "actions" / "check-toml" / "check_toml.py")
check_toml = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_toml)


def repo_with(tmp_path, files):
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)


def test_valid_files_pass(tmp_path, monkeypatch, capsys):
    repo_with(tmp_path, {"pyproject.toml": b'[project]\nname = "x"\n', "d/b.toml": b"a = [1, 2]\n",
                         "c.txt": b"not toml ="})
    monkeypatch.chdir(tmp_path)
    assert check_toml.main() == 0
    assert "2 TOML files checked, 0 invalid" in capsys.readouterr().out


def test_invalid_and_undecodable_files_fail(tmp_path, monkeypatch, capsys):
    repo_with(tmp_path, {"ok.toml": b"", "bad.toml": b"a = \n", "dup.toml": b"a = 1\na = 2\n",
                         "latin.toml": b'a = "\xe9"\n'})
    (tmp_path / "untracked.toml").write_text("=")
    monkeypatch.chdir(tmp_path)
    assert check_toml.main() == 1
    out = capsys.readouterr().out
    assert "::error file=bad.toml::" in out and "::error file=dup.toml::" in out
    assert "::error file=latin.toml::" in out
    assert "untracked.toml" not in out
    assert "4 TOML files checked, 3 invalid" in out

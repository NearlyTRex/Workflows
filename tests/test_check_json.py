import importlib.util
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("check_json", ROOT / "actions" / "check-json" / "check_json.py")
check_json = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_json)


def repo_with(tmp_path, files):
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)


def test_valid_files_pass(tmp_path, monkeypatch, capsys):
    repo_with(tmp_path, {"a.json": b'{"a": 1}', "d/b.json": b"[1, 2]", "c.txt": b"not json"})
    monkeypatch.chdir(tmp_path)
    assert check_json.main() == 0
    assert "2 JSON files checked, 0 invalid" in capsys.readouterr().out


def test_invalid_and_undecodable_files_fail(tmp_path, monkeypatch, capsys):
    repo_with(tmp_path, {"ok.json": b"{}", "bad.json": b'{"a": }', "latin.json": b'{"a": "\xe9"}'})
    (tmp_path / "untracked.json").write_text("{")
    monkeypatch.chdir(tmp_path)
    assert check_json.main() == 1
    out = capsys.readouterr().out
    assert "::error file=bad.json::" in out and "::error file=latin.json::" in out
    assert "untracked.json" not in out
    assert "3 JSON files checked, 2 invalid" in out

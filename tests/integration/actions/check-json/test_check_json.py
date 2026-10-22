import pytest

from script_helpers import load_script
from git_helpers import repo_with

pytestmark = pytest.mark.requires_binary("git")

check_json = load_script("actions/check-json/check_json.py")


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

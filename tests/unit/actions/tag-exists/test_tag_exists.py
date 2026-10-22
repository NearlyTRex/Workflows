"""tag_exists.py, with the fake gh from conftest.py answering for the tags."""

import pytest

from script_helpers import load_script

tag_exists = load_script("actions/tag-exists/tag_exists.py")


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

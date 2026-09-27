import importlib.util
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "check_tracked_files", ROOT / "actions" / "check-tracked-files" / "check_tracked_files.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def repo_with(tmp_path, names):
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-f", "."], cwd=tmp_path, check=True)


def test_clean_repo_passes(tmp_path, monkeypatch, capsys):
    repo_with(tmp_path, ["README.md", "docker/.env.example", "src/keys.py", "certs/ca.pem.template"])
    monkeypatch.chdir(tmp_path)
    assert check.main([]) == 0
    assert "No credential-bearing files" in capsys.readouterr().out


def test_default_patterns_fail(tmp_path, monkeypatch, capsys):
    repo_with(tmp_path, [".env", "docker/.env.local", "prod.env", "tls/server.key", "ssh/id_ed25519", "ok.txt"])
    monkeypatch.chdir(tmp_path)
    assert check.main([]) == 1
    out = capsys.readouterr().out
    for name in [".env", "docker/.env.local", "prod.env", "tls/server.key", "ssh/id_ed25519"]:
        assert f"::error file={name}::" in out
    assert "ok.txt" not in out


def test_extra_patterns_and_exceptions(tmp_path, monkeypatch, capsys):
    repo_with(tmp_path, ["data/app.db", "data/.gitkeep", "data/notes.txt", "tests/fixture.db",
                         "tests/certs/test.pem"])
    monkeypatch.chdir(tmp_path)
    assert check.main(["*.db", "data/*", "!data/.gitkeep", "!tests/*"]) == 1
    out = capsys.readouterr().out
    assert "::error file=data/app.db::" in out
    assert "::error file=data/notes.txt::" in out
    assert "data/.gitkeep" not in out
    assert "tests/" not in out


def test_name_patterns_do_not_match_directories():
    assert check.forbidden(["keys/readme.md", "a.key/readme.md"], []) == []

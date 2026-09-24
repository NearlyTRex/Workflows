import importlib.util
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("init_repo", ROOT / "scripts" / "init_repo.py")
init_repo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(init_repo)

SHA = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True,
                     check=True).stdout.strip()
PIN = re.compile(r"^[\w.-]+/[\w.-]+/\.github/workflows/[\w-]+\.yml@[0-9a-f]{40}$")


def make_repo(tmp_path, files):
    repo = tmp_path / "repo"
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    return repo


def load(repo, name):
    return yaml.safe_load((repo / name).read_text())


def all_uses(workflow):
    return [job["uses"] for job in workflow["jobs"].values() if "uses" in job]


PYTHON = {
    "pyproject.toml": '[project]\nname = "x"\nversion = "1.0.0"\n',
    "x/__init__.py": "",
    "requirements.txt": "a==1 \\\n    --hash=sha256:00\n",
    "docker/Dockerfile": "FROM scratch\n",
}


def test_python_repo(tmp_path):
    repo = make_repo(tmp_path, PYTHON)
    assert init_repo.main([str(repo), "--ref", "HEAD"]) == 0

    ci = load(repo, ".github/workflows/ci.yml")
    assert set(ci["jobs"]) == {"python", "lint"}
    security = load(repo, ".github/workflows/security.yml")
    assert set(security["jobs"]) == {"security", "codeql", "dependency-review", "container-scan"}
    assert security["jobs"]["security"]["with"]["pip-audit-files"] == "requirements.txt"
    assert security["jobs"]["codeql"]["with"]["languages"] == '["python", "actions"]'
    assert security["jobs"]["container-scan"]["with"] == {"dockerfile": "docker/Dockerfile", "context": "docker"}

    release = load(repo, ".github/workflows/release.yml")
    assert release["jobs"]["release"]["needs"] == "check"
    assert release["jobs"]["release"]["with"] == {"version-file": "pyproject.toml"}
    prepare = load(repo, ".github/workflows/prepare-release.yml")
    assert prepare["jobs"]["prepare"]["with"]["version-file"] == "pyproject.toml"

    dependabot = load(repo, ".github/dependabot.yml")
    ecosystems = {u["package-ecosystem"]: u["directories"] for u in dependabot["updates"]}
    assert ecosystems == {"github-actions": ["/"], "pip": ["/"], "docker": ["/docker"]}
    assert all(u["cooldown"] == {"default-days": 7} for u in dependabot["updates"])


def test_every_library_reference_is_pinned_with_a_label(tmp_path):
    repo = make_repo(tmp_path, PYTHON)
    init_repo.main([str(repo), "--ref", "HEAD"])
    for name in ["ci", "security", "release", "prepare-release"]:
        path = repo / f".github/workflows/{name}.yml"
        workflow = yaml.safe_load(path.read_text())
        assert workflow["permissions"] == {}
        for uses in all_uses(workflow):
            assert PIN.match(uses), uses
            assert uses.endswith(f"@{SHA}")
        assert all(line.rstrip().endswith("# HEAD") for line in path.read_text().splitlines() if "uses:" in line)


@pytest.mark.parametrize("files,stack,languages,release_file", [
    ({"CMakeLists.txt": "project(T VERSION 2.1.0 LANGUAGES CXX)\n", "main.cpp": ""}, "cpp",
     '["c-cpp", "actions"]', "CMakeLists.txt"),
    ({"src/a.cpp": "", "src/a.h": "", "Setup.py": ""}, "cpp", '["c-cpp", "actions"]', None),
    ({"a.json": "{}", "b.json": "{}", "c.json": "{}", "README.md": ""}, "data", '["actions"]', None),
    ({"build.sh": "#!/bin/sh\n", "VERSION": "0.3.0\n"}, "shell", '["actions"]', "VERSION"),
])
def test_stack_detection(tmp_path, files, stack, languages, release_file):
    repo = make_repo(tmp_path, files)
    target, detected, version_file, outputs, _ = init_repo.plan(repo, ref="HEAD")
    assert detected == stack
    assert version_file == release_file
    security = yaml.safe_load(outputs[".github/workflows/security.yml"])
    assert security["jobs"]["codeql"]["with"]["languages"] == languages
    assert set(yaml.safe_load(outputs[".github/workflows/ci.yml"])["jobs"]) == {"lint"}
    assert (".github/workflows/release.yml" in outputs) == (release_file is not None)


def test_release_checks_run_lint_outside_python(tmp_path):
    repo = make_repo(tmp_path, {"build.sh": "", "VERSION": "1.0.0\n"})
    _, _, _, outputs, _ = init_repo.plan(repo, ref="HEAD")
    release = yaml.safe_load(outputs[".github/workflows/release.yml"])
    assert "/lint.yml@" in release["jobs"]["check"]["uses"]


def test_submodules_and_npm_get_dependabot(tmp_path):
    repo = make_repo(tmp_path, {".gitmodules": "", "package.json": '{"version": "1.0.0"}', "a.sh": ""})
    _, _, _, outputs, _ = init_repo.plan(repo, ref="HEAD")
    ecosystems = {u["package-ecosystem"] for u in yaml.safe_load(outputs[".github/dependabot.yml"])["updates"]}
    assert ecosystems == {"github-actions", "npm", "gitsubmodule"}


def test_existing_files_are_kept(tmp_path, capsys):
    repo = make_repo(tmp_path, PYTHON)
    (repo / ".github/workflows").mkdir(parents=True)
    (repo / ".github/workflows/ci.yml").write_text("mine\n")
    (repo / ".github/workflows/build.yml").write_text("mine\n")
    assert init_repo.main([str(repo), "--ref", "HEAD"]) == 1
    assert (repo / ".github/workflows/ci.yml").read_text() == "mine\n"
    assert not (repo / ".github/dependabot.yml").exists()
    assert init_repo.main([str(repo), "--ref", "HEAD", "--force"]) == 0
    assert (repo / ".github/workflows/ci.yml").read_text() != "mine\n"
    assert "check for overlap: build.yml" in capsys.readouterr().out


def test_dry_run_writes_nothing(tmp_path):
    repo = make_repo(tmp_path, PYTHON)
    assert init_repo.main([str(repo), "--ref", "HEAD", "--dry-run"]) == 0
    assert not (repo / ".github").exists()


def test_no_release_and_explicit_version_file(tmp_path):
    repo = make_repo(tmp_path, {**PYTHON, "VERSION": "3.0.0\n"})
    _, _, _, outputs, _ = init_repo.plan(repo, release=False, ref="HEAD")
    assert ".github/workflows/release.yml" not in outputs
    _, _, version_file, _, _ = init_repo.plan(repo, version_file="VERSION", ref="HEAD")
    assert version_file == "VERSION"


def test_bad_version_file_is_an_error(tmp_path):
    repo = make_repo(tmp_path, PYTHON)
    assert init_repo.main([str(repo), "--ref", "HEAD", "--version-file", "x/__init__.py"]) == 1


def test_unknown_ref_is_an_error(tmp_path):
    repo = make_repo(tmp_path, PYTHON)
    assert init_repo.main([str(repo), "--ref", "no-such-ref"]) == 1


def test_locked_python_repos_only_update_lock_files(tmp_path):
    repo = make_repo(tmp_path, PYTHON)
    _, _, _, outputs, _ = init_repo.plan(repo, ref="HEAD")
    pip = [u for u in yaml.safe_load(outputs[".github/dependabot.yml"])["updates"] if u["package-ecosystem"] == "pip"]
    assert pip[0]["versioning-strategy"] == "lockfile-only"

    unlocked = make_repo(tmp_path / "unlocked", {"pyproject.toml": '[project]\nname = "x"\nversion = "1.0.0"\n'})
    _, _, _, outputs, _ = init_repo.plan(unlocked, ref="HEAD")
    pip = [u for u in yaml.safe_load(outputs[".github/dependabot.yml"])["updates"] if u["package-ecosystem"] == "pip"]
    assert "versioning-strategy" not in pip[0]


def test_prints_required_repo_settings(tmp_path, capsys):
    repo = make_repo(tmp_path, PYTHON)
    init_repo.main([str(repo), "--ref", "HEAD"])
    out = capsys.readouterr().out
    assert "Dependency graph" in out and "create and approve pull requests" in out


def test_warns_about_locks_dependabot_cannot_update(tmp_path, capsys):
    header = "#\n# by the following command:\n#\n#    pip-compile --generate-hashes --output-file={} {}\n#\n"
    body = "a==1 \\\n    --hash=sha256:00\n"
    repo = make_repo(tmp_path, {
        **PYTHON,
        "requirements.txt": header.format("requirements.txt", "pyproject.toml") + body,
        "requirements-dev.txt": header.format("requirements-dev.txt", "requirements-dev.in") + body,
        "tools.lock": header.format("tools.lock", "tools.in") + body,
    })
    _, _, _, _, warnings = init_repo.plan(repo, ref="HEAD")
    assert len(warnings) == 2
    assert warnings[0].startswith("requirements.txt: compiled from pyproject.toml")
    assert warnings[1].startswith("tools.lock: Dependabot never updates *.lock files")
    init_repo.main([str(repo), "--ref", "HEAD"])
    assert "warning: tools.lock" in capsys.readouterr().out


@pytest.mark.parametrize("url,expected", [
    ("https://github.com/Someone/Lib.git", "Someone/Lib"),
    ("git@github.com:Someone/Lib.git", "Someone/Lib"),
    ("https://gitlab.com/Someone/Lib.git", init_repo.DEFAULT_LIBRARY),
])
def test_library_name(monkeypatch, url, expected):
    monkeypatch.setattr(init_repo, "git", lambda repo, *args: url)
    assert init_repo.library_name() == expected


def test_library_name_without_remote(monkeypatch):
    def fail(repo, *args):
        raise init_repo.InitError("no remote")
    monkeypatch.setattr(init_repo, "git", fail)
    assert init_repo.library_name() == init_repo.DEFAULT_LIBRARY


def fake_git(tags, fetch_fails=False):
    calls = []

    def git(repo, *args):
        calls.append(args[0])
        if args[0] == "fetch" and fetch_fails:
            raise init_repo.InitError("offline")
        return {"fetch": "", "tag": "\n".join(tags), "rev-list": "a" * 40}[args[0]]
    return git, calls


def test_highest_tag_is_the_default_ref_after_fetching(monkeypatch):
    git, calls = fake_git(["v0.10.0", "v0.9.0", "v0.2.0"])
    monkeypatch.setattr(init_repo, "git", git)
    assert init_repo.resolve_ref(None) == ("a" * 40, "v0.10.0")
    assert calls[0] == "fetch"


def test_offline_falls_back_to_local_tags(monkeypatch, capsys):
    git, _ = fake_git(["v0.1.0"], fetch_fails=True)
    monkeypatch.setattr(init_repo, "git", git)
    assert init_repo.resolve_ref(None) == ("a" * 40, "v0.1.0")
    assert "could not fetch tags" in capsys.readouterr().err


def test_untagged_library_needs_a_ref(monkeypatch):
    git, _ = fake_git([])
    monkeypatch.setattr(init_repo, "git", git)
    with pytest.raises(init_repo.InitError, match="no release tag yet"):
        init_repo.resolve_ref(None)


def test_works_outside_git(tmp_path):
    repo = tmp_path / "plain"
    (repo / "x").mkdir(parents=True)
    (repo / "x" / "a.cpp").write_text("")
    (repo / "VERSION").write_text("1.0.0\n")
    _, stack, version_file, _, _ = init_repo.plan(repo, ref="HEAD")
    assert (stack, version_file) == ("cpp", "VERSION")


def test_unreadable_candidate_version_file_is_skipped(tmp_path):
    repo = make_repo(tmp_path, {"pyproject.toml": '[project]\nname = "x"\ndynamic = ["version"]\n',
                                "VERSION": "2.0.0\n"})
    _, _, version_file, _, _ = init_repo.plan(repo, ref="HEAD")
    assert version_file == "VERSION"


def test_target_must_be_a_directory(tmp_path):
    assert init_repo.main([str(tmp_path / "missing"), "--ref", "HEAD"]) == 1


def test_settings_without_release(tmp_path, capsys):
    repo = make_repo(tmp_path, {"a.sh": ""})
    assert init_repo.main([str(repo), "--ref", "HEAD"]) == 0
    out = capsys.readouterr().out
    assert "no release workflows" in out and "Dependency graph" in out
    assert "create and approve pull requests" not in out


def test_repin_moves_only_the_pins(tmp_path, capsys, monkeypatch):
    repo = make_repo(tmp_path, PYTHON)
    init_repo.main([str(repo), "--ref", "HEAD"])
    ci = repo / ".github/workflows/ci.yml"
    ci.write_text(ci.read_text().replace("    # with:\n", "    with:\n      commands: make check\n"))
    (repo / ".github/workflows/other.yml").write_text("uses: someone/else/x.yml@" + "b" * 40 + " # v9\n")
    newer = "c" * 40
    monkeypatch.setattr(init_repo, "resolve_ref", lambda ref: (newer, "v9.9.9"))

    assert init_repo.main([str(repo), "--repin"]) == 0
    assert "Pinned to v9.9.9" in capsys.readouterr().out
    text = ci.read_text()
    assert f"@{newer} # v9.9.9" in text and SHA not in text
    assert "commands: make check" in text
    assert (repo / ".github/workflows/other.yml").read_text().endswith("@" + "b" * 40 + " # v9\n")


def test_repin_without_pins(tmp_path, capsys):
    repo = make_repo(tmp_path, {"a.sh": ""})
    (repo / ".github/workflows").mkdir(parents=True)
    assert init_repo.main([str(repo), "--repin", "--ref", "HEAD"]) == 0
    assert "No library pins found" in capsys.readouterr().out
    assert init_repo.main([str(repo), "--repin", "--ref", "no-such-ref"]) == 1

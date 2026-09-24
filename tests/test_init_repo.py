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
    "requirements.lock": "a==1 \\\n    --hash=sha256:00\n",
    "docker/Dockerfile": "FROM scratch\n",
}


def test_python_repo(tmp_path):
    repo = make_repo(tmp_path, PYTHON)
    assert init_repo.main([str(repo), "--ref", "HEAD"]) == 0

    ci = load(repo, ".github/workflows/ci.yml")
    assert set(ci["jobs"]) == {"python", "lint"}
    security = load(repo, ".github/workflows/security.yml")
    assert set(security["jobs"]) == {"security", "codeql", "dependency-review", "container-scan"}
    assert security["jobs"]["security"]["with"]["pip-audit-files"] == "requirements.lock"
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
    target, detected, version_file, outputs = init_repo.plan(repo, ref="HEAD")
    assert detected == stack
    assert version_file == release_file
    security = yaml.safe_load(outputs[".github/workflows/security.yml"])
    assert security["jobs"]["codeql"]["with"]["languages"] == languages
    assert set(yaml.safe_load(outputs[".github/workflows/ci.yml"])["jobs"]) == {"lint"}
    assert (".github/workflows/release.yml" in outputs) == (release_file is not None)


def test_release_checks_run_lint_outside_python(tmp_path):
    repo = make_repo(tmp_path, {"build.sh": "", "VERSION": "1.0.0\n"})
    _, _, _, outputs = init_repo.plan(repo, ref="HEAD")
    release = yaml.safe_load(outputs[".github/workflows/release.yml"])
    assert "/lint.yml@" in release["jobs"]["check"]["uses"]


def test_submodules_and_npm_get_dependabot(tmp_path):
    repo = make_repo(tmp_path, {".gitmodules": "", "package.json": '{"version": "1.0.0"}', "a.sh": ""})
    _, _, _, outputs = init_repo.plan(repo, ref="HEAD")
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
    _, _, _, outputs = init_repo.plan(repo, release=False, ref="HEAD")
    assert ".github/workflows/release.yml" not in outputs
    _, _, version_file, _ = init_repo.plan(repo, version_file="VERSION", ref="HEAD")
    assert version_file == "VERSION"


def test_bad_version_file_is_an_error(tmp_path):
    repo = make_repo(tmp_path, PYTHON)
    assert init_repo.main([str(repo), "--ref", "HEAD", "--version-file", "x/__init__.py"]) == 1


def test_unknown_ref_is_an_error(tmp_path):
    repo = make_repo(tmp_path, PYTHON)
    assert init_repo.main([str(repo), "--ref", "no-such-ref"]) == 1

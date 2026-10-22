import importlib.util
import json
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
    assert security["jobs"]["container-scan"]["with"] == {
        "dockerfile": "docker/Dockerfile", "context": "docker", "config-scan": True}

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


@pytest.mark.parametrize("files,stack,languages,release_file,ci_jobs", [
    ({"CMakeLists.txt": "project(T VERSION 2.1.0 LANGUAGES CXX)\n", "main.cpp": ""}, "cpp",
     '["c-cpp", "actions"]', "CMakeLists.txt", {"build", "lint"}),
    ({"src/a.cpp": "", "src/a.h": "", "Setup.py": ""}, "cpp", '["c-cpp", "actions"]', None, {"lint"}),
    ({"a.json": "{}", "b.json": "{}", "c.json": "{}", "README.md": ""}, "data", '["actions"]', None,
     {"lint"}),
    ({"build.sh": "#!/bin/sh\n", "VERSION": "0.3.0\n"}, "shell", '["actions"]', "VERSION", {"lint"}),
])
def test_stack_detection(tmp_path, files, stack, languages, release_file, ci_jobs):
    repo = make_repo(tmp_path, files)
    _target, detected, version_file, outputs, _ = init_repo.plan(repo, ref="HEAD")
    assert detected == stack
    assert version_file == release_file
    security = yaml.safe_load(outputs[".github/workflows/security.yml"])
    assert security["jobs"]["codeql"]["with"]["languages"] == languages
    assert set(yaml.safe_load(outputs[".github/workflows/ci.yml"])["jobs"]) == ci_jobs
    assert (".github/workflows/release.yml" in outputs) == (release_file is not None)


@pytest.mark.parametrize("gitmodules", [False, True])
def test_cmake_repos_build_on_linux_and_windows(tmp_path, gitmodules):
    files = {"CMakeLists.txt": "project(T VERSION 1.0.0)\n", "main.cpp": ""}
    if gitmodules:
        files[".gitmodules"] = ""
    repo = make_repo(tmp_path, files)
    _, _, _, outputs, _ = init_repo.plan(repo, ref="HEAD")
    build = yaml.safe_load(outputs[".github/workflows/ci.yml"])["jobs"]["build"]
    assert "/cpp-build.yml@" in build["uses"]
    assert build["strategy"]["matrix"]["os"] == ["ubuntu-latest", "windows-latest"]
    assert "ctest" in build["with"]["test-command"]
    assert build["with"].get("submodules") == ("recursive" if gitmodules else None)


@pytest.mark.parametrize("names,jobs", [
    (["docker/docker-compose.yml"], {"compose"}),
    (["compose.yaml", "dev/compose.yml"], {"compose-1", "compose-2"}),
])
def test_compose_files_get_a_stack_test(tmp_path, names, jobs):
    repo = make_repo(tmp_path, {"a.sh": "", **{name: "services: {}\n" for name in names}})
    _, _, _, outputs, _ = init_repo.plan(repo, ref="HEAD")
    ci = yaml.safe_load(outputs[".github/workflows/ci.yml"])["jobs"]
    assert set(ci) == jobs | {"lint"}
    assert sorted(ci[job]["with"]["compose-file"] for job in jobs) == sorted(names)
    assert all("/compose-test.yml@" in ci[job]["uses"] for job in jobs)


def test_installer_is_compiled_in_ci_and_attached_on_release(tmp_path):
    repo = make_repo(tmp_path, {**PYTHON, "packaging/app.iss": ""})
    _, _, _, outputs, _ = init_repo.plan(repo, ref="HEAD")
    installer = yaml.safe_load(outputs[".github/workflows/ci.yml"])["jobs"]["installer"]
    assert "/inno-setup.yml@" in installer["uses"]
    assert installer["with"] == {"script": "packaging/app.iss", "version": "0.0.0-ci"}

    release = yaml.safe_load(outputs[".github/workflows/release.yml"])["jobs"]
    assert set(release) == {"check", "pending", "installer", "attest", "release"}
    assert "/release-check.yml@" in release["pending"]["uses"]
    assert release["installer"]["if"] == "needs.pending.outputs.pending == 'true'"
    assert release["installer"]["with"]["version"] == "${{ needs.pending.outputs.version }}"
    assert "/attest.yml@" in release["attest"]["uses"]
    assert release["attest"]["needs"] == ["installer"]
    assert release["attest"]["permissions"] == {"id-token": "write", "attestations": "write"}
    assert release["attest"]["with"] == {"artifacts": "installer*"}
    assert release["release"]["needs"] == ["check", "attest", "installer"]
    assert release["release"]["with"]["assets"] == "installer*"
    assert release["release"]["with"]["checksums"] is True


def test_several_installers_each_get_a_job(tmp_path):
    repo = make_repo(tmp_path, {**PYTHON, "a.iss": "", "b/b.iss": ""})
    _, _, _, outputs, _ = init_repo.plan(repo, ref="HEAD")
    assert {"installer-1", "installer-2"} <= set(yaml.safe_load(outputs[".github/workflows/ci.yml"])["jobs"])
    release = yaml.safe_load(outputs[".github/workflows/release.yml"])["jobs"]
    assert release["attest"]["needs"] == ["installer-1", "installer-2"]
    assert release["release"]["needs"] == ["check", "attest", "installer-1", "installer-2"]
    assert release["installer-2"]["with"]["artifact"] == "installer-2"


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


def test_compose_images_get_dependabot(tmp_path):
    repo = make_repo(tmp_path, {**PYTHON, "docker/docker-compose.yml": "services: {}\n",
                                "compose.yaml": "services: {}\n"})
    _, _, _, outputs, _ = init_repo.plan(repo, ref="HEAD")
    ecosystems = {u["package-ecosystem"]: u["directories"]
                  for u in yaml.safe_load(outputs[".github/dependabot.yml"])["updates"]}
    assert ecosystems["docker-compose"] == ["/", "/docker"]
    assert ecosystems["docker"] == ["/docker"]


def dependabot_assignees(outputs):
    return [u.get("assignees") for u in yaml.safe_load(outputs[".github/dependabot.yml"])["updates"]]


def test_dependabot_assigns_the_origins_owner(tmp_path):
    repo = make_repo(tmp_path, PYTHON)
    subprocess.run(["git", "remote", "add", "origin", "git@github.com:Someone/App.git"], cwd=repo, check=True)
    _, _, _, outputs, _ = init_repo.plan(repo, ref="HEAD")
    assert dependabot_assignees(outputs) == [["Someone"]] * 3


@pytest.mark.parametrize("remote", [None, "https://gitlab.com/Someone/App.git"])
def test_dependabot_is_unassigned_without_a_github_origin(tmp_path, remote):
    repo = make_repo(tmp_path, {"a.sh": ""})
    if remote:
        subprocess.run(["git", "remote", "add", "origin", remote], cwd=repo, check=True)
    _, _, _, outputs, _ = init_repo.plan(repo, ref="HEAD")
    assert dependabot_assignees(outputs) == [None]


def test_assignees_on_the_command_line(tmp_path):
    repo = make_repo(tmp_path, {"a.sh": ""})
    subprocess.run(["git", "remote", "add", "origin", "git@github.com:SomeOrg/App.git"], cwd=repo, check=True)
    assert init_repo.main([str(repo), "--ref", "HEAD", "--assignee", "alice", "--assignee", "bob"]) == 0
    assert [u["assignees"] for u in load(repo, ".github/dependabot.yml")["updates"]] == [["alice", "bob"]]
    assert init_repo.main([str(repo), "--ref", "HEAD", "--no-assignee", "--force"]) == 0
    assert "assignees" not in (repo / ".github/dependabot.yml").read_text()


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


def test_repin_moves_action_pins_too(tmp_path, monkeypatch):
    repo = make_repo(tmp_path, PYPI)
    init_repo.main([str(repo), "--ref", "HEAD", "--pypi"])
    newer = "c" * 40
    monkeypatch.setattr(init_repo, "resolve_ref", lambda ref: (newer, "v9.9.9"))

    assert init_repo.main([str(repo), "--repin"]) == 0
    text = (repo / ".github/workflows/release.yml").read_text()
    assert f"/actions/pypi-publish@{newer} # v9.9.9" in text and SHA not in text


def test_repin_dry_run_writes_nothing(tmp_path, capsys, monkeypatch):
    repo = make_repo(tmp_path, PYTHON)
    init_repo.main([str(repo), "--ref", "HEAD"])
    before = {path.name: path.read_text() for path in (repo / ".github/workflows").glob("*.yml")}
    monkeypatch.setattr(init_repo, "resolve_ref", lambda ref: ("c" * 40, "v9.9.9"))

    assert init_repo.main([str(repo), "--repin", "--dry-run"]) == 0
    assert "Would pin to v9.9.9" in capsys.readouterr().out
    assert {path.name: path.read_text() for path in (repo / ".github/workflows").glob("*.yml")} == before


def test_repin_reports_pins_already_at_the_ref(tmp_path, capsys, monkeypatch):
    repo = make_repo(tmp_path, PYTHON)
    init_repo.main([str(repo), "--ref", "HEAD"])
    monkeypatch.setattr(init_repo, "resolve_ref", lambda ref: ("c" * 40, "v9.9.9"))
    init_repo.main([str(repo), "--repin"])
    capsys.readouterr()

    assert init_repo.main([str(repo), "--repin"]) == 0
    out = capsys.readouterr().out
    assert "Already pinned to v9.9.9" in out and "No library pins found" not in out


def test_repin_without_pins(tmp_path, capsys):
    repo = make_repo(tmp_path, {"a.sh": ""})
    (repo / ".github/workflows").mkdir(parents=True)
    assert init_repo.main([str(repo), "--repin", "--ref", "HEAD"]) == 0
    assert "No library pins found" in capsys.readouterr().out
    assert init_repo.main([str(repo), "--repin", "--ref", "no-such-ref"]) == 1


def test_workflow_and_job_names_are_capitalized(tmp_path):
    repo = make_repo(tmp_path, {**PYTHON, "other/Dockerfile": "FROM scratch\n"})
    _, _, _, outputs, _ = init_repo.plan(repo, ref="HEAD")
    names = {}
    for name, text in outputs.items():
        if name.startswith(".github/workflows/"):
            workflow = yaml.safe_load(text)
            names[workflow["name"]] = sorted(job["name"] for job in workflow["jobs"].values())
    assert names == {
        "CI": ["Lint", "Python"],
        "Security": ["CodeQL", "Container Scan (docker/Dockerfile)", "Container Scan (other/Dockerfile)",
                     "Dependency Review", "Security"],
        "Prepare Release": ["Prepare"],
        "Release": ["Check", "Pending", "Release"],
    }


@pytest.mark.parametrize("files", [PYTHON, {**PYTHON, "packaging/app.iss": ""}])
def test_release_checks_only_run_when_a_release_is_pending(tmp_path, files):
    repo = make_repo(tmp_path, files)
    _, _, _, outputs, _ = init_repo.plan(repo, ref="HEAD")
    jobs = yaml.safe_load(outputs[".github/workflows/release.yml"])["jobs"]
    assert "/release-check.yml@" in jobs["pending"]["uses"]
    assert jobs["check"]["needs"] == "pending"
    assert jobs["check"]["if"] == "needs.pending.outputs.pending == 'true'"


def test_only_pull_request_runs_are_cancelled(tmp_path):
    repo = make_repo(tmp_path, PYTHON)
    _, _, _, outputs, _ = init_repo.plan(repo, ref="HEAD")
    for name in [".github/workflows/ci.yml", ".github/workflows/security.yml"]:
        concurrency = yaml.safe_load(outputs[name])["concurrency"]
        assert concurrency["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"


PYPI = {
    "pyproject.toml": '[project]\nname = "some-package"\nversion = "1.0.0"\n',
    "some_package/__init__.py": "",
}


def test_pypi_releases_build_attach_and_publish_the_package(tmp_path):
    repo = make_repo(tmp_path, PYPI)

    assert init_repo.main([str(repo), "--ref", "HEAD", "--pypi"]) == 0

    jobs = load(repo, ".github/workflows/release.yml")["jobs"]
    assert list(jobs) == ["pending", "check", "build", "release", "publish"]
    assert jobs["build"]["uses"].endswith("/.github/workflows/python-build.yml@" + SHA)
    assert jobs["release"]["needs"] == "build"
    assert jobs["release"]["with"] == {"version-file": "pyproject.toml", "assets": "dist", "checksums": True}
    publish = jobs["publish"]
    assert publish["if"] == "needs.release.outputs.released == 'true'"
    assert publish["environment"] == {
        "name": "pypi", "url": "https://pypi.org/project/some-package/${{ needs.release.outputs.version }}/"}
    assert publish["permissions"] == {"id-token": "write"}
    assert publish["steps"] == [{"uses": "NearlyTRex/Workflows/actions/pypi-publish@" + SHA}]


def test_a_release_without_pypi_publishes_nothing(tmp_path):
    repo = make_repo(tmp_path, PYPI)

    assert init_repo.main([str(repo), "--ref", "HEAD"]) == 0

    assert list(load(repo, ".github/workflows/release.yml")["jobs"]) == ["pending", "check", "release"]


@pytest.mark.parametrize("files, message", [
    ({"VERSION": "1.0.0\n", "a.sh": ""}, "--pypi needs"),
    ({"pyproject.toml": '[project]\nversion = "1.0.0"\n'}, "--pypi needs a [project] name"),
])
def test_pypi_needs_a_named_project(tmp_path, capsys, files, message):
    repo = make_repo(tmp_path, files)

    assert init_repo.main([str(repo), "--ref", "HEAD", "--pypi"]) == 1
    assert message in capsys.readouterr().err


def fake_gh(monkeypatch, fails=False, rulesets=(), payloads=None):
    """Let git through, and record gh calls instead of running them. Listing rulesets
    answers with rulesets; what a call is given on stdin goes in payloads."""
    calls, real_run = [], subprocess.run

    def run(command, **kwargs):
        if command[0] != "gh":
            return real_run(command, **kwargs)
        calls.append(command)
        if payloads is not None and kwargs.get("input") is not None:
            payloads.append(json.loads(kwargs["input"]))
        stdout = json.dumps(list(rulesets)) if command[-1].endswith("/rulesets") else ""
        return subprocess.CompletedProcess(command, 1 if fails else 0, stdout, "HTTP 403: Must have admin rights")
    monkeypatch.setattr(init_repo.subprocess, "run", run)
    return calls


def ruleset_calls(slug, method="POST", path="rulesets"):
    return [["gh", "api", f"repos/{slug}/rulesets"],
            ["gh", "api", "-X", method, f"repos/{slug}/{path}", "--input", "-"]]


def with_origin(repo, url="git@github.com:Someone/App.git"):
    subprocess.run(["git", "remote", "add", "origin", url], cwd=repo, check=True)
    return repo


def test_settings_turns_on_what_the_workflows_need(tmp_path, capsys, monkeypatch):
    repo = with_origin(make_repo(tmp_path, PYTHON))
    init_repo.main([str(repo), "--ref", "HEAD"])
    assert "--settings" in capsys.readouterr().out
    calls = fake_gh(monkeypatch)

    assert init_repo.main([str(repo), "--settings"]) == 0
    assert calls == [
        ["gh", "api", "-X", "PUT", "repos/Someone/App/vulnerability-alerts"],
        ["gh", "api", "-X", "PUT", "repos/Someone/App/actions/permissions/workflow",
         "-f", "default_workflow_permissions=read", "-F", "can_approve_pull_request_reviews=true"],
        ["gh", "api", "-X", "PUT", "repos/Someone/App/private-vulnerability-reporting"],
        *ruleset_calls("Someone/App"),
    ]
    out = capsys.readouterr().out
    assert "Set: Actions may open pull requests" in out and "Someone/App is set up" in out
    assert "Set: pull requests required for the default branch" in out


def test_settings_without_release_workflows(tmp_path, monkeypatch):
    repo = with_origin(make_repo(tmp_path, {"a.sh": ""}), "https://github.com/Someone/Scripts")
    calls = fake_gh(monkeypatch)
    assert init_repo.main([str(repo), "--settings"]) == 0
    assert calls == [["gh", "api", "-X", "PUT", "repos/Someone/Scripts/vulnerability-alerts"],
                     *ruleset_calls("Someone/Scripts")]


def test_settings_dry_run_only_prints(tmp_path, capsys, monkeypatch):
    repo = with_origin(make_repo(tmp_path, {"a.sh": ""}))
    calls = fake_gh(monkeypatch)
    assert init_repo.main([str(repo), "--settings", "--dry-run"]) == 0
    assert calls == []
    out = capsys.readouterr().out
    assert "Would run (Dependabot alerts" in out
    assert "gh api -X PUT repos/Someone/App/vulnerability-alerts" in out and "is set up" not in out
    assert "Would create or update the 'Protect main' ruleset" in out and '"~DEFAULT_BRANCH"' in out


def test_settings_protect_the_default_branch(tmp_path, monkeypatch):
    repo = with_origin(make_repo(tmp_path, {"a.sh": ""}))
    payloads = []
    fake_gh(monkeypatch, payloads=payloads)
    assert init_repo.main([str(repo), "--settings"]) == 0
    assert payloads == [init_repo.branch_ruleset()]
    ruleset = payloads[0]
    assert ruleset["conditions"]["ref_name"]["include"] == ["~DEFAULT_BRANCH"]
    assert [rule["type"] for rule in ruleset["rules"]] == ["pull_request", "non_fast_forward", "deletion"]
    assert ruleset["rules"][0]["parameters"]["required_approving_review_count"] == 0
    assert ruleset["bypass_actors"] == []


def test_settings_update_the_ruleset_rather_than_add_another(tmp_path, monkeypatch):
    repo = with_origin(make_repo(tmp_path, {"a.sh": ""}))
    calls = fake_gh(monkeypatch, rulesets=[{"id": 7, "name": "Other"}, {"id": 42, "name": "Protect main"}])
    assert init_repo.main([str(repo), "--settings"]) == 0
    assert calls[-2:] == ruleset_calls("Someone/App", "PUT", "rulesets/42")


@pytest.mark.parametrize("remote", [None, "https://gitlab.com/Someone/App.git"])
def test_settings_need_a_github_origin(tmp_path, capsys, monkeypatch, remote):
    repo = make_repo(tmp_path, {"a.sh": ""})
    if remote:
        with_origin(repo, remote)
    calls = fake_gh(monkeypatch)
    assert init_repo.main([str(repo), "--settings"]) == 1
    assert calls == [] and "error:" in capsys.readouterr().err


def test_settings_report_what_gh_refused(tmp_path, capsys, monkeypatch):
    repo = with_origin(make_repo(tmp_path, {"a.sh": ""}))
    fake_gh(monkeypatch, fails=True)
    assert init_repo.main([str(repo), "--settings"]) == 1
    assert "Must have admin rights" in capsys.readouterr().err


COMMUNITY = [
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/ISSUE_TEMPLATE/config.yml",
    ".github/ISSUE_TEMPLATE/feature_request.yml",
    ".github/pull_request_template.md",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    ".markdownlint.yaml",
]


def with_community_origin(repo, url="git@github.com:Owner/thing.git"):
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", url], check=True)
    return repo


def test_community_files_are_filled_in_for_the_repo(tmp_path):
    repo = with_community_origin(make_repo(tmp_path, {"README.md": ""}))

    assert init_repo.main([str(repo), "--community"]) == 0

    for name in COMMUNITY:
        text = (repo / name).read_text()
        assert "{{" not in text, name
    assert "https://github.com/Owner/thing/security/advisories/new" in (repo / "SECURITY.md").read_text()
    assert (repo / "CONTRIBUTING.md").read_text().startswith("# Contributing to thing\n")


def test_issue_forms_are_valid_and_point_security_reports_away(tmp_path):
    repo = with_community_origin(make_repo(tmp_path, {"README.md": ""}))
    init_repo.main([str(repo), "--community"])

    config = load(repo, ".github/ISSUE_TEMPLATE/config.yml")
    assert config["blank_issues_enabled"] is False
    assert config["contact_links"][0]["url"] == "https://github.com/Owner/thing/security/advisories/new"
    for form in ["bug_report.yml", "feature_request.yml"]:
        body = load(repo, f".github/ISSUE_TEMPLATE/{form}")["body"]
        assert any(item.get("validations", {}).get("required") for item in body), form


def test_the_library_markdownlint_config_is_copied_as_it_is(tmp_path):
    repo = with_community_origin(make_repo(tmp_path, {"README.md": ""}))

    assert init_repo.main([str(repo), "--community"]) == 0

    assert (repo / ".markdownlint.yaml").read_text() == (ROOT / ".markdownlint.yaml").read_text()
    assert load(repo, ".markdownlint.yaml")["MD013"]["line_length"] == 100


def test_a_repos_own_community_files_are_kept(tmp_path, capsys):
    repo = with_community_origin(make_repo(tmp_path, {"README.md": "", "SECURITY.md": "ours\n"}))

    assert init_repo.main([str(repo), "--community"]) == 0

    assert (repo / "SECURITY.md").read_text() == "ours\n"
    assert (repo / "CONTRIBUTING.md").exists()
    assert "kept the repo's own: SECURITY.md" in capsys.readouterr().out


def test_community_dry_run_writes_nothing(tmp_path, capsys):
    repo = with_community_origin(make_repo(tmp_path, {"README.md": ""}))

    assert init_repo.main([str(repo), "--community", "--dry-run"]) == 0

    assert not (repo / "CONTRIBUTING.md").exists()
    assert "would write CONTRIBUTING.md" in capsys.readouterr().out


def test_community_files_need_a_github_origin(tmp_path, capsys):
    repo = make_repo(tmp_path, {"README.md": ""})

    assert init_repo.main([str(repo), "--community"]) == 1
    assert "no GitHub origin" in capsys.readouterr().err


def test_community_needs_a_directory(tmp_path, capsys):
    assert init_repo.main([str(tmp_path / "absent"), "--community"]) == 1
    assert "is not a directory" in capsys.readouterr().err


def test_a_full_init_adds_community_files_but_never_overwrites_them(tmp_path):
    repo = with_community_origin(make_repo(tmp_path, {**PYTHON, "CODE_OF_CONDUCT.md": "ours\n"}))

    assert init_repo.main([str(repo), "--ref", "HEAD", "--force"]) == 0

    assert (repo / "CODE_OF_CONDUCT.md").read_text() == "ours\n"
    assert all((repo / name).exists() for name in COMMUNITY)


def test_a_full_init_without_an_origin_says_how_to_add_them(tmp_path, capsys):
    repo = make_repo(tmp_path, PYTHON)

    assert init_repo.main([str(repo), "--ref", "HEAD"]) == 0

    assert "run with --community" in capsys.readouterr().out
    assert not (repo / "CONTRIBUTING.md").exists()


def test_a_repo_with_a_security_policy_is_told_to_turn_on_private_reporting(tmp_path, capsys):
    repo = with_community_origin(make_repo(tmp_path, PYTHON))

    assert init_repo.main([str(repo), "--ref", "HEAD"]) == 0

    assert "Private vulnerability reporting, for SECURITY.md's report form" in capsys.readouterr().out

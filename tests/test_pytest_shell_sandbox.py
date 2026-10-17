"""The shell sandbox: building it, arranging and reading back, and real runs in a user namespace."""

import importlib.util
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT / "actions" / "shell-coverage"
spec = importlib.util.spec_from_file_location("pytest_shell_sandbox", PLUGIN_DIR / "pytest_shell_sandbox.py")
sandbox_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sandbox_mod)


@pytest.fixture
def sandbox(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    return sandbox_mod.ShellSandbox(tmp_path / "sb", repo, extra_stubs=["terraform"], extra_tools=["jq"])


# --- building and reading back (any OS) ---------------------------------

def test_root_has_accounts_and_hidden_dirs(sandbox):
    assert "root:x:0:0" in sandbox.read("/etc/passwd")
    assert sandbox.exists("/var/log") and sandbox.exists("/usr/local/bin")
    assert oct(os.stat(sandbox.host_path("/tmp")).st_mode & 0o7777) == "0o1777"


def test_add_user_creates_account_group_membership_and_home(sandbox):
    sandbox.add_user("alice", uid=1001, groups=["sudo"])
    assert "alice:x:1001:1001::/home/alice:/bin/bash" in sandbox.read("/etc/passwd")
    assert "sudo:x:27:alice" in sandbox.read("/etc/group")
    assert sandbox.exists("/home/alice")


def test_stubs_are_executable_and_include_extras(sandbox):
    for name in ("systemctl", "sudo", "terraform"):
        assert os.access(os.path.join(sandbox.stub_dir, name), os.X_OK)


def test_stub_records_calls_and_replays_output(sandbox):
    sandbox.stub("tool", stdout="out\n", code=3)
    path = os.path.join(sandbox.stub_dir, "tool")
    result = subprocess.run([path, "a b", "c"], capture_output=True, text=True, check=False)
    assert (result.stdout, result.returncode) == ("out\n", 3)
    subprocess.run([os.path.join(sandbox.stub_dir, "systemctl"), "enable", "x"], check=False)
    assert sandbox.calls("tool") == [["a b", "c"]]
    assert sandbox.calls() == [["tool", "a b", "c"], ["systemctl", "enable", "x"]]
    assert sandbox.ran("tool", "a b") and not sandbox.ran("tool", "c")
    assert not sandbox.ran("nothing")


def test_calls_empty_before_anything_runs(sandbox):
    assert sandbox.calls() == []


def test_write_sets_mode(sandbox):
    sandbox.write("/etc/app/conf", "x", mode=0o600)
    assert oct(os.stat(sandbox.host_path("/etc/app/conf")).st_mode & 0o777) == "0o600"


def test_allowed_tools_are_linked(sandbox):
    assert os.path.islink(os.path.join(sandbox.tool_dir, "cat"))


def test_options_override_defaults(tmp_path):
    custom = sandbox_mod.ShellSandbox(tmp_path / "sb", tmp_path, stubs=["only"], allowed_tools=["cat"],
                                      hidden_dirs=["/srv"], accounts="x", group="y")
    assert sorted(os.listdir(custom.stub_dir)) == [".only.stdout", ".sudo.stdout", "only", "sudo"]
    assert os.listdir(custom.tool_dir) == ["cat"]
    assert not custom.exists("/etc/passwd") and custom.exists("/srv")


def test_launcher_hides_dirs_and_keeps_traces(tmp_path):
    traced = sandbox_mod.ShellSandbox(tmp_path / "sb", tmp_path / "repo", trace_dir="/t/traces", hook_file="/t/hook.sh")
    launcher = traced.build_launcher(["bash", "my script.sh"], {"A": "b c"})
    assert "mount --bind /media/keep0/root/etc /etc" in launcher
    assert "/t/traces" in launcher and "/t" in launcher
    assert launcher.rstrip().endswith("exec env -i A='b c' bash 'my script.sh'")
    untraced = sandbox_mod.ShellSandbox(tmp_path / "sb2", tmp_path / "repo")
    assert "/t/traces" not in untraced.build_launcher(["true"], {})


def test_unavailable_namespaces_skip_or_fail(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox_mod, "_namespace_support", {"error": "nope"})
    sb = sandbox_mod.ShellSandbox(tmp_path / "sb", tmp_path)
    with pytest.raises(pytest.skip.Exception, match="nope"):
        sb.run_command(["true"])
    required = sandbox_mod.ShellSandbox(tmp_path / "sb2", tmp_path, required=True)
    with pytest.raises(pytest.fail.Exception, match="nope"):
        required.run_command(["true"])


def test_check_namespace_support_without_unshare(monkeypatch):
    monkeypatch.setattr(sandbox_mod, "_namespace_support", {})

    def missing(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", missing)
    assert sandbox_mod.check_namespace_support() == "unshare is not installed"


def test_check_namespace_support_failure_and_success(monkeypatch):
    results = iter([subprocess.CompletedProcess([], 1, "", " denied \n"), subprocess.CompletedProcess([], 0, "", "")])
    for expected in ("denied", None):
        monkeypatch.setattr(sandbox_mod, "_namespace_support", {})
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: next(results))
        assert sandbox_mod.check_namespace_support() == expected


# --- real runs (need unprivileged user namespaces) -----------------------

needs_userns = pytest.mark.skipif(sandbox_mod.check_namespace_support() is not None,
                                  reason="needs unprivileged user namespaces")


@needs_userns
def test_script_runs_as_root_with_stubs_and_private_dirs(tmp_path):
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "setup.sh").write_text(textwrap.dedent("""\
        #!/bin/bash
        set -e
        [ "$(id -u)" = 0 ]
        echo "$1" > /etc/app.conf
        sudo systemctl enable "$1"
        """))
    sb = sandbox_mod.ShellSandbox(tmp_path / "sb", repo)
    result = sb.run("scripts/setup.sh", "svc")
    assert result.returncode == 0, result.stderr
    assert sb.read("/etc/app.conf") == "svc\n"
    assert sb.ran("systemctl", "enable", "svc")
    assert not os.path.exists("/etc/app.conf")


@needs_userns
def test_unplanned_command_is_not_found(tmp_path):
    sb = sandbox_mod.ShellSandbox(tmp_path / "sb", tmp_path)
    result = sb.run_command(["bash", "-c", "hostname"])
    assert result.returncode == 127


@needs_userns
def test_sandbox_runs_are_traced_for_coverage(tmp_path):
    project = tmp_path / "proj"
    (project / "scripts").mkdir(parents=True)
    (project / "scripts" / "a.sh").write_text("echo one\nif false; then\n    echo two\nfi\n")
    (project / "test_a.py").write_text(textwrap.dedent("""\
        def test_a(shell_sandbox):
            assert shell_sandbox.run("scripts/a.sh").returncode == 0
        """))
    env = {k: v for k, v in os.environ.items() if k not in ("BASH_ENV", "SHELL_COV_TRACE_DIR", "PYTEST_ADDOPTS")}
    env["PYTHONPATH"] = str(PLUGIN_DIR)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "pytest_shell_cov", "-p", "pytest_shell_sandbox", "-p",
         "no:cacheprovider", "-q", "--shell-cov-source", "scripts", "--shell-cov-fail-under", "100"],
        cwd=project, env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "66.7%  3" in result.stdout
    assert "1 passed" in result.stdout


# --- host-independent coverage of the rest -------------------------------

def unwrap(fixture):
    return getattr(fixture, "__wrapped__", None) or fixture.__pytest_wrapped__.obj


def test_host_etc_files_and_dirs_are_copied_when_present(tmp_path, monkeypatch):
    host = tmp_path / "host-etc"
    (host / "ssl").mkdir(parents=True)
    (host / "ssl" / "openssl.cnf").write_text("cnf")
    (host / "os-release").write_text("ID=x")
    (host / "alternatives").mkdir()
    (host / "alternatives" / "editor").write_text("vim")
    monkeypatch.setattr(sandbox_mod, "HOST_ETC", str(host))
    sb = sandbox_mod.ShellSandbox(tmp_path / "sb", tmp_path)
    assert sb.read("/etc/ssl/openssl.cnf") == "cnf" and sb.read("/etc/os-release") == "ID=x"
    assert sb.read("/etc/alternatives/editor") == "vim"
    assert not sb.exists("/etc/localtime") and not sb.exists("/etc/ld.so.conf.d")


def test_add_user_without_groups_and_with_unmatched_groups(sandbox):
    sandbox.add_user("bob")
    sandbox.add_user("carol", uid=1002, groups=["nosuchgroup", "adm"])
    assert "adm:x:4:carol" in sandbox.read("/etc/group")
    assert "bob:x:1000:1000" in sandbox.read("/etc/passwd")


def test_hidden_dirs_without_tmp_or_etc(tmp_path):
    sb = sandbox_mod.ShellSandbox(tmp_path / "sb", tmp_path, hidden_dirs=["/opt", "/var"])
    assert sb.exists("/var/lib") and not sb.exists("/usr/local/bin") and not sb.exists("/etc/passwd")


def test_run_builds_launcher_and_calls_unshare(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox_mod, "_namespace_support", {"error": None})
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"], seen["kwargs"] = argv, kwargs
        seen["launcher"] = open(argv[-1], encoding="utf-8").read()
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    sb = sandbox_mod.ShellSandbox(tmp_path / "sb", tmp_path / "repo", trace_dir=str(tmp_path / "t"),
                                  hook_file=str(tmp_path / "t" / "hook.sh"))
    result = sb.run("scripts/x.sh", 1, env={"EXTRA": "1"}, input="in", timeout=5)
    assert result.stdout == "ok"
    assert seen["argv"][:6] == ["unshare", "--user", "--map-root-user", "--mount", "--net", "--fork"]
    assert seen["kwargs"]["input"] == "in" and seen["kwargs"]["timeout"] == 5
    assert "EXTRA=1" in seen["launcher"] and "BASH_ENV=" in seen["launcher"]
    assert f"bash {tmp_path / 'repo' / 'scripts' / 'x.sh'} 1" in seen["launcher"]
    sb.run_command(["true"])
    assert "SHELL_COV_TRACE_DIR" in seen["launcher"]


def test_run_without_tracing_sets_no_hook(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox_mod, "_namespace_support", {"error": None})
    seen = {}
    monkeypatch.setattr(subprocess, "run", lambda argv, **k: seen.update(
        launcher=open(argv[-1], encoding="utf-8").read()) or subprocess.CompletedProcess(argv, 0, "", ""))
    sandbox_mod.ShellSandbox(tmp_path / "sb", tmp_path).run_command(["true"])
    assert "BASH_ENV" not in seen["launcher"]


def test_plugin_option_and_fixtures(tmp_path, monkeypatch):
    added = []
    group = type("G", (), {"addoption": lambda self, *a, **k: added.append(a[0])})()
    sandbox_mod.pytest_addoption(type("P", (), {"getgroup": lambda self, name: group})())
    assert added == ["--shell-sandbox-required"]
    assert unwrap(sandbox_mod.shell_sandbox_options)() == {}

    request = type("R", (), {"config": type("C", (), {
        "rootpath": tmp_path, "getoption": lambda self, name: True})()})()
    monkeypatch.setenv("SHELL_COV_TRACE_DIR", str(tmp_path / "traces"))
    monkeypatch.setenv("BASH_ENV", str(tmp_path / "hook.sh"))
    sb = unwrap(sandbox_mod.shell_sandbox)(request, tmp_path / "t", {"extra_stubs": ["x"]})
    assert sb.required and sb.trace_dir == str(tmp_path / "traces") and sb.hook_file == str(tmp_path / "hook.sh")
    assert os.path.exists(os.path.join(sb.stub_dir, "x"))

    monkeypatch.delenv("SHELL_COV_TRACE_DIR")
    sb = unwrap(sandbox_mod.shell_sandbox)(request, tmp_path / "u", {})
    assert sb.trace_dir is None and sb.hook_file is None


def test_allowed_tool_missing_on_the_host_is_skipped(tmp_path):
    sb = sandbox_mod.ShellSandbox(tmp_path / "sb", tmp_path, allowed_tools=["cat", "no-such-tool-anywhere"])
    assert os.listdir(sb.tool_dir) == ["cat"]

"""A disposable machine for pytest tests that run shell scripts.

Scripts written for a fresh server run as root and write to /etc, /var, /home
and the rest. Each run here happens in a user namespace where the caller is
root, with no network, and with those directories replaced by empty ones the
test owns, so whatever a script writes lands in the sandbox and can be read
back.

PATH holds only recording stubs and an allowlist of harmless real tools, so a
command nobody planned for fails with "command not found" rather than running
for real. When pytest_shell_cov is also loaded and enabled, every run is
traced for shell coverage.

    def test_enables_service(shell_sandbox):
        shell_sandbox.run("scripts/setup.sh", "install")
        assert shell_sandbox.ran("systemctl", "enable", "myservice")

Needs Linux with `unshare` and unprivileged user namespaces (Ubuntu 24.04
runners need kernel.apparmor_restrict_unprivileged_userns=0; the
shell-coverage action does that). Where they are unavailable the tests
skip, or fail with --shell-sandbox-required.

Tailor it by overriding the `shell_sandbox_options` fixture:

    @pytest.fixture
    def shell_sandbox_options():
        return {"extra_stubs": ["terraform"], "extra_tools": ["jq"]}

Keys: stubs, extra_stubs, allowed_tools, extra_tools, hidden_dirs, passwd, group.
"""

import os
import shlex
import shutil
import subprocess
import uuid

import pytest

# Directories a script sees as empty and its own, in the order they are hidden
HIDDEN_DIRS = ["/home", "/root", "/var", "/usr/local", "/opt", "/mnt", "/srv", "/etc", "/tmp"]

# Where the directories that must stay reachable are staged while the rest are hidden
STAGING_DIR = "/media"

# Real tools a script may run: they only read and write files it could anyway
ALLOWED_TOOLS = [
    "bash", "sh", "env", "cat", "cp", "mv", "rm", "ln", "mkdir", "rmdir", "chmod", "ls",
    "grep", "sed", "awk", "sort", "uniq", "head", "tail", "tr", "cut", "wc", "basename",
    "dirname", "readlink", "realpath", "mktemp", "touch", "stat", "find", "xargs", "tee",
    "date", "sleep", "id", "whoami", "getent", "seq", "true", "false", "diff", "cmp",
    "install", "sha256sum", "base64", "printf", "test", "[", "expr", "comm", "paste",
    "tac", "yes", "timeout", "openssl", "uname", "nproc", "od", "fold",
]

# Commands every sandbox stubs: they change the machine, reach the network or
# need privileges the namespace does not have
DEFAULT_STUBS = [
    "systemctl", "service", "apt-get", "apt", "dpkg", "dpkg-query", "ufw", "netplan",
    "nginx", "docker", "certbot", "sshd", "fail2ban-client", "useradd", "usermod",
    "userdel", "adduser", "groupadd", "chown", "chgrp", "chpasswd", "passwd", "visudo",
    "curl", "wget", "git", "python3", "pip", "pip3", "mount", "umount", "mountpoint",
    "sshfs", "fusermount", "su", "keytool", "wp", "htpasswd", "hostnamectl",
    "timedatectl", "reboot", "shutdown", "crontab", "ssh", "ssh-keygen", "loginctl",
    "update-alternatives", "snap", "flatpak", "iptables", "mkcert",
]

# sudo runs its command directly: the sandbox is already root
SUDO_STUB = """while [ $# -gt 0 ]; do
    case "$1" in
        -u|-g|-C|-D|-h|-p|-r|-t|-U) shift 2 ;;
        --) shift; break ;;
        -*) shift ;;
        *) break ;;
    esac
done
exec "$@"
"""

# Accounts every sandbox starts with
BASE_ACCOUNTS = (
    "root:x:0:0:root:/root:/bin/bash\n"
    "daemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin\n"
    "www-data:x:33:33:www-data:/var/www:/usr/sbin/nologin\n"
    "nobody:x:65534:65534:nobody:/nonexistent:/usr/sbin/nologin\n")
BASE_GROUPS = (
    "root:x:0:\n"
    "daemon:x:1:\n"
    "adm:x:4:\n"
    "sudo:x:27:\n"
    "www-data:x:33:\n"
    "users:x:100:\n"
    "docker:x:999:\n"
    "nogroup:x:65534:\n")

# What of the host's /etc a program needs to start at all
HOST_ETC = "/etc"
HOST_ETC_FILES = ["ld.so.cache", "ld.so.conf", "localtime", "os-release", "ssl/openssl.cnf"]
HOST_ETC_DIRS = ["ld.so.conf.d", "alternatives"]

# The shell coverage plugin's variables; when set, runs are traced
HOOK_VARIABLE = "BASH_ENV"
TRACE_DIR_VARIABLE = "SHELL_COV_TRACE_DIR"

_namespace_support = {}


def check_namespace_support():
    """Check once whether this machine lets an unprivileged user build the sandbox.

    Returns None when it does, else the reason it doesn't.
    """
    if "error" not in _namespace_support:
        try:
            result = subprocess.run(
                ["unshare", "--user", "--map-root-user", "--mount", "--net", "true"],
                capture_output=True, text=True, check=False)
            error = None if result.returncode == 0 else (result.stderr.strip() or "unshare failed")
        except FileNotFoundError:
            error = "unshare is not installed"
        _namespace_support["error"] = error
    return _namespace_support["error"]


def quote(value):
    return shlex.quote(str(value))


class ShellSandbox:

    def __init__(self, work_dir, repo_root, trace_dir=None, hook_file=None, stubs=None, extra_stubs=(),
                 allowed_tools=None, extra_tools=(), hidden_dirs=None, passwd=None, group=None,
                 required=False):
        self.work_dir = str(work_dir)
        self.repo_root = str(repo_root)
        self.trace_dir = trace_dir
        self.hook_file = hook_file
        self.required = required
        self.hidden_dirs = list(HIDDEN_DIRS if hidden_dirs is None else hidden_dirs)
        self.allowed_tools = list(ALLOWED_TOOLS if allowed_tools is None else allowed_tools) + list(extra_tools)
        self.root_dir = os.path.join(self.work_dir, "root")
        self.stub_dir = os.path.join(self.work_dir, "stubs")
        self.tool_dir = os.path.join(self.work_dir, "tools")
        self.calls_log = os.path.join(self.work_dir, "calls.log")
        self.build_root(BASE_ACCOUNTS if passwd is None else passwd, BASE_GROUPS if group is None else group)
        self.build_tools()
        for name in list(DEFAULT_STUBS if stubs is None else stubs) + list(extra_stubs):
            self.stub(name)
        self.stub("sudo", script=SUDO_STUB)

    ###########################################################
    # Building
    ###########################################################

    def build_root(self, passwd, group):
        for directory in self.hidden_dirs:
            os.makedirs(self.host_path(directory), exist_ok=True)
        for directory in ["/var/lib", "/var/log", "/var/tmp", "/var/www", "/usr/local/bin", "/etc/systemd/system"]:
            if any(directory == hidden or directory.startswith(hidden + "/") for hidden in self.hidden_dirs):
                os.makedirs(self.host_path(directory), exist_ok=True)
        if "/tmp" in self.hidden_dirs:
            os.chmod(self.host_path("/tmp"), 0o1777)
        if "/etc" in self.hidden_dirs:
            etc_dir = self.host_path("/etc")
            for name in HOST_ETC_FILES:
                source = os.path.join(HOST_ETC, name)
                if os.path.lexists(source):
                    os.makedirs(os.path.dirname(os.path.join(etc_dir, name)), exist_ok=True)
                    shutil.copy2(source, os.path.join(etc_dir, name), follow_symlinks=False)
            for name in HOST_ETC_DIRS:
                source = os.path.join(HOST_ETC, name)
                if os.path.isdir(source):
                    shutil.copytree(source, os.path.join(etc_dir, name), symlinks=True)
            self.write("/etc/passwd", passwd)
            self.write("/etc/group", group)
            self.write("/etc/nsswitch.conf", "passwd: files\ngroup: files\nhosts: files\n")
            self.write("/etc/hosts", "127.0.0.1 localhost\n")
            os.symlink("/proc/self/mounts", os.path.join(etc_dir, "mtab"))

    def build_tools(self):
        os.makedirs(self.stub_dir)
        os.makedirs(self.tool_dir)
        for name in self.allowed_tools:
            found = shutil.which(name)
            if found:
                os.symlink(os.path.realpath(found), os.path.join(self.tool_dir, name))

    ###########################################################
    # Arranging
    ###########################################################

    def host_path(self, path):
        """Where a path inside the sandbox lives on this machine."""
        return os.path.join(self.root_dir, path.lstrip("/"))

    def write(self, path, contents, mode=None):
        target = self.host_path(path)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(contents)
        if mode is not None:
            os.chmod(target, mode)
        return target

    def read(self, path):
        with open(self.host_path(path), encoding="utf-8") as handle:
            return handle.read()

    def exists(self, path):
        return os.path.lexists(self.host_path(path))

    def add_user(self, name, uid=1000, groups=()):
        with open(self.host_path("/etc/passwd"), "a", encoding="utf-8") as passwd:
            passwd.write(f"{name}:x:{uid}:{uid}::/home/{name}:/bin/bash\n")
        with open(self.host_path("/etc/group"), "a", encoding="utf-8") as group:
            group.write(f"{name}:x:{uid}:\n")
        if groups:
            lines = self.read("/etc/group").splitlines()
            for index, line in enumerate(lines):
                fields = line.split(":")
                if fields[0] in groups:
                    members = [member for member in fields[3].split(",") if member] + [name]
                    lines[index] = ":".join(fields[:3] + [",".join(members)])
            self.write("/etc/group", "\n".join(lines) + "\n")
        os.makedirs(self.host_path(f"/home/{name}"), exist_ok=True)

    def stub(self, name, stdout="", code=0, script=None):
        """Replace a command with one that records its arguments, prints stdout
        and exits with code, or runs script after recording when one is given."""
        stdout_file = os.path.join(self.stub_dir, f".{name}.stdout")
        with open(stdout_file, "w", encoding="utf-8") as handle:
            handle.write(stdout)
        body = script if script is not None else f'cat "{stdout_file}"\nexit {code}\n'
        path = os.path.join(self.stub_dir, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("#!/bin/bash\n")
            handle.write("{ printf '%%s\\0' \"${0##*/}\" \"$@\"; printf '\\036'; } >> \"%s\"\n" % self.calls_log)
            handle.write(body)
        os.chmod(path, 0o755)

    ###########################################################
    # Running
    ###########################################################

    def build_launcher(self, command, env):
        keep = [self.work_dir, self.repo_root]
        if self.trace_dir:
            keep += [self.trace_dir, os.path.dirname(self.hook_file)]
        lines = ["set -e", f"mount -t tmpfs tmpfs {STAGING_DIR}"]
        for index, path in enumerate(keep):
            lines.append(f"mkdir -p {STAGING_DIR}/keep{index} && mount --bind {quote(path)} {STAGING_DIR}/keep{index}")
        # keep0 is work_dir, so keep0/root is root_dir
        staged_root = f"{STAGING_DIR}/keep0/root"
        for directory in self.hidden_dirs:
            lines.append(f"mount --bind {staged_root}{directory} {directory}")
        for index, path in enumerate(keep):
            lines.append(f"mkdir -p {quote(path)} && mount --bind {STAGING_DIR}/keep{index} {quote(path)}")
        lines.append("cd /root")
        assignments = " ".join(f"{key}={quote(value)}" for key, value in env.items())
        lines.append(f"exec env -i {assignments} {' '.join(quote(part) for part in command)}")
        return "\n".join(lines) + "\n"

    def run(self, script, *args, env=None, input=None, timeout=60):
        """Run a script from the repository, by its path relative to the root."""
        return self.run_command(["bash", os.path.join(self.repo_root, script)] + [str(arg) for arg in args],
                                env=env, input=input, timeout=timeout)

    def run_command(self, command, env=None, input=None, timeout=60):
        """Run a command line inside the sandbox."""
        error = check_namespace_support()
        if error:
            message = f"user namespaces are unavailable here: {error}"
            if self.required:
                pytest.fail(message, pytrace=False)
            pytest.skip(message)
        full_env = {
            "PATH": f"{self.stub_dir}:{self.tool_dir}",
            "HOME": "/root",
            "USER": "root",
            "LANG": "C.UTF-8",
            "TERM": "dumb",
        }
        if self.trace_dir:
            full_env[HOOK_VARIABLE] = self.hook_file
            full_env[TRACE_DIR_VARIABLE] = self.trace_dir
        full_env.update(env or {})
        launcher = os.path.join(self.work_dir, f"launcher-{uuid.uuid4().hex}.sh")
        with open(launcher, "w", encoding="utf-8") as handle:
            handle.write(self.build_launcher(command, full_env))
        return subprocess.run(
            ["unshare", "--user", "--map-root-user", "--mount", "--net", "--fork", "/bin/bash", launcher],
            input=input, capture_output=True, text=True, timeout=timeout, check=False,
            env={"PATH": "/usr/bin:/bin"})

    ###########################################################
    # Reading back
    ###########################################################

    def calls(self, name=None):
        """Every stubbed command run, as argument lists, optionally of one command."""
        if not os.path.exists(self.calls_log):
            return []
        with open(self.calls_log, "rb") as handle:
            records = handle.read().split(b"\x1e")
        found = []
        for record in records:
            if not record:
                continue
            fields = [field.decode(errors="replace") for field in record.split(b"\0")[:-1]]
            if name is None or fields[0] == name:
                found.append(fields if name is None else fields[1:])
        return found

    def ran(self, name, *args):
        """Whether a stubbed command ran with these arguments, in order, among its own."""
        wanted = list(args)
        return any(call[:len(wanted)] == wanted for call in self.calls(name))

###########################################################
# Plugin
###########################################################


def pytest_addoption(parser):
    group = parser.getgroup("shell sandbox")
    group.addoption("--shell-sandbox-required", action="store_true",
                    help="fail, rather than skip, shell_sandbox tests when user namespaces are unavailable")


@pytest.fixture
def shell_sandbox_options():
    """Overridable ShellSandbox options (see the module docstring)."""
    return {}


@pytest.fixture
def shell_sandbox(request, tmp_path, shell_sandbox_options):
    trace_dir = os.environ.get(TRACE_DIR_VARIABLE) or None
    hook_file = os.environ.get(HOOK_VARIABLE) if trace_dir else None
    return ShellSandbox(
        tmp_path / "sandbox", request.config.rootpath, trace_dir=trace_dir, hook_file=hook_file,
        required=request.config.getoption("--shell-sandbox-required"), **shell_sandbox_options)

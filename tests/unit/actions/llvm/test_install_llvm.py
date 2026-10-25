"""install_llvm.py, with apt, the key download and the release file stood in for."""

import os

import pytest

from script_helpers import load_script

install_llvm = load_script("actions/llvm/install_llvm.py")

NOBLE = 'NAME="Ubuntu"\nVERSION_ID="24.04"\nVERSION_CODENAME=noble\n'


class Recorder:
    """Records each command, and copies what install puts in place so the test can read it."""

    def __init__(self):
        self.commands = []
        self.installed = {}

    def __call__(self, command, check):
        assert check
        self.commands.append(command)
        if "install" in command and "-D" in command:
            source, target = command[-2], command[-1]
            with open(source, encoding="utf-8") as staged:
                self.installed[target] = staged.read()


def run(tmp_path, monkeypatch, version="23", os_release=NOBLE, euid=1001):
    release = tmp_path / "os-release"
    release.write_text(os_release, encoding="utf-8")
    monkeypatch.setattr(os, "geteuid", lambda: euid)
    recorder = Recorder()
    downloads = []

    def download(url):
        downloads.append(url)
        return b"-----BEGIN PGP PUBLIC KEY BLOCK-----\n"

    status = install_llvm.main({"LLVM_VERSION": version}, run=recorder, download=download,
                               os_release=release)
    return status, recorder, downloads


def test_adds_the_signed_repository_for_the_release_and_version(tmp_path, monkeypatch, capsys):
    status, recorder, downloads = run(tmp_path, monkeypatch)
    assert status == 0
    assert downloads == ["https://apt.llvm.org/llvm-snapshot.gpg.key"]
    assert recorder.installed == {
        "/etc/apt/keyrings/apt.llvm.org.asc": "-----BEGIN PGP PUBLIC KEY BLOCK-----\n",
        "/etc/apt/sources.list.d/llvm-toolchain-noble-23.list":
            "deb [signed-by=/etc/apt/keyrings/apt.llvm.org.asc] https://apt.llvm.org/noble/ "
            "llvm-toolchain-noble-23 main\n",
    }
    assert "LLVM 23 for noble" in capsys.readouterr().out


def test_installs_the_versioned_toolchain_after_updating(tmp_path, monkeypatch):
    _, recorder, _ = run(tmp_path, monkeypatch)
    assert recorder.commands[-2] == ["sudo", "apt-get", "update"]
    assert recorder.commands[-1] == [
        "sudo", "apt-get", "install", "-y", "--no-install-recommends",
        "clang-23", "clang-format-23", "clang-tidy-23", "clang-tools-23", "lld-23", "llvm-23",
        "libclang-rt-23-dev", "libc++-23-dev", "libc++abi-23-dev",
    ]


def test_root_runs_without_sudo(tmp_path, monkeypatch):
    _, recorder, _ = run(tmp_path, monkeypatch, euid=0)
    assert all(command[0] != "sudo" for command in recorder.commands)


def test_a_quoted_code_name_is_read_without_its_quotes(tmp_path, monkeypatch):
    _, recorder, _ = run(tmp_path, monkeypatch, os_release='VERSION_CODENAME="bookworm"\n')
    assert "/etc/apt/sources.list.d/llvm-toolchain-bookworm-23.list" in recorder.installed


@pytest.mark.parametrize("version", ["", "latest", "23.1"])
def test_the_version_must_be_a_release_number(tmp_path, monkeypatch, capsys, version):
    status, recorder, _ = run(tmp_path, monkeypatch, version=version)
    assert status == 1 and recorder.commands == []
    assert "::error::LLVM_VERSION must be a release number" in capsys.readouterr().out


@pytest.mark.parametrize("os_release", ['NAME="Arch Linux"\n', "VERSION_CODENAME=\n"])
def test_a_release_without_a_code_name_fails(tmp_path, monkeypatch, capsys, os_release):
    status, recorder, _ = run(tmp_path, monkeypatch, os_release=os_release)
    assert status == 1 and recorder.commands == []
    assert "names no release" in capsys.readouterr().out


def test_fetch_reads_the_whole_response(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"key"

    opened = []

    def urlopen(url, timeout):
        opened.append((url, timeout))
        return Response()

    monkeypatch.setattr(install_llvm.urllib.request, "urlopen", urlopen)
    assert install_llvm.fetch("https://example.test/key") == b"key"
    assert opened == [("https://example.test/key", 60)]

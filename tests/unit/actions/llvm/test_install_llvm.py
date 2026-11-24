"""install_llvm.py, with apt, the downloads, the installer and the release file stood in for."""

import hashlib
import io
import json
import os
import tarfile
import urllib.error
from pathlib import Path

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


def run(tmp_path, monkeypatch, version="23", os_release=NOBLE, euid=1001, extra_env=None):
    release = tmp_path / "os-release"
    release.write_text(os_release, encoding="utf-8")
    monkeypatch.setattr(os, "geteuid", lambda: euid)
    recorder = Recorder()
    downloads = []

    def download(url):
        downloads.append(url)
        return b"-----BEGIN PGP PUBLIC KEY BLOCK-----\n"

    status = install_llvm.main({"LLVM_VERSION": version, **(extra_env or {})}, run=recorder,
                               download=download, os_release=release)
    return status, recorder, downloads


def test_on_path_puts_the_release_bin_first_for_later_steps(tmp_path, monkeypatch, capsys):
    github_path = tmp_path / "github_path"
    github_path.write_text("/earlier\n", encoding="utf-8")
    status, _, _ = run(tmp_path, monkeypatch, extra_env={"LLVM_ON_PATH": "true", "GITHUB_PATH": str(github_path)})
    assert status == 0
    assert github_path.read_text(encoding="utf-8") == "/earlier\n/usr/lib/llvm-23/bin\n"
    assert "/usr/lib/llvm-23/bin is first on PATH" in capsys.readouterr().out


def test_without_on_path_nothing_is_added_to_path(tmp_path, monkeypatch, capsys):
    run(tmp_path, monkeypatch, extra_env={"LLVM_ON_PATH": "false", "GITHUB_PATH": str(tmp_path / "unused")})
    assert not (tmp_path / "unused").exists()
    assert "first on PATH" not in capsys.readouterr().out


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

    def urlopen(request, timeout):
        opened.append((request.full_url, dict(request.header_items()), timeout))
        return Response()

    monkeypatch.setattr(install_llvm.urllib.request, "urlopen", urlopen)
    assert install_llvm.fetch("https://example.test/key") == b"key"
    assert install_llvm.fetch("https://example.test/api", {"Accept": "json"}) == b"key"
    assert opened == [("https://example.test/key", {}, 300), ("https://example.test/api", {"Accept": "json"}, 300)]


def test_save_writes_the_body_in_pieces_and_gives_its_sha256(monkeypatch, tmp_path):
    class Response:
        def __init__(self):
            self.pieces = [b"abc", b"def", b""]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size):
            assert size == 1 << 20
            return self.pieces.pop(0)

    monkeypatch.setattr(install_llvm.urllib.request, "urlopen", lambda url, timeout: Response())
    out = tmp_path / "archive"
    assert install_llvm.save("https://example.test/archive", out) == hashlib.sha256(b"abcdef").hexdigest()
    assert out.read_bytes() == b"abcdef"


# Windows: LLVM's tags and releases as GitHub's API lists them, and the archive they point at.

REFS_URL = "https://api.github.com/repos/llvm/llvm-project/git/matching-refs/tags/llvmorg-23."
RELEASE_URL = "https://api.github.com/repos/llvm/llvm-project/releases/tags/llvmorg-{}"
DOWNLOAD_URL = "https://github.com/llvm/llvm-project/releases/download/llvmorg-{0}/{1}"


def archive_name(point, arch="x86_64"):
    return f"clang+llvm-{point}-{arch}-pc-windows-msvc.tar.xz"


def archive_bytes(top, member="bin/clang-cl.exe"):
    """A .tar.xz holding one file at top/member, as LLVM's archives hold bin under one folder."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:xz") as archive:
        info = tarfile.TarInfo(f"{top}/{member}")
        info.size = 2
        archive.addfile(info, io.BytesIO(b"MZ"))
    return buffer.getvalue()


def tags(*names):
    return json.dumps([{"ref": f"refs/tags/llvmorg-{name}"} for name in names]).encode()


def release(point, arch="x86_64", body=None, digest=None):
    """The release's API listing, its archive's digest that of body (the archive as it should be)."""
    name = archive_name(point, arch)
    body = archive_bytes(name.removesuffix(".tar.xz")) if body is None else body
    asset = {"name": name, "browser_download_url": DOWNLOAD_URL.format(point, name),
             "digest": "sha256:" + hashlib.sha256(body).hexdigest() if digest is None else digest}
    return json.dumps({"assets": [{"name": "LLVM-src.tar.xz"}, asset]}).encode()


def missing(url):
    return urllib.error.HTTPError(url, 404, "Not Found", None, None)


def run_windows(tmp_path, answers, archives=None, arch="X64", token="secret"):
    """main on a Windows runner; answers maps each API URL to its body or an exception, archives each archive URL."""
    requests, saved = [], []
    temp = tmp_path / "temp"
    github_path = tmp_path / "github_path"

    def download(url, headers=None):
        requests.append((url, headers))
        answer = answers[url]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def saver(url, path):
        saved.append(url)
        Path(path).write_bytes(archives[url])
        return hashlib.sha256(archives[url]).hexdigest()

    env = {"LLVM_VERSION": "23", "RUNNER_OS": "Windows", "RUNNER_ARCH": arch, "RUNNER_TEMP": str(temp),
           "GITHUB_PATH": str(github_path), "GITHUB_TOKEN": token}
    status = install_llvm.main(env, run=None, download=download, saver=saver)
    return status, requests, saved, github_path


def good_archive(point, arch="x86_64"):
    name = archive_name(point, arch)
    return {DOWNLOAD_URL.format(point, name): archive_bytes(name.removesuffix(".tar.xz"))}


def test_windows_unpacks_the_newest_point_release_and_puts_its_bin_first(tmp_path, capsys):
    answers = {REFS_URL: tags("23.1.0-rc1", "23.1.0", "23.1.10", "23.1.9", "230.1.0"),
               RELEASE_URL.format("23.1.10"): release("23.1.10")}
    status, requests, _, github_path = run_windows(tmp_path, answers, good_archive("23.1.10"))
    assert status == 0
    assert requests[0] == (REFS_URL, {"Accept": "application/vnd.github+json", "Authorization": "Bearer secret"})
    target = tmp_path / "temp" / "llvm-23"
    bin_dir = target / "clang+llvm-23.1.10-x86_64-pc-windows-msvc" / "bin"
    assert (bin_dir / "clang-cl.exe").read_bytes() == b"MZ"
    assert not (target / archive_name("23.1.10")).exists()
    assert github_path.read_text(encoding="utf-8") == f"{bin_dir}\n"
    assert "LLVM 23.1.10 (x86_64)" in capsys.readouterr().out


def test_windows_passes_over_releases_without_an_archive_yet(tmp_path):
    answers = {REFS_URL: tags("23.1.2", "23.1.1", "23.1.0"),
               RELEASE_URL.format("23.1.2"): missing(RELEASE_URL.format("23.1.2")),
               RELEASE_URL.format("23.1.1"): release("23.1.1", arch="aarch64"),
               RELEASE_URL.format("23.1.0"): release("23.1.0")}
    status, _, saved, _ = run_windows(tmp_path, answers, good_archive("23.1.0"))
    assert status == 0 and saved == [DOWNLOAD_URL.format("23.1.0", archive_name("23.1.0"))]


def test_windows_on_arm_takes_the_aarch64_archive_without_a_token(tmp_path):
    answers = {REFS_URL: tags("23.1.0"), RELEASE_URL.format("23.1.0"): release("23.1.0", arch="aarch64")}
    status, requests, saved, _ = run_windows(tmp_path, answers, good_archive("23.1.0", "aarch64"),
                                             arch="ARM64", token="")
    assert status == 0 and saved[0].endswith(archive_name("23.1.0", "aarch64"))
    assert requests[0][1] == {"Accept": "application/vnd.github+json"}


def test_windows_fails_on_a_runner_llvm_has_no_archive_for(tmp_path, capsys):
    status, requests, _, _ = run_windows(tmp_path, {}, arch="X86")
    assert status == 1 and requests == []
    assert "no Windows archive for 'X86' runners" in capsys.readouterr().out


def test_windows_fails_when_no_release_has_an_archive(tmp_path, capsys):
    answers = {REFS_URL: tags("23.1.0-rc2", "23.1.0"), RELEASE_URL.format("23.1.0"): release("23.1.0", arch="aarch64")}
    status, _, saved, _ = run_windows(tmp_path, answers)
    assert status == 1 and saved == []
    assert "no LLVM 23.x.y release has a x86_64 Windows archive" in capsys.readouterr().out


def test_windows_raises_an_api_failure_other_than_a_missing_release(tmp_path):
    error = urllib.error.HTTPError(RELEASE_URL.format("23.1.0"), 403, "rate limited", None, None)
    with pytest.raises(urllib.error.HTTPError):
        run_windows(tmp_path, {REFS_URL: tags("23.1.0"), RELEASE_URL.format("23.1.0"): error})


@pytest.mark.parametrize("digest", ["", "sha512:abc"])
def test_windows_refuses_an_archive_without_a_sha256(tmp_path, capsys, digest):
    answers = {REFS_URL: tags("23.1.0"), RELEASE_URL.format("23.1.0"): release("23.1.0", digest=digest)}
    status, _, saved, _ = run_windows(tmp_path, answers)
    assert status == 1 and saved == []
    assert f"GitHub gives no sha256 for {archive_name('23.1.0')}" in capsys.readouterr().out


def test_windows_refuses_an_archive_that_does_not_match_its_sha256(tmp_path, capsys):
    answers = {REFS_URL: tags("23.1.0"), RELEASE_URL.format("23.1.0"): release("23.1.0")}
    archives = {DOWNLOAD_URL.format("23.1.0", archive_name("23.1.0")): b"something else"}
    status, _, _, github_path = run_windows(tmp_path, answers, archives)
    assert status == 1 and not github_path.exists()
    assert list((tmp_path / "temp" / "llvm-23").iterdir()) == []
    assert "does not match the sha256 GitHub gives for it" in capsys.readouterr().out


def test_windows_refuses_an_archive_without_its_bin(tmp_path, capsys):
    body = archive_bytes("somewhere-else")
    answers = {REFS_URL: tags("23.1.0"), RELEASE_URL.format("23.1.0"): release("23.1.0", body=body)}
    archives = {DOWNLOAD_URL.format("23.1.0", archive_name("23.1.0")): body}
    status, _, _, github_path = run_windows(tmp_path, answers, archives)
    assert status == 1 and not github_path.exists() and not (tmp_path / "temp" / "llvm-23").exists()
    assert "holds no clang+llvm-23.1.0-x86_64-pc-windows-msvc" in capsys.readouterr().out

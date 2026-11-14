"""install_cmake.py, with Kitware's release downloads stood in for by archives built here."""

import hashlib
import io
import runpy
import sys
import tarfile

import pytest

from script_helpers import ROOT, load_script

install_cmake = load_script("actions/cmake/install_cmake.py")

BASE = "https://github.com/Kitware/CMake/releases/download/v3.28.3"


def release_archive(top, contents):
    """A .tar.gz holding top/contents/bin/cmake, as Kitware's archives lay it out."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        data = b"#!/bin/sh\n"
        info = tarfile.TarInfo("/".join(part for part in (top, contents, "bin", "cmake") if part))
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def release(name, contents="", digest=None):
    """The downloads one release serves: its SHA-256 list and the archive name."""
    data = release_archive(name.removesuffix(".tar.gz"), contents)
    listing = (f"{'0' * 64}  cmake-3.28.3-windows-x86_64.zip\n"
               f"{digest or hashlib.sha256(data).hexdigest()}  {name}\n")
    return {f"{BASE}/cmake-3.28.3-SHA-256.txt": listing.encode(), f"{BASE}/{name}": data}


def run(tmp_path, downloads, version="3.28.3", system="Linux", machine="x86_64"):
    env = {"CMAKE_VERSION": version, "RUNNER_TEMP": str(tmp_path / "temp"),
           "GITHUB_PATH": str(tmp_path / "github_path")}
    requested = []

    def download(url):
        requested.append(url)
        return downloads[url]

    status = install_cmake.main(env, download=download, system=system, machine=machine)
    return status, requested, env


@pytest.mark.parametrize("system, machine, name, contents", [
    ("Linux", "x86_64", "cmake-3.28.3-linux-x86_64.tar.gz", ""),
    ("Linux", "arm64", "cmake-3.28.3-linux-aarch64.tar.gz", ""),
    ("Darwin", "arm64", "cmake-3.28.3-macos-universal.tar.gz", "CMake.app/Contents"),
])
def test_unpacks_the_checked_archive_and_puts_its_bin_on_path(tmp_path, capsys, system, machine, name, contents):
    status, requested, env = run(tmp_path, release(name, contents), system=system, machine=machine)
    bin_dir = tmp_path / "temp" / "cmake" / name.removesuffix(".tar.gz") / contents / "bin"
    assert status == 0
    assert requested == [f"{BASE}/cmake-3.28.3-SHA-256.txt", f"{BASE}/{name}"]
    assert (bin_dir / "cmake").read_bytes() == b"#!/bin/sh\n"
    assert not (tmp_path / "temp" / "cmake" / name).exists()
    assert (tmp_path / "github_path").read_text(encoding="utf-8") == f"{bin_dir}\n"
    assert capsys.readouterr().out == f"CMake 3.28.3 in {bin_dir}\n"
    assert env["CMAKE_VERSION"] == "3.28.3"


def test_an_archive_that_does_not_match_its_digest_is_refused(tmp_path, capsys):
    name = "cmake-3.28.3-linux-x86_64.tar.gz"
    status, _, _ = run(tmp_path, release(name, digest="f" * 64))
    assert status == 1
    assert capsys.readouterr().out == f"::error::{name} does not match its sha256 in the release's list\n"
    assert not (tmp_path / "github_path").exists()


@pytest.mark.parametrize("version, system, machine, message", [
    ("3.28", "Linux", "x86_64", "CMAKE_VERSION must be a release such as 3.28.3, not '3.28'"),
    ("3.28.3", "Linux", "riscv64", "CMake publishes no archive for Linux riscv64"),
    ("3.28.3", "Windows", "AMD64", "CMake publishes no archive for Windows AMD64"),
])
def test_refuses_a_version_or_machine_it_cannot_serve(tmp_path, capsys, version, system, machine, message):
    status, requested, _ = run(tmp_path, {}, version=version, system=system, machine=machine)
    assert status == 1
    assert requested == []
    assert capsys.readouterr().out == f"::error::{message}\n"


def test_a_release_whose_list_lacks_the_archive_is_refused(tmp_path, capsys):
    downloads = {f"{BASE}/cmake-3.28.3-SHA-256.txt": b"0000  cmake-3.28.3-linux-aarch64.tar.gz\n"}
    status, _, _ = run(tmp_path, downloads)
    assert status == 1
    assert capsys.readouterr().out == "::error::the release's SHA-256 list has no cmake-3.28.3-linux-x86_64.tar.gz\n"


def test_reads_the_environment_when_run_as_a_script(monkeypatch, capsys):
    monkeypatch.setenv("CMAKE_VERSION", "latest")
    monkeypatch.setattr(sys, "argv", ["install_cmake.py"])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(str(ROOT / "actions" / "cmake" / "install_cmake.py"), run_name="__main__")
    assert exit_info.value.code == 1
    assert "CMAKE_VERSION must be a release" in capsys.readouterr().out


def test_fetch_reads_the_whole_response(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"archive"

    opened = []

    def urlopen(url, timeout):
        opened.append((url, timeout))
        return Response()

    monkeypatch.setattr(install_cmake.urllib.request, "urlopen", urlopen)
    assert install_cmake.fetch(f"{BASE}/x.tar.gz") == b"archive"
    assert opened == [(f"{BASE}/x.tar.gz", 300)]

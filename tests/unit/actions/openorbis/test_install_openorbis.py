"""install_openorbis.py, with the release download stood in for by a small archive of the layout."""

import hashlib
import io
import tarfile

import pytest

from script_helpers import ROOT, load_script

install_openorbis = load_script("actions/openorbis/install_openorbis.py")

BROKEN = ("typedef unsigned int mode_t;\n#define __DEFINED_mode_t\n"
          "typedef unsigned short mode_t;\n#define __DEFINED_mode_t\n")
FIXED = BROKEN.replace("unsigned int", "unsigned short")


def archive(path, alltypes=BROKEN, crt1=True):
    """A gzipped tarball shaped like an OpenOrbis release, at path; its sha256."""
    members = {"include/bits/alltypes.h": alltypes.encode(), "bin/linux/create-fself": b"#!"}
    if crt1:
        members["lib/crt1.o"] = b"\x7fELF"
    with tarfile.open(path, "w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(f"OpenOrbis/PS4Toolchain/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(tmp_path, monkeypatch, version="0.5.4", pinned=None, **layout):
    """main() against a fake release; its status, the files it wrote to and the URLs it fetched."""
    built = tmp_path / "release.tar.gz"
    digest = archive(built, **layout)
    monkeypatch.setitem(install_openorbis.RELEASES, "0.5.4",
                        install_openorbis.Release("https://example.test/oo.tar.gz", pinned or digest, "18"))
    fetched = []

    def fetch(url, path):
        fetched.append(url)
        path.write_bytes(built.read_bytes())
        return digest

    github = {name: tmp_path / name for name in ("env", "path", "output")}
    for file in github.values():
        file.write_text("", encoding="utf-8")
    env = {"OPENORBIS_VERSION": version, "OPENORBIS_DIR": str(tmp_path / "openorbis"),
           "CMAKE_FILES": str(ROOT / "actions" / "openorbis" / "cmake"),
           "GITHUB_ENV": str(github["env"]), "GITHUB_PATH": str(github["path"]),
           "GITHUB_OUTPUT": str(github["output"])}
    status = install_openorbis.main(env, fetch=fetch)
    return status, {name: file.read_text(encoding="utf-8") for name, file in github.items()}, fetched


def test_installs_the_release_and_exports_it(tmp_path, monkeypatch, capsys):
    status, written, fetched = run(tmp_path, monkeypatch)
    root = tmp_path / "openorbis" / "0.5.4"
    sdk = root / "OpenOrbis" / "PS4Toolchain"
    assert status == 0 and fetched == ["https://example.test/oo.tar.gz"]
    assert written["env"].splitlines() == [
        f"OO_PS4_TOOLCHAIN={sdk}", "OPENORBIS_LLVM_VERSION=18",
        f"OPENORBIS_TOOLCHAIN_FILE={root / 'cmake' / 'OpenOrbisToolchain.cmake'}"]
    assert written["path"] == f"{sdk / 'bin' / 'linux'}\n"
    assert written["output"] == "llvm-version=18\n"
    assert {file.name for file in (root / "cmake").iterdir()} == {
        "OpenOrbisToolchain.cmake", "OpenOrbis.cmake", "OpenOrbisPackage.cmake"}
    assert not (root / "toolchain.tar.gz").exists()
    assert "OpenOrbis 0.5.4 in" in capsys.readouterr().out


def test_fixes_the_32_bit_mode_t(tmp_path, monkeypatch, capsys):
    run(tmp_path, monkeypatch)
    header = tmp_path / "openorbis/0.5.4/OpenOrbis/PS4Toolchain/include/bits/alltypes.h"
    assert header.read_text(encoding="utf-8") == FIXED
    assert "mode_t is 16 bits" in capsys.readouterr().out


def test_a_correct_header_is_left_alone(tmp_path, monkeypatch, capsys):
    status, _, _ = run(tmp_path, monkeypatch, alltypes=FIXED)
    assert status == 0 and "mode_t is 16 bits" not in capsys.readouterr().out


@pytest.mark.parametrize("text", [FIXED, "typedef unsigned short mode_t;\n", "", "typedef int mode_t;\n"])
def test_fix_mode_t_changes_only_a_leading_32_bit_declaration(text):
    assert install_openorbis.fix_mode_t(text) == (text, False)


def test_a_v_prefix_names_the_same_release(tmp_path, monkeypatch):
    status, _, _ = run(tmp_path, monkeypatch, version=" v0.5.4 ")
    assert status == 0


def test_an_earlier_install_is_replaced(tmp_path, monkeypatch):
    stale = tmp_path / "openorbis" / "0.5.4" / "stale"
    stale.mkdir(parents=True)
    status, _, _ = run(tmp_path, monkeypatch)
    assert status == 0 and not stale.exists()


@pytest.mark.parametrize("version", ["", "0.5.3", "latest"])
def test_a_release_not_pinned_here_fails(tmp_path, monkeypatch, capsys, version):
    status, written, fetched = run(tmp_path, monkeypatch, version=version)
    assert status == 1 and fetched == [] and written["env"] == ""
    assert f"::error::OpenOrbis {version!r} isn't pinned here; pinned: 0.5.4" in capsys.readouterr().out


def test_a_download_not_matching_the_pin_fails(tmp_path, monkeypatch, capsys):
    status, written, _ = run(tmp_path, monkeypatch, pinned="0" * 64)
    assert status == 1 and written["env"] == ""
    assert f"not the pinned {'0' * 64}" in capsys.readouterr().out
    assert not (tmp_path / "openorbis" / "0.5.4" / "OpenOrbis").exists()


def test_an_archive_without_the_runtime_fails(tmp_path, monkeypatch, capsys):
    status, written, _ = run(tmp_path, monkeypatch, crt1=False)
    assert status == 1 and written["env"] == ""
    assert "is not an OpenOrbis SDK" in capsys.readouterr().out


def test_download_saves_the_body_in_chunks_and_hashes_it(tmp_path, monkeypatch):
    body = b"x" * ((1 << 20) + 5)
    opened = []

    def urlopen(url, timeout):
        opened.append((url, timeout))
        return io.BytesIO(body)

    monkeypatch.setattr(install_openorbis.urllib.request, "urlopen", urlopen)
    target = tmp_path / "download"
    assert install_openorbis.download("https://example.test/oo", target) == hashlib.sha256(body).hexdigest()
    assert target.read_bytes() == body and opened == [("https://example.test/oo", 60)]

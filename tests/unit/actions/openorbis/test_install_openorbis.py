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


def tarball(members, mode="w:gz"):
    """A tarball of members, a {name: bytes} mapping, as bytes."""
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode=mode) as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return out.getvalue()


def archive(alltypes=BROKEN, crt1=True):
    """A release shaped like OpenOrbis's, as bytes."""
    members = {"include/bits/alltypes.h": alltypes.encode(), "bin/linux/PkgTool.Core": b"ELF",
               **{f"bin/linux/{tool}": b"ELF" for tool in install_openorbis.TOOLS}}
    if crt1:
        members["lib/crt1.o"] = b"\x7fELF"
    return tarball({f"OpenOrbis/PS4Toolchain/{name}": data for name, data in members.items()})


def ar_member(name, data):
    """One ar archive member: its 60-byte header, its data, and padding to an even length."""
    header = f"{name + '/':<16}{0:<12}{0:<6}{0:<6}{'100644':<8}{len(data):<10}`\n".encode()
    return header + data + (b"\n" if len(data) % 2 else b"")


def deb(libraries=("libssl.so.1.1", "libcrypto.so.1.1")):
    """A .deb shaped like libssl1.1's, as bytes, with an odd-sized member before its data."""
    data = {f"./usr/lib/x86_64-linux-gnu/{name}": name.encode() for name in libraries}
    data["./usr/lib/x86_64-linux-gnu/engines-1.1/afalg.so"] = b"engine"
    data["./usr/share/doc/libssl1.1/copyright"] = b"OpenSSL"
    return (b"!<arch>\n" + ar_member("debian-binary", b"2.0\n") + ar_member("control.tar.xz", b"odd")
            + ar_member("data.tar.xz", tarball(data, "w:xz")))


def run(tmp_path, monkeypatch, version="0.5.4", pinned=None, libssl=None, **layout):
    """main() against a fake release and libssl; its status, the files it wrote and the URLs it fetched."""
    bodies = {"https://example.test/oo.tar.gz": archive(**layout), "http://example.test/libssl.deb": libssl or deb()}
    digests = {url: hashlib.sha256(body).hexdigest() for url, body in bodies.items()}
    monkeypatch.setitem(install_openorbis.RELEASES, "0.5.4", install_openorbis.Release(
        "https://example.test/oo.tar.gz", pinned or digests["https://example.test/oo.tar.gz"], "18"))
    monkeypatch.setattr(install_openorbis, "LIBSSL", install_openorbis.Download(
        "http://example.test/libssl.deb", digests["http://example.test/libssl.deb"]))
    fetched = []

    def fetch(url, path):
        fetched.append(url)
        path.write_bytes(bodies[url])
        return digests[url]

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
    assert status == 0 and fetched == ["https://example.test/oo.tar.gz", "http://example.test/libssl.deb"]
    assert written["env"].splitlines() == [
        f"OO_PS4_TOOLCHAIN={sdk}", "OPENORBIS_LLVM_VERSION=18",
        f"OPENORBIS_TOOLCHAIN_FILE={root / 'cmake' / 'OpenOrbisToolchain.cmake'}", f"OPENORBIS_BIN={root / 'bin'}"]
    assert written["path"] == f"{root / 'bin'}\n"
    assert written["output"] == "llvm-version=18\n"
    assert {file.name for file in (root / "cmake").iterdir()} == {
        "OpenOrbisToolchain.cmake", "OpenOrbis.cmake", "OpenOrbisPackage.cmake"}
    assert not (root / "toolchain.tar.gz").exists() and not (root / "libssl.deb").exists()
    assert "OpenOrbis 0.5.4 in" in capsys.readouterr().out


def test_the_tools_are_linked_and_pkgtool_gets_openssl_1_1(tmp_path, monkeypatch):
    run(tmp_path, monkeypatch)
    root = tmp_path / "openorbis" / "0.5.4"
    tools = root / "OpenOrbis" / "PS4Toolchain" / "bin" / "linux"
    assert {path.name: path.resolve() for path in (root / "bin").iterdir() if path.is_symlink()} == {
        tool: tools / tool for tool in ("create-fself", "create-gp4", "readoelf")}
    wrapper = root / "bin" / "PkgTool.Core"
    assert wrapper.stat().st_mode & 0o777 == 0o755
    text = wrapper.read_text(encoding="utf-8")
    assert f'export LD_LIBRARY_PATH="{root / "libssl"}${{LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}}"' in text
    assert "DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1" in text and f'exec "{tools / "PkgTool.Core"}" "$@"' in text
    assert sorted(path.name for path in (root / "libssl").iterdir()) == ["libcrypto.so.1.1", "libssl.so.1.1"]
    assert (root / "libssl" / "libssl.so.1.1").read_bytes() == b"libssl.so.1.1"


def test_a_libssl_not_matching_the_pin_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(install_openorbis, "fetch_checked", fail_second(install_openorbis.fetch_checked))
    status, written, _ = run(tmp_path, monkeypatch)
    assert status == 1 and written["env"] == "" and "not the pinned" in capsys.readouterr().out


def fail_second(fetch_checked):
    """fetch_checked, failing its second call (libssl's) as a pin mismatch would."""
    calls = []

    def checked(pinned, path, fetch):
        calls.append(pinned)
        if len(calls) == 2:
            raise install_openorbis.InstallError(f"{pinned.url} has sha256 x, not the pinned {pinned.sha256}")
        return fetch_checked(pinned, path, fetch)
    return checked


@pytest.mark.parametrize("libssl", [b"not a deb", b"!<arch>\n" + ar_member("control.tar.xz", b"control")])
def test_a_libssl_without_a_data_archive_fails(tmp_path, monkeypatch, capsys, libssl):
    status, _, _ = run(tmp_path, monkeypatch, libssl=libssl)
    assert status == 1 and "is not a .deb with a data archive" in capsys.readouterr().out


def test_a_libssl_without_the_libraries_fails(tmp_path, monkeypatch, capsys):
    status, _, _ = run(tmp_path, monkeypatch, libssl=deb(libraries=()))
    assert status == 1 and "holds no libssl or libcrypto" in capsys.readouterr().out


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

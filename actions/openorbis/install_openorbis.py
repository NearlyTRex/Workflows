"""Install a pinned OpenOrbis PS4 Toolchain release, and the CMake files that build with it.

Reads OPENORBIS_VERSION, OPENORBIS_DIR and CMAKE_FILES from the environment and writes to
$GITHUB_ENV, $GITHUB_PATH and $GITHUB_OUTPUT; for Linux runners.

OPENORBIS_VERSION=V  download release V, check it against the sha256 pinned in RELEASES, and
                     unpack it in OPENORBIS_DIR/V, fixing the header fix_mode_t describes. The
                     CMake files in CMAKE_FILES are copied beside it. Later steps get
                     OO_PS4_TOOLCHAIN, OPENORBIS_TOOLCHAIN_FILE and OPENORBIS_LLVM_VERSION, and
                     the SDK's tools on PATH, from OPENORBIS_BIN: create-fself, create-gp4,
                     readoelf, and PkgTool.Core with the OpenSSL 1.1 LIBSSL holds.
                     The LLVM release the SDK was built with is the llvm-version output.
"""

import hashlib
import io
import os
import re
import shutil
import sys
import tarfile
import urllib.request
from collections import namedtuple
from pathlib import Path

Release = namedtuple("Release", "url sha256 llvm")
Download = namedtuple("Download", "url sha256")

# A release added here needs the sha256 GitHub lists for its asset, and the LLVM
# release its asset's name says it was built with.
RELEASES = {
    "0.5.4": Release(
        "https://github.com/OpenOrbis/OpenOrbis-PS4-Toolchain/releases/download/v0.5.4/"
        "toolchain-llvm-18.tar.gz",
        "3c7cd5bb593ca74fa1c13fd59f3938dc0fc07985167f7275063019e63abe4526",
        "18",
    ),
}
# PkgTool.Core is a .NET Core 3.0 program, which loads OpenSSL 1.0 or 1.1, and
# Ubuntu has shipped only OpenSSL 3 since 22.04. This is 20.04's last libssl1.1,
# unpacked for PkgTool alone. The archive serves no HTTPS; the sha256 is
# focal-updates' own, as apt would check it.
LIBSSL = Download(
    "http://archive.ubuntu.com/ubuntu/pool/main/o/openssl/libssl1.1_1.1.1f-1ubuntu2.24_amd64.deb",
    "7cf39d70a639017d1dd7c8d36daa2258063608688e449fddf40ffdd46f992a78",
)
SDK = Path("OpenOrbis", "PS4Toolchain")
TOOLS = ("create-fself", "create-gp4", "readoelf")
MODE_T = re.compile(r"typedef unsigned (int|short) mode_t;")
LIBRARY = re.compile(r"/lib(ssl|crypto)\.so\.[\d.]+$")
PKGTOOL = """#!/usr/bin/env bash
# The OpenOrbis SDK's PkgTool.Core, with the OpenSSL 1.1 its .NET Core 3.0 needs,
# and without the ICU it would otherwise look for.
export LD_LIBRARY_PATH="{lib}${{LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}}"
export DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1
exec "{real}" "$@"
"""


class InstallError(Exception):
    """A download or archive that won't do, with what to print."""


def download(url, path):
    """Save the body at url to path, waiting up to a minute for each read; its sha256."""
    digest = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=60) as response, open(path, "wb") as out:
        while chunk := response.read(1 << 20):
            digest.update(chunk)
            out.write(chunk)
    return digest.hexdigest()


def fetch_checked(pinned, path, fetch):
    """Download pinned to path, failing unless it has the pinned sha256."""
    digest = fetch(pinned.url, path)
    if digest != pinned.sha256:
        raise InstallError(f"{pinned.url} has sha256 {digest}, not the pinned {pinned.sha256}")


def fix_mode_t(text):
    """alltypes.h with the 16-bit mode_t libkernel and the SDK's libc++ use, and whether it changed."""
    # v0.5.4 declares mode_t twice, and the first, 32-bit one wins, which moves
    # every struct stat field after st_mode by 8 bytes.
    if [match.group(1) for match in MODE_T.finditer(text)][:1] != ["int"]:
        return text, False
    return text.replace("typedef unsigned int mode_t;", "typedef unsigned short mode_t;"), True


def unpack(release, root, fetch):
    """Download, check and unpack release in root, fixing its header; the SDK's directory."""
    archive = root / "toolchain.tar.gz"
    fetch_checked(release, archive, fetch)
    with tarfile.open(archive) as tar:
        tar.extractall(root, filter="data")
    archive.unlink()
    sdk = root / SDK
    if not (sdk / "lib" / "crt1.o").is_file():
        raise InstallError(f"{release.url} has no {SDK}/lib/crt1.o, so it is not an OpenOrbis SDK")
    header = sdk / "include" / "bits" / "alltypes.h"
    text, fixed = fix_mode_t(header.read_text(encoding="utf-8"))
    if fixed:
        header.write_text(text, encoding="utf-8")
        print(f"Fixed {header.relative_to(root)}: mode_t is 16 bits, as libkernel has it")
    return sdk


def deb_data(deb):
    """The data archive inside a .deb, an ar archive of control and data tarballs."""
    data = deb.read_bytes()
    offset = 8 if data.startswith(b"!<arch>\n") else len(data)
    while offset + 60 <= len(data):
        name, size = data[offset:offset + 16].decode().strip().rstrip("/"), int(data[offset + 48:offset + 58])
        if name.startswith("data.tar"):
            return io.BytesIO(data[offset + 60:offset + 60 + size])
        offset += 60 + size + size % 2
    raise InstallError(f"{deb} is not a .deb with a data archive")


def install_libssl(root, fetch):
    """Download LIBSSL and unpack libssl and libcrypto from it into root/libssl; that directory."""
    deb, lib = root / "libssl.deb", root / "libssl"
    fetch_checked(LIBSSL, deb, fetch)
    lib.mkdir()
    with tarfile.open(fileobj=deb_data(deb)) as tar:
        for member in tar.getmembers():
            if member.isfile() and LIBRARY.search(member.name):
                (lib / Path(member.name).name).write_bytes(tar.extractfile(member).read())
    deb.unlink()
    if not list(lib.iterdir()):
        raise InstallError(f"{LIBSSL.url} holds no libssl or libcrypto")
    return lib


def link_tools(sdk, root, lib):
    """root/bin, with the SDK's tools linked in and PkgTool.Core wrapped to load lib."""
    tools = sdk / "bin" / "linux"
    bin_dir = root / "bin"
    bin_dir.mkdir()
    for tool in TOOLS:
        (bin_dir / tool).symlink_to(tools / tool)
    wrapper = bin_dir / "PkgTool.Core"
    wrapper.write_text(PKGTOOL.format(lib=lib, real=tools / "PkgTool.Core"), encoding="utf-8")
    wrapper.chmod(0o755)
    return bin_dir


def append(env, variable, lines):
    """Add lines to the file the runner names in env[variable], such as GITHUB_ENV."""
    with open(env[variable], "a", encoding="utf-8") as out:
        out.writelines(f"{line}\n" for line in lines)


def main(env=None, fetch=download):
    """Install the release and export it; 1 when the version isn't pinned or a download won't do."""
    env = os.environ if env is None else env
    version = env.get("OPENORBIS_VERSION", "").strip().removeprefix("v")
    release = RELEASES.get(version)
    if release is None:
        print(f"::error::OpenOrbis {version!r} isn't pinned here; pinned: {', '.join(RELEASES)}")
        return 1
    root = Path(env["OPENORBIS_DIR"], version)
    # A self-hosted runner keeps its temp directory between jobs.
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    try:
        sdk = unpack(release, root, fetch)
        bin_dir = link_tools(sdk, root, install_libssl(root, fetch))
    except InstallError as error:
        print(f"::error::{error}")
        return 1
    cmake = shutil.copytree(env["CMAKE_FILES"], root / "cmake")
    append(env, "GITHUB_ENV", [f"OO_PS4_TOOLCHAIN={sdk}", f"OPENORBIS_LLVM_VERSION={release.llvm}",
                               f"OPENORBIS_TOOLCHAIN_FILE={cmake / 'OpenOrbisToolchain.cmake'}",
                               f"OPENORBIS_BIN={bin_dir}"])
    append(env, "GITHUB_PATH", [str(bin_dir)])
    append(env, "GITHUB_OUTPUT", [f"llvm-version={release.llvm}"])
    print(f"OpenOrbis {version} in {sdk}, for LLVM {release.llvm}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

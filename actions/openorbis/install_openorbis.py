"""Install a pinned OpenOrbis PS4 Toolchain release, and the CMake files that build with it.

Reads OPENORBIS_VERSION, OPENORBIS_DIR and CMAKE_FILES from the environment and writes to
$GITHUB_ENV, $GITHUB_PATH and $GITHUB_OUTPUT; for Linux runners.

OPENORBIS_VERSION=V  download release V, check it against the sha256 pinned in RELEASES, and
                     unpack it in OPENORBIS_DIR/V, fixing the header fix_mode_t describes. The
                     CMake files in CMAKE_FILES are copied beside it. Later steps get
                     OO_PS4_TOOLCHAIN, OPENORBIS_TOOLCHAIN_FILE and OPENORBIS_LLVM_VERSION, and
                     the SDK's tools on PATH: create-fself, create-gp4, PkgTool.Core and readoelf.
                     The LLVM release the SDK was built with is the llvm-version output.
"""

import hashlib
import os
import re
import shutil
import sys
import tarfile
import urllib.request
from collections import namedtuple
from pathlib import Path

Release = namedtuple("Release", "url sha256 llvm")

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
SDK = Path("OpenOrbis", "PS4Toolchain")
MODE_T = re.compile(r"typedef unsigned (int|short) mode_t;")


def download(url, path):
    """Save the body at url to path, waiting up to a minute for each read; its sha256."""
    digest = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=60) as response, open(path, "wb") as out:
        while chunk := response.read(1 << 20):
            digest.update(chunk)
            out.write(chunk)
    return digest.hexdigest()


def fix_mode_t(text):
    """alltypes.h with the 16-bit mode_t libkernel and the SDK's libc++ use, and whether it changed."""
    # v0.5.4 declares mode_t twice, and the first, 32-bit one wins, which moves
    # every struct stat field after st_mode by 8 bytes.
    if [match.group(1) for match in MODE_T.finditer(text)][:1] != ["int"]:
        return text, False
    return text.replace("typedef unsigned int mode_t;", "typedef unsigned short mode_t;"), True


def unpack(release, root, fetch):
    """Download, check and unpack release in root; the SDK's directory, or an error message."""
    archive = root / "toolchain.tar.gz"
    digest = fetch(release.url, archive)
    if digest != release.sha256:
        return None, f"{release.url} has sha256 {digest}, not the pinned {release.sha256}"
    with tarfile.open(archive) as tar:
        tar.extractall(root, filter="data")
    archive.unlink()
    sdk = root / SDK
    if not (sdk / "lib" / "crt1.o").is_file():
        return None, f"{release.url} has no {SDK}/lib/crt1.o, so it is not an OpenOrbis SDK"
    return sdk, None


def append(env, variable, lines):
    """Add lines to the file the runner names in env[variable], such as GITHUB_ENV."""
    with open(env[variable], "a", encoding="utf-8") as out:
        out.writelines(f"{line}\n" for line in lines)


def main(env=None, fetch=download):
    """Install the release and export it; 1 when the version isn't pinned or the download won't do."""
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
    sdk, error = unpack(release, root, fetch)
    if error:
        print(f"::error::{error}")
        return 1
    header = sdk / "include" / "bits" / "alltypes.h"
    text, fixed = fix_mode_t(header.read_text(encoding="utf-8"))
    if fixed:
        header.write_text(text, encoding="utf-8")
        print(f"Fixed {header.relative_to(root)}: mode_t is 16 bits, as libkernel has it")
    cmake = shutil.copytree(env["CMAKE_FILES"], root / "cmake")
    append(env, "GITHUB_ENV", [f"OO_PS4_TOOLCHAIN={sdk}", f"OPENORBIS_LLVM_VERSION={release.llvm}",
                               f"OPENORBIS_TOOLCHAIN_FILE={cmake / 'OpenOrbisToolchain.cmake'}"])
    append(env, "GITHUB_PATH", [str(sdk / "bin" / "linux")])
    append(env, "GITHUB_OUTPUT", [f"llvm-version={release.llvm}"])
    print(f"OpenOrbis {version} in {sdk}, for LLVM {release.llvm}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

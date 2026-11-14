"""Install a CMake release from Kitware's GitHub releases, first on PATH.

Reads CMAKE_VERSION, RUNNER_TEMP and GITHUB_PATH from the environment; Linux and macOS.

CMAKE_VERSION=X.Y.Z  download that release's archive for this machine, check it against the
                     release's own SHA-256 list, unpack it under RUNNER_TEMP, and put its bin
                     first on PATH for later steps. For an image whose CMake is older than the
                     project needs, such as one picked for an old glibc.
"""

import hashlib
import os
import platform
import re
import sys
import tarfile
import urllib.request
from pathlib import Path

RELEASES = "https://github.com/Kitware/CMake/releases/download"
LINUX_MACHINES = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}


def archive_name(version, system, machine):
    """The release asset for one machine, and the directory under it that holds bin; ValueError if none."""
    if system == "Darwin":
        return f"cmake-{version}-macos-universal.tar.gz", "CMake.app/Contents"
    if system == "Linux" and machine.lower() in LINUX_MACHINES:
        return f"cmake-{version}-linux-{LINUX_MACHINES[machine.lower()]}.tar.gz", ""
    raise ValueError(f"CMake publishes no archive for {system} {machine}")


def expected_digest(listing, name):
    """The sha256 the release's SHA-256 list gives for name; ValueError if it gives none."""
    for line in listing.splitlines():
        digest, _, listed = line.partition("  ")
        if listed.strip() == name:
            return digest.strip()
    raise ValueError(f"the release's SHA-256 list has no {name}")


def fetch(url):
    """The body at url, waiting up to five minutes for it."""
    with urllib.request.urlopen(url, timeout=300) as response:
        return response.read()


def main(env=None, download=fetch, system=None, machine=None):
    """Download, check, unpack and put CMake on PATH; 1 when the version or machine won't do."""
    env = os.environ if env is None else env
    version = env.get("CMAKE_VERSION", "").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        print(f"::error::CMAKE_VERSION must be a release such as 3.28.3, not {version!r}")
        return 1
    try:
        name, contents = archive_name(version, system or platform.system(), machine or platform.machine())
        base = f"{RELEASES}/v{version}"
        digest = expected_digest(download(f"{base}/cmake-{version}-SHA-256.txt").decode("utf-8"), name)
    except ValueError as error:
        print(f"::error::{error}")
        return 1
    data = download(f"{base}/{name}")
    if hashlib.sha256(data).hexdigest() != digest:
        print(f"::error::{name} does not match its sha256 in the release's list")
        return 1
    target = Path(env["RUNNER_TEMP"], "cmake")
    archive = target / name
    target.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(data)
    with tarfile.open(archive) as unpacked:
        unpacked.extractall(target, filter="data")
    archive.unlink()
    bin_dir = target / name.removesuffix(".tar.gz") / contents / "bin"
    with open(env["GITHUB_PATH"], "a", encoding="utf-8") as path_file:
        path_file.write(f"{bin_dir}\n")
    print(f"CMake {version} in {bin_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

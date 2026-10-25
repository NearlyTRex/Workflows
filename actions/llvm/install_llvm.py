"""Install an LLVM release from apt.llvm.org beside the runner's own clang.

Reads LLVM_VERSION from the environment; for Ubuntu and Debian runners.

LLVM_VERSION=N  add LLVM's signed repository for release N and the runner's distribution
                release, then install clang-N and its tools (clang-format-N, clang-tidy-N,
                clang-tools-N, lld-N, and llvm-N with llvm-cov and llvm-profdata), the
                profiling runtime and libc++. Every package carries the version, so the
                distribution's clang stays the default and callers name clang++-N.
"""

import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

REPOSITORY = "https://apt.llvm.org"
KEY_URL = f"{REPOSITORY}/llvm-snapshot.gpg.key"
KEYRING = "/etc/apt/keyrings/apt.llvm.org.asc"
TOOLS = ("clang", "clang-format", "clang-tidy", "clang-tools", "lld", "llvm")
LIBRARIES = ("libclang-rt-{}-dev", "libc++-{}-dev", "libc++abi-{}-dev")


def packages(version):
    return [f"{tool}-{version}" for tool in TOOLS] + [library.format(version) for library in LIBRARIES]


def codename(os_release):
    """The distribution release's code name from /etc/os-release, or None."""
    for line in os_release.splitlines():
        key, _, value = line.partition("=")
        if key == "VERSION_CODENAME" and value.strip('"'):
            return value.strip('"')
    return None


def sources_line(release, version):
    return f"deb [signed-by={KEYRING}] {REPOSITORY}/{release}/ llvm-toolchain-{release}-{version} main\n"


def fetch(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def main(env=None, run=subprocess.run, download=fetch, os_release=Path("/etc/os-release")):
    env = os.environ if env is None else env
    version = env.get("LLVM_VERSION", "").strip()
    if not version.isdigit():
        print(f"::error::LLVM_VERSION must be a release number, not {version!r}")
        return 1
    release = codename(os_release.read_text(encoding="utf-8"))
    if release is None:
        print(f"::error::{os_release} names no release, so there is no LLVM repository to pick")
        return 1
    # A container job usually runs as root, without sudo.
    sudo = [] if os.geteuid() == 0 else ["sudo"]
    sources = f"/etc/apt/sources.list.d/llvm-toolchain-{release}-{version}.list"
    with tempfile.TemporaryDirectory() as staging:
        key = Path(staging, "key.asc")
        key.write_bytes(download(KEY_URL))
        line = Path(staging, "llvm.list")
        line.write_text(sources_line(release, version), encoding="utf-8")
        run([*sudo, "install", "-D", "-m", "644", str(key), KEYRING], check=True)
        run([*sudo, "install", "-D", "-m", "644", str(line), sources], check=True)
    run([*sudo, "apt-get", "update"], check=True)
    run([*sudo, "apt-get", "install", "-y", "--no-install-recommends", *packages(version)], check=True)
    print(f"LLVM {version} for {release}: clang++-{version}, clang-tidy-{version}, libc++")
    return 0


if __name__ == "__main__":
    sys.exit(main())

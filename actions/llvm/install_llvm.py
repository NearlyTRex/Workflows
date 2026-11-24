"""Install an LLVM release: from apt.llvm.org beside the runner's own clang, or LLVM's archive on Windows.

Reads LLVM_VERSION, LLVM_ON_PATH, RUNNER_OS, RUNNER_ARCH, RUNNER_TEMP, GITHUB_TOKEN and GITHUB_PATH
from the environment; for Ubuntu, Debian and Windows runners.

LLVM_VERSION=N     on Linux, add LLVM's signed repository for release N and the runner's
                   distribution release, then install clang-N and its tools (clang-format-N,
                   clang-tidy-N, clang-tools-N, lld-N, and llvm-N with llvm-cov and llvm-profdata),
                   the profiling runtime and libc++. Every package carries the version, so the
                   distribution's clang stays the default and callers name clang++-N.
                   On Windows, unpack the newest N.x.y that LLVM published a Windows archive for,
                   checked against the sha256 GitHub gives for it, under RUNNER_TEMP, and put its
                   bin first on PATH: its clang-cl, lld-link and clang-tidy carry no version, so
                   that is the only way a build reaches them. A Visual Studio's STL can need a
                   newer clang than the image's own. The archive rather than the installer, which
                   went from NSIS to MSI with release 23.
LLVM_ON_PATH=true  on Linux, also put /usr/lib/llvm-N/bin first on PATH for later steps, so the
                   unversioned clang, clang-tidy and lld are release N's.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

REPOSITORY = "https://apt.llvm.org"
KEY_URL = f"{REPOSITORY}/llvm-snapshot.gpg.key"
KEYRING = "/etc/apt/keyrings/apt.llvm.org.asc"
TOOLS = ("clang", "clang-format", "clang-tidy", "clang-tools", "lld", "llvm")
LIBRARIES = ("libclang-rt-{}-dev", "libc++-{}-dev", "libc++abi-{}-dev")
GITHUB_API = "https://api.github.com/repos/llvm/llvm-project"
# The Windows archive's name for each runner architecture.
WINDOWS_ARCHES = {"X64": "x86_64", "ARM64": "aarch64"}
ARCHIVE = "clang+llvm-{point}-{arch}-pc-windows-msvc.tar.xz"


def packages(version):
    """Release version's compiler, tools, profiling runtime and libc++, as apt names them."""
    return [f"{tool}-{version}" for tool in TOOLS] + [library.format(version) for library in LIBRARIES]


def codename(os_release):
    """The distribution release's code name from /etc/os-release, or None."""
    for line in os_release.splitlines():
        key, _, value = line.partition("=")
        if key == "VERSION_CODENAME" and value.strip('"'):
            return value.strip('"')
    return None


def sources_line(release, version):
    """The apt source for one LLVM release on one distribution release, signed by LLVM's key."""
    return f"deb [signed-by={KEYRING}] {REPOSITORY}/{release}/ llvm-toolchain-{release}-{version} main\n"


def fetch(url, headers=None):
    """The body at url, waiting up to five minutes for it."""
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=300) as response:
        return response.read()


def save(url, path):
    """Write the body at url to path a piece at a time, for an archive too big to hold; its sha256."""
    digest = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=300) as response, open(path, "wb") as out:
        while piece := response.read(1 << 20):
            digest.update(piece)
            out.write(piece)
    return digest.hexdigest()


def point_releases(refs, version):
    """Release version's finished N.x.y from LLVM's tags, newest first; release candidates left out."""
    pattern = re.compile(rf"refs/tags/llvmorg-({version}\.\d+\.\d+)")
    found = [match.group(1) for ref in refs if (match := pattern.fullmatch(ref["ref"]))]
    return sorted(found, key=lambda release: tuple(int(part) for part in release.split(".")), reverse=True)


def release_asset(release, name):
    """The release's asset called name, as the API lists it, or None."""
    return next((asset for asset in release.get("assets", []) if asset["name"] == name), None)


def install_linux(env, version, run, download, os_release):
    """Add the repository and install the versioned toolchain; 1 when the release won't do."""
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
    if env.get("LLVM_ON_PATH") == "true":
        with open(env["GITHUB_PATH"], "a", encoding="utf-8") as path_file:
            path_file.write(f"/usr/lib/llvm-{version}/bin\n")
        print(f"/usr/lib/llvm-{version}/bin is first on PATH")
    return 0


def find_archive(version, arch, download, headers):
    """The newest point release with a Windows archive for arch, and that archive's asset; (None, None) if none."""
    refs = json.loads(download(f"{GITHUB_API}/git/matching-refs/tags/llvmorg-{version}.", headers))
    # A release is tagged before its Windows build is attached, so the newest tag may not have one yet.
    for point in point_releases(refs, version):
        try:
            release = json.loads(download(f"{GITHUB_API}/releases/tags/llvmorg-{point}", headers))
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
            continue
        asset = release_asset(release,ARCHIVE.format(point=point, arch=arch))
        if asset is not None:
            return point, asset
    return None, None


def install_windows(env, version, download, saver):
    """Download, check and unpack LLVM's Windows archive, then put its bin first on PATH; 1 when none will do."""
    arch = WINDOWS_ARCHES.get(env.get("RUNNER_ARCH", ""))
    if arch is None:
        print(f"::error::LLVM publishes no Windows archive for {env.get('RUNNER_ARCH')!r} runners")
        return 1
    headers = {"Accept": "application/vnd.github+json"}
    if env.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {env['GITHUB_TOKEN']}"
    point, asset = find_archive(version, arch, download, headers)
    if asset is None:
        print(f"::error::no LLVM {version}.x.y release has a {arch} Windows archive")
        return 1
    algorithm, _, digest = (asset.get("digest") or "").partition(":")
    if algorithm != "sha256":
        print(f"::error::GitHub gives no sha256 for {asset['name']}, so it can't be checked")
        return 1
    target = Path(env["RUNNER_TEMP"], f"llvm-{version}")
    target.mkdir(parents=True, exist_ok=True)
    archive = target / asset["name"]
    if saver(asset["browser_download_url"], archive) != digest:
        archive.unlink()
        print(f"::error::{asset['name']} does not match the sha256 GitHub gives for it")
        return 1
    with tarfile.open(archive) as unpacked:
        unpacked.extractall(target, filter="data")
    archive.unlink()
    bin_dir = target / asset["name"].removesuffix(".tar.xz") / "bin"
    if not bin_dir.is_dir():
        shutil.rmtree(target)
        print(f"::error::{asset['name']} holds no {bin_dir.relative_to(target)}")
        return 1
    with open(env["GITHUB_PATH"], "a", encoding="utf-8") as path_file:
        path_file.write(f"{bin_dir}\n")
    print(f"LLVM {point} ({arch}) in {bin_dir}, first on PATH")
    return 0


def main(env=None, run=subprocess.run, download=fetch, os_release=Path("/etc/os-release"), saver=save):
    """Install release LLVM_VERSION for the runner; 1 when the version, release or runner won't do."""
    env = os.environ if env is None else env
    version = env.get("LLVM_VERSION", "").strip()
    if not version.isdigit():
        print(f"::error::LLVM_VERSION must be a release number, not {version!r}")
        return 1
    if env.get("RUNNER_OS") == "Windows":
        return install_windows(env, version, download, saver)
    return install_linux(env, version, run, download, os_release)


if __name__ == "__main__":
    sys.exit(main())

"""Check the tool versions Dependabot can't track.

Dependabot has no Chocolatey ecosystem, so the Inno Setup version inno-setup.yml
installs would go stale silently, as would the OpenOrbis release actions/openorbis
pins by sha256 and the devkitPro and linux-image images cpp-build.yml pins by
digest inside an expression. This compares them with the newest stable Inno Setup
on the Chocolatey community feed, the latest OpenOrbis release on GitHub, the
newest dated devkitPro tags on Docker Hub and the buildpack-deps tags' current
builds, and fails when a newer one is out, so the weekly run says so.

Usage:
    check_tool_versions.py
"""

import datetime
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INNO_WORKFLOW = ROOT / ".github" / "workflows" / "inno-setup.yml"
FEED = "https://community.chocolatey.org/api/v2/FindPackagesById()?id='innosetup'"
OPENORBIS_SCRIPT = ROOT / "actions" / "openorbis" / "install_openorbis.py"
OPENORBIS_LATEST = "https://api.github.com/repos/OpenOrbis/OpenOrbis-PS4-Toolchain/releases/latest"
CPP_BUILD_WORKFLOW = ROOT / ".github" / "workflows" / "cpp-build.yml"
DEVKITPRO_TAGS = "https://hub.docker.com/v2/repositories/devkitpro/{image}/tags?page_size=100&ordering=last_updated"
# devkitPro tags each image build with its date, and moves latest along.
DEVKITPRO_IMAGE = re.compile(r"devkitpro/([\w-]+):(\d{8})@sha256:[0-9a-f]{64}")
# linux-image's images are buildpack-deps tags, which Docker rebuilds in place
# for each security update; a rebuild counts once it is as old as Dependabot's cooldown.
LINUX_IMAGE = re.compile(r'"([\w.-]+)":"buildpack-deps:([\w.-]+)@(sha256:[0-9a-f]{64})"')
BUILDPACK_TAG = "https://hub.docker.com/v2/repositories/library/buildpack-deps/tags/{tag}"
SETTLE = datetime.timedelta(days=7)

ATOM = "{http://www.w3.org/2005/Atom}"
DATA = "{http://schemas.microsoft.com/ado/2007/08/dataservices}"
META = "{http://schemas.microsoft.com/ado/2007/08/dataservices/metadata}"


def fetch(url):
    with urllib.request.urlopen(url, timeout=30) as response:
        return response.read()


def parse_feed(xml):
    """Return the (version, is_prerelease) pairs on one feed page, and the next page's URL."""
    root = ET.fromstring(xml)
    found = []
    for entry in root.iter(f"{ATOM}entry"):
        props = entry.find(f"{META}properties")
        version = props.findtext(f"{DATA}Version")
        found.append((version, props.findtext(f"{DATA}IsPrerelease") == "true"))
    following = next((link.get("href") for link in root.findall(f"{ATOM}link") if link.get("rel") == "next"),
                     None)
    return found, following


def version_key(version):
    return tuple(int(part) for part in re.findall(r"\d+", version))


def latest_stable(get=fetch, url=FEED):
    stable = []
    while url:
        found, url = parse_feed(get(url))
        stable += [version for version, prerelease in found if not prerelease]
    if not stable:
        raise ValueError("the Chocolatey feed listed no stable innosetup release")
    return max(stable, key=version_key)


def pinned(workflow=INNO_WORKFLOW):
    match = re.search(r'inno-setup-version:.*?default:\s*"([^"]+)"', workflow.read_text(), re.DOTALL)
    return match.group(1)


def pinned_openorbis(script=OPENORBIS_SCRIPT):
    """The newest release install_openorbis.py has a sha256 for."""
    return max(re.findall(r'^    "([\d.]+)": Release\(', script.read_text(), re.MULTILINE), key=version_key)


def latest_openorbis(get=fetch):
    """The version GitHub marks as OpenOrbis's latest release."""
    return json.loads(get(OPENORBIS_LATEST))["tag_name"].removeprefix("v")


def pinned_devkitpro(workflow=CPP_BUILD_WORKFLOW):
    """Each devkitPro image cpp-build.yml pins, as its name and dated tag."""
    return DEVKITPRO_IMAGE.findall(workflow.read_text())


def latest_devkitpro(image, get=fetch):
    """The newest dated tag of devkitpro/image on Docker Hub, and its digest."""
    tags = json.loads(get(DEVKITPRO_TAGS.format(image=image)))["results"]
    newest = max((tag for tag in tags if re.fullmatch(r"\d{8}", tag["name"])), key=lambda tag: tag["name"])
    return newest["name"], newest["digest"]


def pinned_linux_images(workflow=CPP_BUILD_WORKFLOW):
    """Each linux-image name cpp-build.yml maps, with its buildpack-deps tag and digest."""
    return LINUX_IMAGE.findall(workflow.read_text())


def latest_buildpack(tag, get=fetch):
    """The digest buildpack-deps:tag has on Docker Hub now, and when it was built."""
    found = json.loads(get(BUILDPACK_TAG.format(tag=tag)))
    return found["digest"], datetime.datetime.fromisoformat(found["last_updated"].replace("Z", "+00:00"))


def check_linux_images(cpp_build, get, now):
    """A line per linux-image, and whether any has a rebuild older than SETTLE to move to."""
    lines, stale = [], False
    for name, tag, digest in pinned_linux_images(cpp_build):
        newest, built = latest_buildpack(tag, get)
        if newest != digest and now - built >= SETTLE:
            lines.append(f"::error file={cpp_build.relative_to(ROOT)}::buildpack-deps:{tag} was rebuilt on "
                         f"{built:%Y-%m-%d}. Move cpp-build.yml's linux-image {name} to "
                         f"buildpack-deps:{tag}@{newest}.")
            stale = True
        else:
            lines.append(f"linux-image {name} (buildpack-deps:{tag}) is current")
    return lines, stale


def main(get=fetch, workflow=INNO_WORKFLOW, openorbis=OPENORBIS_SCRIPT, cpp_build=CPP_BUILD_WORKFLOW, now=None):
    """Compare each pin with the newest release; 1 when any is behind."""
    checks = [("Inno Setup", pinned(workflow), latest_stable(get), workflow,
               "Update inno-setup.yml's inno-setup-version default."),
              ("OpenOrbis", pinned_openorbis(openorbis), latest_openorbis(get), openorbis,
               "Add it to actions/openorbis's RELEASES with its asset's sha256.")]
    for image, tag in pinned_devkitpro(cpp_build):
        newest, digest = latest_devkitpro(image, get)
        checks.append((f"devkitpro/{image}", tag, newest, cpp_build,
                       f"Move cpp-build.yml's devkitpro image to devkitpro/{image}:{newest}@{digest}."))
    status = 0
    for name, current, newest, file, fix in checks:
        if version_key(newest) > version_key(current):
            print(f"::error file={file.relative_to(ROOT)}::{name} {newest} is out; {current} is pinned. {fix}")
            status = 1
        else:
            print(f"{name} {current} is the newest")
    lines, stale = check_linux_images(cpp_build, get, now or datetime.datetime.now(datetime.timezone.utc))
    for line in lines:
        print(line)
    return 1 if stale else status

if __name__ == "__main__":
    sys.exit(main())

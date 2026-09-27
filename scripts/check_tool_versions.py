"""Check the tool versions Dependabot can't track.

Dependabot has no Chocolatey ecosystem, so the Inno Setup version inno-setup.yml
installs would go stale silently. This compares it with the newest stable
release on the Chocolatey community feed, and fails when a newer one is out, so
the weekly run says so.

Usage:
    check_tool_versions.py
"""

import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INNO_WORKFLOW = ROOT / ".github" / "workflows" / "inno-setup.yml"
FEED = "https://community.chocolatey.org/api/v2/FindPackagesById()?id='innosetup'"

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
    match = re.search(r'inno-setup-version:.*?default:\s*"([^"]+)"', workflow.read_text(), re.S)
    return match.group(1)


def main(get=fetch, workflow=INNO_WORKFLOW):
    current, newest = pinned(workflow), latest_stable(get)
    if version_key(newest) > version_key(current):
        print(f"::error file={workflow.relative_to(ROOT)}::Inno Setup {newest} is out; "
              f"inno-setup.yml installs {current}. Update its inno-setup-version default.")
        return 1
    print(f"Inno Setup {current} is the newest stable release")
    return 0


if __name__ == "__main__":
    sys.exit(main())

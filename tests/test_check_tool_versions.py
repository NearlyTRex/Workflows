import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("check_tool_versions", ROOT / "scripts" / "check_tool_versions.py")
tools = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tools)


def page(versions, next_url=None):
    entries = "".join(
        f"""<entry><m:properties><d:Version>{v}</d:Version>
        <d:IsPrerelease m:type="Edm.Boolean">{"true" if pre else "false"}</d:IsPrerelease>
        </m:properties></entry>""" for v, pre in versions)
    link = f'<link rel="next" href="{next_url}" />' if next_url else ""
    return f"""<?xml version="1.0" encoding="utf-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices"
          xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata">
      <link rel="self" href="x" />{link}{entries}</feed>""".encode()


PAGES = {
    tools.FEED: page([("6.6.1", False), ("6.9.0", False), ("7.0.0-beta1", True)], "page2"),
    "page2": page([("6.10.0", False), ("5.5.9", False)]),
}


def test_latest_stable_follows_pages_and_skips_prereleases():
    assert tools.latest_stable(PAGES.__getitem__) == "6.10.0"   # numeric, not string, order


def test_no_stable_release_is_an_error():
    with pytest.raises(ValueError):
        tools.latest_stable(lambda url: page([("7.0.0-rc1", True)]))


def test_pinned_reads_the_real_workflow():
    assert tools.version_key(tools.pinned()) >= (6, 7, 1)


def workflow_pinning(tmp_path, version):
    path = tools.INNO_WORKFLOW
    copy = tmp_path / "inno-setup.yml"
    copy.write_text(path.read_text().replace(f'default: "{tools.pinned()}"', f'default: "{version}"'))
    return copy


def test_up_to_date_passes(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tools, "ROOT", tmp_path)
    assert tools.main(PAGES.__getitem__, workflow_pinning(tmp_path, "6.10.0")) == 0
    assert "6.10.0 is the newest" in capsys.readouterr().out


def test_newer_release_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tools, "ROOT", tmp_path)
    assert tools.main(PAGES.__getitem__, workflow_pinning(tmp_path, "6.7.1")) == 1
    assert "Inno Setup 6.10.0 is out; inno-setup.yml installs 6.7.1" in capsys.readouterr().out


def test_fetch_reads_the_url(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def read(self): return b"body"
    monkeypatch.setattr(tools.urllib.request, "urlopen", lambda url, timeout: Response())
    assert tools.fetch("https://example.test") == b"body"

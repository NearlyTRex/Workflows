
import datetime
import json

import pytest

from script_helpers import load_script

tools = load_script("scripts/check_tool_versions.py")


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
    tools.OPENORBIS_LATEST: json.dumps({"tag_name": "v0.5.4"}).encode(),
    tools.DEVKITPRO_TAGS.format(image="devkita64"): json.dumps({"results": [
        {"name": "latest", "digest": "sha256:" + "c" * 64},
        {"name": "20260219", "digest": "sha256:" + "c" * 64},
        {"name": "20251231", "digest": "sha256:" + "b" * 64},
    ]}).encode(),
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


def openorbis_pinning(tmp_path, *versions):
    script = tmp_path / "install_openorbis.py"
    script.write_text("RELEASES = {\n" + "".join(f'    "{v}": Release(\n    ),\n' for v in versions) + "}\n")
    return script


def cpp_build_pinning(tmp_path, tag):
    workflow = tmp_path / "cpp-build.yml"
    workflow.write_text("container: ${{ fromJSON('{\"switch\":\"devkitpro/devkita64:"
                        f"{tag}@sha256:{'a' * 64}\"}}')[inputs.devkitpro] }}}}\n")
    return workflow


def test_pinned_devkitpro_reads_the_real_workflow():
    assert [image for image, _ in tools.pinned_devkitpro()] == ["devkita64"]


def test_latest_devkitpro_is_the_newest_dated_tag():
    assert tools.latest_devkitpro("devkita64", PAGES.__getitem__) == ("20260219", "sha256:" + "c" * 64)


def test_pinned_openorbis_reads_the_real_script():
    assert tools.version_key(tools.pinned_openorbis()) >= (0, 5, 4)


def test_pinned_openorbis_is_the_newest_release_pinned(tmp_path):
    assert tools.pinned_openorbis(openorbis_pinning(tmp_path, "0.5.10", "0.5.4")) == "0.5.10"


def test_up_to_date_passes(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tools, "ROOT", tmp_path)
    assert tools.main(PAGES.__getitem__, workflow_pinning(tmp_path, "6.10.0"),
                      openorbis_pinning(tmp_path, "0.5.4"), cpp_build_pinning(tmp_path, "20260219")) == 0
    out = capsys.readouterr().out
    assert "Inno Setup 6.10.0 is the newest" in out and "OpenOrbis 0.5.4 is the newest" in out
    assert "devkitpro/devkita64 20260219 is the newest" in out


def test_newer_release_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tools, "ROOT", tmp_path)
    assert tools.main(PAGES.__getitem__, workflow_pinning(tmp_path, "6.7.1"),
                      openorbis_pinning(tmp_path, "0.5.4"), cpp_build_pinning(tmp_path, "20260219")) == 1
    assert "Inno Setup 6.10.0 is out; 6.7.1 is pinned" in capsys.readouterr().out


def test_newer_openorbis_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tools, "ROOT", tmp_path)
    assert tools.main(PAGES.__getitem__, workflow_pinning(tmp_path, "6.10.0"),
                      openorbis_pinning(tmp_path, "0.5.3"), cpp_build_pinning(tmp_path, "20260219")) == 1
    assert "OpenOrbis 0.5.4 is out; 0.5.3 is pinned" in capsys.readouterr().out


def test_newer_devkitpro_image_fails_with_its_digest(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tools, "ROOT", tmp_path)
    assert tools.main(PAGES.__getitem__, workflow_pinning(tmp_path, "6.10.0"),
                      openorbis_pinning(tmp_path, "0.5.4"), cpp_build_pinning(tmp_path, "20251231")) == 1
    assert ("devkitpro/devkita64 20260219 is out; 20251231 is pinned. Move cpp-build.yml's devkitpro image "
            f"to devkitpro/devkita64:20260219@sha256:{'c' * 64}.") in capsys.readouterr().out


NOW = datetime.datetime(2026, 11, 20, tzinfo=datetime.timezone.utc)


def linux_image_pinning(tmp_path, digest):
    workflow = cpp_build_pinning(tmp_path, "20260219")
    workflow.write_text(workflow.read_text() + "container: ${{ fromJSON('{\"ubuntu-22.04\":\"buildpack-deps:"
                        f"jammy-scm@sha256:{digest}\"}}')[inputs.linux-image] }}}}\n")
    return workflow


def buildpack_pages(digest, built):
    return {**PAGES, tools.BUILDPACK_TAG.format(tag="jammy-scm"): json.dumps(
        {"digest": f"sha256:{digest}", "last_updated": built}).encode()}


def test_pinned_linux_images_reads_the_real_workflow():
    assert [(name, tag) for name, tag, _ in tools.pinned_linux_images()] == [("ubuntu-22.04", "jammy-scm")]


@pytest.mark.parametrize("pinned, built", [("d" * 64, "2026-11-01T00:00:00.000000Z"),
                                           ("a" * 64, "2026-11-18T06:19:49.717682Z")])
def test_a_current_or_freshly_rebuilt_image_passes(tmp_path, monkeypatch, capsys, pinned, built):
    monkeypatch.setattr(tools, "ROOT", tmp_path)
    pages = buildpack_pages("d" * 64, built)
    assert tools.main(pages.__getitem__, workflow_pinning(tmp_path, "6.10.0"), openorbis_pinning(tmp_path, "0.5.4"),
                      linux_image_pinning(tmp_path, pinned), now=NOW) == 0
    assert "linux-image ubuntu-22.04 (buildpack-deps:jammy-scm) is current" in capsys.readouterr().out


def test_a_rebuild_older_than_the_cooldown_fails_with_its_digest(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tools, "ROOT", tmp_path)
    pages = buildpack_pages("d" * 64, "2026-11-12T06:19:49.717682Z")
    assert tools.main(pages.__getitem__, workflow_pinning(tmp_path, "6.10.0"), openorbis_pinning(tmp_path, "0.5.4"),
                      linux_image_pinning(tmp_path, "a" * 64), now=NOW) == 1
    assert ("::error file=cpp-build.yml::buildpack-deps:jammy-scm was rebuilt on 2026-11-12. Move cpp-build.yml's "
            f"linux-image ubuntu-22.04 to buildpack-deps:jammy-scm@sha256:{'d' * 64}.") in capsys.readouterr().out


def test_now_defaults_to_the_clock(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tools, "ROOT", tmp_path)
    pages = buildpack_pages("a" * 64, "2026-11-12T06:19:49.717682Z")
    assert tools.main(pages.__getitem__, workflow_pinning(tmp_path, "6.10.0"), openorbis_pinning(tmp_path, "0.5.4"),
                      linux_image_pinning(tmp_path, "a" * 64)) == 0
    assert "is current" in capsys.readouterr().out


def test_fetch_reads_the_url(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"body"
    monkeypatch.setattr(tools.urllib.request, "urlopen", lambda url, timeout: Response())
    assert tools.fetch("https://example.test") == b"body"

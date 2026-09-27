import hashlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "publish_release", ROOT / "actions" / "publish-release" / "publish_release.py")
pub = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pub)


class FakeGh:
    """Records gh calls; fails the ones whose first two words are in fail."""

    def __init__(self, fail=()):
        self.calls, self.fail = [], set(fail)

    def __call__(self, args):
        self.calls.append(args)
        return 1 if " ".join(args[:2]) in self.fail else 0

    def verbs(self):
        return [" ".join(c[:3] if c[1] == "-X" else c[:2]) for c in self.calls]


@pytest.fixture
def env(tmp_path):
    assets = tmp_path / "assets"
    (assets / "sub").mkdir(parents=True)
    (assets / "App-1.2.3.exe").write_bytes(b"exe")
    (assets / "sub" / "app.tar.gz").write_bytes(b"tar")
    work = tmp_path / "work"
    work.mkdir()
    return {"REPO": "o/r", "SHA": "abc", "TAG": "v1.2.3", "VERSION": "1.2.3", "TITLE": "App",
            "ASSETS_DIR": str(assets), "RUNNER_TEMP": str(work),
            "GITHUB_OUTPUT": str(tmp_path / "out")}


def test_publishes_tag_then_release(env, tmp_path):
    gh = FakeGh()
    assert pub.publish(env, gh) == 0
    assert gh.verbs() == ["api repos/o/r/git/refs", "release create"]
    release = gh.calls[1]
    assert release[:3] == ["release", "create", "v1.2.3"]
    assert ["--title", "App 1.2.3"] == release[release.index("--title"):release.index("--title") + 2]
    assert "--draft" not in release and "--notes-file" not in release
    assert [Path(a).name for a in release if a.startswith(env["ASSETS_DIR"])] == ["App-1.2.3.exe", "app.tar.gz"]
    assert (tmp_path / "out").read_text() == "released=true\n"


def test_draft_notes_and_checksums(env, tmp_path):
    notes = tmp_path / "notes.md"
    notes.write_text("Get App-{version}.exe from {tag} at github.com/{repository}")
    env.update(DRAFT="true", CHECKSUMS="true", NOTES_FILE=str(notes))
    gh = FakeGh()
    assert pub.publish(env, gh) == 0
    release = gh.calls[1]
    assert "--draft" in release
    rendered = Path(release[release.index("--notes-file") + 1]).read_text()
    assert rendered == "Get App-1.2.3.exe from v1.2.3 at github.com/o/r"
    sums = Path(release[-1])
    assert sums.name == "SHA256SUMS.txt"
    assert sums.read_text() == (f"{hashlib.sha256(b'exe').hexdigest()}  App-1.2.3.exe\n"
                                f"{hashlib.sha256(b'tar').hexdigest()}  sub/app.tar.gz\n")


def test_checksums_need_assets(env, tmp_path):
    env.update(ASSETS_DIR="", CHECKSUMS="true")
    gh = FakeGh()
    assert pub.publish(env, gh) == 0
    assert not any("SHA256SUMS" in a for a in gh.calls[1])


def test_missing_assets_directory_attaches_nothing(env, tmp_path):
    env["ASSETS_DIR"] = str(tmp_path / "nothing-downloaded")
    gh = FakeGh()
    assert pub.publish(env, gh) == 0
    assert gh.calls[1][-1] == "--generate-notes"


def test_failed_publish_removes_the_release_and_tag(env, tmp_path, capsys):
    gh = FakeGh(fail={"release create"})
    assert pub.publish(env, gh) == 1
    assert gh.verbs() == ["api repos/o/r/git/refs", "release create", "release delete", "api -X DELETE"]
    assert gh.calls[3][3] == "repos/o/r/git/refs/tags/v1.2.3"
    assert "retries it" in capsys.readouterr().out
    assert not (tmp_path / "out").exists()


def test_failed_tag_stops_before_publishing(env, capsys):
    gh = FakeGh(fail={"api repos/o/r/git/refs"})
    assert pub.publish(env, gh) == 1
    assert gh.verbs() == ["api repos/o/r/git/refs"]
    assert "Could not create the tag" in capsys.readouterr().out


def test_missing_notes_file_fails_before_the_tag(env):
    env["NOTES_FILE"] = "/no/such/notes.md"
    gh = FakeGh()
    with pytest.raises(FileNotFoundError):
        pub.publish(env, gh)
    assert gh.calls == []


def test_dry_run_prepares_but_runs_nothing(env, tmp_path, capsys):
    env.update(DRY_RUN="true", CHECKSUMS="true")
    gh = FakeGh()
    assert pub.publish(env, gh) == 0
    assert gh.calls == []
    out = capsys.readouterr().out
    assert "gh api repos/o/r/git/refs -f ref=refs/tags/v1.2.3 -f sha=abc --silent" in out
    assert "gh release create v1.2.3" in out and "SHA256SUMS.txt" in out
    assert (tmp_path / "work" / "SHA256SUMS.txt").exists()
    assert not (tmp_path / "out").exists()


def test_main_runs_the_real_gh(env, monkeypatch):
    seen = []
    monkeypatch.setattr(pub.subprocess, "run", lambda args: seen.append(args) or type("R", (), {"returncode": 0})())
    assert pub.main(env) == 0
    assert seen[0][:2] == ["gh", "api"] and seen[1][:3] == ["gh", "release", "create"]

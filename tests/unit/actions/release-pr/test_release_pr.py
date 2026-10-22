"""release_pr.py, with the fake gh and git from conftest.py logging their calls."""

from script_helpers import load_script

release_pr = load_script("actions/release-pr/release_pr.py")


RELEASE = {"TAG": "v1.2.0", "VERSION": "1.2.0", "VERSION_FILE": "VERSION", "BASE": "main", "REPO": "owner/repo"}
COMMITS = [
    ["git", "config", "user.name", "github-actions[bot]"],
    ["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"],
    ["git", "switch", "-c", "release/v1.2.0"],
    ["git", "commit", "-m", "Release v1.2.0", "--", "VERSION"],
    ["git", "push", "--force", "origin", "release/v1.2.0"],
]
VIEW = ["gh", "pr", "view", "release/v1.2.0", "--json", "url,state", "--jq", 'select(.state == "OPEN") | .url']


def test_opens_a_pull_request(fakes, capsys):
    assert release_pr.main(RELEASE) == 0
    calls = fakes()
    assert calls[1:6] == COMMITS
    assert calls[6] == VIEW
    assert calls[7] == ["gh", "pr", "create", "--base", "main", "--head", "release/v1.2.0", "--title", "Release v1.2.0",
                        "--body", ("Sets the version to 1.2.0 in `VERSION`. Merging this tags v1.2.0 and publishes "
                                   "the release.")]


def test_an_open_pull_request_is_updated_not_reopened(fakes, monkeypatch, capsys):
    url = "https://github.com/owner/repo/pull/7"
    monkeypatch.setenv("FAKE_PR_URL", url)
    assert release_pr.main(RELEASE) == 0
    calls = fakes()
    assert calls[-2] == VIEW
    assert calls[-1][:4] == ["gh", "pr", "edit", url] and calls[-1][4:6] == ["--title", "Release v1.2.0"]
    assert f"Updated the open pull request: {url}" in capsys.readouterr().out


def test_a_closed_pull_request_is_replaced_by_a_new_one(fakes, monkeypatch):
    # gh prints nothing when the branch's pull request is closed, since the jq filter drops it.
    monkeypatch.setenv("FAKE_PR_URL", "")
    assert release_pr.main(RELEASE) == 0
    assert fakes()[-1][:3] == ["gh", "pr", "create"]


def test_an_existing_tag_stops_the_release(fakes, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_TAG", "v1.2.0")
    assert release_pr.main(RELEASE) == 1
    assert "::error::Tag v1.2.0 already exists" in capsys.readouterr().out
    assert [call[0] for call in fakes()] == ["gh"]


def test_release_pr_reads_the_process_environment(fakes, monkeypatch):
    for name, value in RELEASE.items():
        monkeypatch.setenv(name, value)
    assert release_pr.main() == 0

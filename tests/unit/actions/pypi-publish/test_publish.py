"""publish.py's trusted publishing exchange, against fake GitHub and PyPI endpoints and a fake twine."""

import io
import json
import subprocess
import urllib.error

import pytest

from script_helpers import load_script

publish = load_script("actions/pypi-publish/publish.py")

TOKEN_URL = "https://github.example/token?v=1"


class Endpoints:
    """Answers each URL with its JSON, or raises the error given for it, and records the requests."""

    def __init__(self, mint=None):
        self.requests = []
        self.answers = {
            "https://pypi.org/_/oidc/audience": {"audience": "pypi"},
            TOKEN_URL + "&audience=pypi": {"value": "signed-oidc"},
            "https://pypi.org/_/oidc/mint-token": mint if mint is not None else {"success": True, "token": "upload-token"},
        }

    def __call__(self, request, timeout):
        self.requests.append(request)
        answer = self.answers[request.full_url]
        if isinstance(answer, Exception):
            raise answer
        return io.BytesIO(json.dumps(answer).encode())


class Twine:
    def __init__(self, *codes):
        self.calls = []
        self.codes = list(codes) or [0, 0]

    def __call__(self, command, env=None, check=False):
        self.calls.append((command, env))
        return subprocess.CompletedProcess(command, self.codes.pop(0))


@pytest.fixture
def dist(tmp_path):
    (tmp_path / "pkg-1.0-py3-none-any.whl").write_text("wheel")
    (tmp_path / "pkg-1.0.tar.gz").write_text("sdist")
    return tmp_path


def environment(dist, **extra):
    return dict({"DIST": str(dist), "ACTIONS_ID_TOKEN_REQUEST_URL": TOKEN_URL,
                 "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "runner-secret"}, **extra)


def test_the_oidc_token_is_exchanged_and_the_distributions_checked_then_uploaded(dist, capsys):
    endpoints, twine = Endpoints(), Twine()

    assert publish.main(environment(dist), endpoints, twine) == 0

    github, mint = endpoints.requests[1], endpoints.requests[2]
    assert github.get_header("Authorization") == "bearer runner-secret"
    assert json.loads(mint.data) == {"token": "signed-oidc"}
    files = [str(dist / "pkg-1.0-py3-none-any.whl"), str(dist / "pkg-1.0.tar.gz")]
    check = twine.calls[0][0]
    upload, upload_env = twine.calls[1]
    assert check[1:] == ["-m", "twine", "check", "--strict", *files]
    assert upload[1:] == ["-m", "twine", "upload", "--non-interactive", "--repository-url",
                          "https://upload.pypi.org/legacy/", *files]
    assert (upload_env["TWINE_USERNAME"], upload_env["TWINE_PASSWORD"]) == ("__token__", "upload-token")
    assert "::add-mask::upload-token" in capsys.readouterr().out


def test_another_index_can_be_named(dist):
    endpoints, twine = Endpoints(), Twine()
    for url in list(endpoints.answers):
        endpoints.answers[url.replace("https://pypi.org", "https://test.pypi.org")] = endpoints.answers.pop(url)

    env = environment(dist, PYPI_URL="https://test.pypi.org", UPLOAD_URL="https://test.pypi.org/legacy/")

    assert publish.main(env, endpoints, twine) == 0
    assert twine.calls[1][0][6] == "https://test.pypi.org/legacy/"


def test_a_job_without_id_token_permission_is_told_what_it_needs(dist, capsys):
    env = environment(dist)
    del env["ACTIONS_ID_TOKEN_REQUEST_URL"]

    assert publish.main(env, Endpoints(), Twine()) == 1
    assert "permissions: id-token: write" in capsys.readouterr().out


@pytest.mark.parametrize("make", [lambda path: path / "missing", lambda path: path])
def test_nothing_to_upload_fails(tmp_path, capsys, make):
    assert publish.main(environment(make(tmp_path)), Endpoints(), Twine()) == 1
    assert "no distributions in" in capsys.readouterr().out


def refusal(body):
    return urllib.error.HTTPError("https://pypi.org/_/oidc/mint-token", 422, "Unprocessable", {}, io.BytesIO(body))


@pytest.mark.parametrize("body, reason", [
    (json.dumps({"message": "Token request failed", "errors": [
        {"code": "invalid-publisher", "description": "valid token, but no corresponding publisher"}]}).encode(),
     "invalid-publisher: valid token, but no corresponding publisher"),
    (json.dumps({"message": "Token request failed"}).encode(), "Token request failed"),
    (b"<html>gateway error</html>", "no reason given"),
])
def test_a_refused_exchange_says_why_and_uploads_nothing(dist, capsys, body, reason):
    twine = Twine()

    assert publish.main(environment(dist), Endpoints(mint=refusal(body)), twine) == 1

    out = capsys.readouterr().out
    assert "PyPI refused the trusted publishing token (HTTP 422)" in out and reason in out
    assert "trusted publisher names this repository" in out
    assert twine.calls == []


def test_a_failed_check_stops_before_the_upload(dist):
    twine = Twine(1)

    assert publish.main(environment(dist), Endpoints(), twine) == 1
    assert len(twine.calls) == 1


def test_a_failed_upload_fails_the_step(dist):
    assert publish.main(environment(dist), Endpoints(), Twine(0, 2)) == 2

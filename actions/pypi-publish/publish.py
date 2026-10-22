"""Publish distributions to PyPI through trusted publishing, with no stored token.

Asks PyPI which audience it expects, has GitHub sign an OIDC token for the job with it,
exchanges that at PyPI for an upload token that lasts minutes, then checks and uploads
every file in DIST with twine. GitHub sets ACTIONS_ID_TOKEN_REQUEST_URL and _TOKEN when
the job has id-token: write. pypa/gh-action-pypi-publish makes the same exchange, but it
can't run inside another repository's composite action (actions/runner#2473).
"""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

PYPI_URL = "https://pypi.org"
UPLOAD_URL = "https://upload.pypi.org/legacy/"


class PublishError(Exception):
    pass


def fetch_json(url, opener, data=None, headers=None):
    request = urllib.request.Request(url, data=data, headers=headers or {})
    with opener(request, timeout=30) as response:
        return json.load(response)


def oidc_token(env, audience, opener):
    url = env["ACTIONS_ID_TOKEN_REQUEST_URL"] + "&audience=" + urllib.parse.quote(audience)
    answer = fetch_json(url, opener, headers={"Authorization": "bearer " + env["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]})
    return answer["value"]


def upload_token(pypi_url, oidc, opener):
    """Exchange the OIDC token for an upload token, or say why PyPI refused it."""
    try:
        answer = fetch_json(pypi_url + "/_/oidc/mint-token", opener, data=json.dumps({"token": oidc}).encode(),
                            headers={"Content-Type": "application/json"})
    except urllib.error.HTTPError as error:
        try:
            answer = json.load(error)
        except ValueError:
            answer = {}
        reasons = "; ".join(f"{item.get('code')}: {item.get('description')}" for item in answer.get("errors", []))
        raise PublishError(f"PyPI refused the trusted publishing token (HTTP {error.code}): "
                           f"{reasons or answer.get('message') or 'no reason given'}. Check that the project's "
                           "trusted publisher names this repository, this workflow file and this environment.") from error
    return answer["token"]


def main(env=None, opener=urllib.request.urlopen, run=subprocess.run):
    env = os.environ if env is None else env
    if not env.get("ACTIONS_ID_TOKEN_REQUEST_URL"):
        print("::error::no OIDC token to exchange; the publishing job needs permissions: id-token: write")
        return 1
    dist = env["DIST"]
    files = sorted(os.path.join(dist, name) for name in os.listdir(dist)) if os.path.isdir(dist) else []
    if not files:
        print(f"::error::no distributions in {dist}")
        return 1
    pypi_url = env.get("PYPI_URL") or PYPI_URL
    try:
        audience = fetch_json(pypi_url + "/_/oidc/audience", opener)["audience"]
        token = upload_token(pypi_url, oidc_token(env, audience, opener), opener)
    except PublishError as error:
        print(f"::error::{error}")
        return 1
    print(f"::add-mask::{token}")
    checked = run([sys.executable, "-m", "twine", "check", "--strict", *files], check=False)
    if checked.returncode:
        return checked.returncode
    upload_env = dict(env, TWINE_USERNAME="__token__", TWINE_PASSWORD=token)
    uploaded = run([sys.executable, "-m", "twine", "upload", "--non-interactive",
                    "--repository-url", env.get("UPLOAD_URL") or UPLOAD_URL, *files], env=upload_env, check=False)
    return uploaded.returncode


if __name__ == "__main__":
    sys.exit(main())

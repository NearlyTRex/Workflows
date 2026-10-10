"""Wait until a URL answers, or fail after a timeout.

Reads URL and TIMEOUT (seconds) from the environment. An answer is any response below 400,
redirects included without following them. It goes by the clock rather than by counting
attempts, and caps each attempt, so a server that accepts the connection but never answers
holds the step at most one attempt past the timeout, not until the job is killed.
"""

import os
import sys
import time
import urllib.error
import urllib.request

ATTEMPT_SECONDS = 10


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def answers(url, opener):
    try:
        with opener.open(url, timeout=ATTEMPT_SECONDS):
            return True
    except urllib.error.HTTPError as error:
        return error.code < 400
    except (urllib.error.URLError, OSError):
        return False


def main(env=None, clock=time.monotonic, sleep=time.sleep):
    env = os.environ if env is None else env
    url, timeout = env["URL"], int(env["TIMEOUT"])
    opener = urllib.request.build_opener(NoRedirect)
    start = clock()
    while not answers(url, opener):
        if clock() - start >= timeout:
            print(f"::error::{url} did not answer within {timeout}s")
            return 1
        sleep(1)
    print(f"{url} answered after {clock() - start:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())

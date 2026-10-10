"""Run one phase of a build step as a log group with its time on the run's summary page.

On PATH as ci-phase after build-env (see build_env.py).

Usage: ci-phase NAME COMMAND [ARGS...]

The command's output is a collapsible group in the log, and NAME and how long it took are a
row of the step's timings table. A failing phase is marked, gets an error annotation, and
its exit status is returned.
"""

import os
import subprocess
import sys
import time

HEADER = "| Phase | Time |\n|---|---|\n"


def duration(seconds):
    minutes, seconds = divmod(round(seconds), 60)
    return f"{minutes}m {seconds}s"


def record(name, elapsed, status):
    time_taken = duration(elapsed)
    if status != 0:
        print(f"::error::{name} failed after {time_taken}", flush=True)
        time_taken += " (failed)"
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if not step_summary:
        return
    try:
        with open(step_summary, encoding="utf-8") as existing:
            text = existing.read()
    except FileNotFoundError:
        text = ""
    with open(step_summary, "a", encoding="utf-8") as out:
        if not continues_table(text):
            # A phase that wrote to the summary itself, such as ci-clang-tidy, ended the table.
            if text and not text.endswith("\n\n"):
                out.write("\n")
            out.write(HEADER)
        out.write(f"| {name} | {time_taken} |\n")


def continues_table(text):
    start = text.rfind(HEADER)
    if start == -1:
        return False
    return all(line.startswith("| ") for line in text[start + len(HEADER):].splitlines())


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 2:
        print("usage: ci-phase NAME COMMAND [ARGS...]", file=sys.stderr)
        return 2
    name, command = argv[0], argv[1:]
    print(f"::group::{name}", flush=True)
    start = time.monotonic()
    try:
        status = subprocess.run(command, check=False).returncode
        if status < 0:
            status = 128 - status  # killed by a signal, reported as a shell would
    except OSError as error:
        print(f"ci-phase: {error}", file=sys.stderr)
        status = 127
    elapsed = time.monotonic() - start
    print("::endgroup::", flush=True)
    record(name, elapsed, status)
    return status


if __name__ == "__main__":
    sys.exit(main())

"""Run clang-tidy over a compilation database, skipping files an earlier run found clean.

On PATH as ci-clang-tidy after build-env (see build_env.py).

Usage: ci-clang-tidy -p BUILD_DIR [--files REGEX] [--exclude REGEX] [--config-file FILE]
                     [--cache-dir DIR] [--clang-tidy BINARY] [-j JOBS]

Checks every file in BUILD_DIR/compile_commands.json that sits under the current directory
but outside BUILD_DIR, or those --files matches, less those --exclude matches (both searched
in the absolute path). Each file's line is printed as it finishes, then a summary with the
slowest files; on GitHub Actions each diagnostic is also an error annotation and the summary
goes on the run's summary page.

With --cache-dir, a clean result is kept under the hash of the file's preprocessed source
(comments kept, so a NOLINT edit counts), its clang-tidy command line, the clang-tidy
version and the config (--config-file, or every .clang-tidy above the file), and the next
run skips that file while all of them are unchanged. Failures are never kept, and entries
this run didn't use are removed. cpp-build's rolling-cache-paths keeps the directory
between runs.
"""

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

DIAGNOSTIC = re.compile(r"^(?P<file>.+?):(?P<line>\d+):(?P<col>\d+): (?:error|warning): (?P<message>.+)$")


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-p", dest="build_dir", type=Path, required=True,
                        help="directory with compile_commands.json")
    parser.add_argument("--files", help="regex of the files to check")
    parser.add_argument("--exclude", help="regex of files to leave out")
    parser.add_argument("--config-file", type=Path, help="clang-tidy config for every file")
    parser.add_argument("--cache-dir", type=Path, help="where clean results are kept")
    parser.add_argument("--clang-tidy", default=os.environ.get("CLANG_TIDY", "clang-tidy"))
    parser.add_argument("-j", "--jobs", type=int, default=os.cpu_count() or 1)
    return parser.parse_args(argv)


def select(entries, build_dir, files, exclude):
    root = Path.cwd().resolve()
    chosen = []
    for entry in entries:
        path = Path(entry["directory"], entry["file"]).resolve()
        if files:
            wanted = re.search(files, str(path))
        else:
            wanted = path.is_relative_to(root) and not path.is_relative_to(build_dir)
        if wanted and not (exclude and re.search(exclude, str(path))):
            chosen.append((path, entry))
    return chosen


def preprocess(entry):
    arguments = list(entry["arguments"]) if "arguments" in entry else shlex.split(entry["command"])
    kept, skip = [arguments[0]], False
    for argument in arguments[1:]:
        if skip:
            skip = False
        elif argument == "-o":
            skip = True
        elif argument != "-c":
            kept.append(argument)
    return subprocess.run(kept + ["-E", "-C", "-o", "-"], cwd=entry["directory"], capture_output=True,
                          check=True).stdout


def display(path):
    root = Path.cwd().resolve()
    return path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)


class Run:
    def __init__(self, args, total):
        self.args = args
        self.total = total
        version = subprocess.run([args.clang_tidy, "--version"], capture_output=True, check=True).stdout
        config = args.config_file.read_bytes() if args.config_file else b""
        self.salt = version + config
        self.lock = threading.Lock()
        self.done = 0
        self.cached = 0
        self.failed = []
        self.timings = []
        self.used = set()
        self.annotated = set()

    def report(self, status, name, seconds, output=""):
        with self.lock:
            self.done += 1
            print(f"[{self.done}/{self.total}] {status:7} {name} ({seconds:.1f}s)", flush=True)
            if output:
                print(output, end="" if output.endswith("\n") else "\n", flush=True)
                self.annotate(output)

    def annotate(self, output):
        if os.environ.get("GITHUB_ACTIONS") != "true":
            return
        for line in output.splitlines():
            match = DIAGNOSTIC.match(line)
            # A header's diagnostic comes once from every file that includes it.
            if match and line not in self.annotated:
                self.annotated.add(line)
                file = display(Path(match["file"]).resolve())
                print(f"::error file={file},line={match['line']},col={match['col']}::{match['message']}",
                      flush=True)

    def cache_key(self, path, command, entry):
        digest = hashlib.sha256(self.salt)
        digest.update("\0".join(command).encode())
        if not self.args.config_file:
            # clang-tidy reads the nearest .clang-tidy, and its parents' with InheritParentConfig.
            for directory in path.parents:
                config = directory / ".clang-tidy"
                if config.is_file():
                    digest.update(config.read_bytes())
        try:
            digest.update(preprocess(entry))
        except subprocess.CalledProcessError:
            return None  # clang-tidy reports the same error when it runs
        return digest.hexdigest()

    def check(self, path, entry):
        start = time.monotonic()
        name = display(path)
        command = [self.args.clang_tidy, f"-p={self.args.build_dir}", "-quiet"]
        if self.args.config_file:
            command.append(f"--config-file={self.args.config_file}")
        command.append(str(path))

        key = self.cache_key(path, command, entry) if self.args.cache_dir else None
        if key:
            with self.lock:
                self.used.add(key)
            marker = self.args.cache_dir / key
            if marker.exists():
                marker.touch()
                with self.lock:
                    self.cached += 1
                self.report("cached", name, time.monotonic() - start)
                return

        result = subprocess.run(command, capture_output=True, text=True, check=False)
        seconds = time.monotonic() - start
        with self.lock:
            self.timings.append((seconds, name))
        if result.returncode != 0 or result.stdout.strip():
            with self.lock:
                self.failed.append(name)
            self.report("FAILED", name, seconds, result.stdout + result.stderr)
            return
        if key:
            (self.args.cache_dir / key).touch()
        self.report("checked", name, seconds)

    def prune(self):
        for marker in self.args.cache_dir.iterdir():
            if marker.name not in self.used:
                marker.unlink()

    def summarize(self, elapsed):
        summary = (f"clang-tidy: {self.total} files, {self.cached} cached, {len(self.timings)} checked, "
                   f"{len(self.failed)} failed in {elapsed:.0f}s on {self.args.jobs} jobs")
        slowest = sorted(self.timings, reverse=True)[:5]
        print("\n" + summary)
        for seconds, name in slowest:
            print(f"  {seconds:6.1f}s  {name}")
        if self.failed:
            print("\nFailed:\n  " + "\n  ".join(sorted(self.failed)))

        if step_summary := os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(step_summary, "a", encoding="utf-8") as out:
                out.write(f"### {summary}\n")
                if slowest:
                    out.write("\n| Slowest | Seconds |\n|---|---|\n")
                    out.writelines(f"| `{name}` | {seconds:.1f} |\n" for seconds, name in slowest)
                if self.failed:
                    out.write("\n**Failed:**\n\n")
                    out.writelines(f"- `{name}`\n" for name in sorted(self.failed))
                out.write("\n")


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    args.build_dir = args.build_dir.resolve()
    database = args.build_dir / "compile_commands.json"
    if not database.is_file():
        print(f"ci-clang-tidy: no {database}; configure with CMAKE_EXPORT_COMPILE_COMMANDS=ON", file=sys.stderr)
        return 2
    chosen = select(json.loads(database.read_text()), args.build_dir, args.files, args.exclude)
    if args.cache_dir:
        args.cache_dir.mkdir(parents=True, exist_ok=True)

    run = Run(args, len(chosen))
    start = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(args.jobs) as pool:
        for future in [pool.submit(run.check, path, entry) for path, entry in chosen]:
            future.result()
    if args.cache_dir:
        run.prune()
    run.summarize(time.monotonic() - start)
    return 1 if run.failed else 0


if __name__ == "__main__":
    sys.exit(main())

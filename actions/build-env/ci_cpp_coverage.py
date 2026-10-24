"""Run a C or C++ project's tests under clang's source-based coverage, and gate the result.

On PATH as ci-cpp-coverage after build-env (see build_env.py).

Usage: ci-cpp-coverage --object BINARY [--object BINARY...] [--exclude REGEX...]
                       [--fail-under [METRIC=]PERCENT...] [--profile-dir DIR]
                       [--llvm-cov BINARY] [--llvm-profdata BINARY] -- COMMAND [ARGS...]

The binaries are built with clang's -fprofile-instr-generate -fcoverage-mapping, so every
process they start writes a profile. COMMAND (usually ctest) runs with LLVM_PROFILE_FILE in
--profile-dir, which is emptied first; the profiles are merged, and lines, functions, branches
and regions are counted over the code mapped in every --object. Name the test binaries, and any
other instrumented binary whose code should count though no test runs it: its code counts as
not run. Only files under the current directory count, less those an --exclude matches
(searched in the absolute path).

--fail-under PERCENT sets the threshold for lines, functions and branches, which default to
100; METRIC=PERCENT sets one metric's, regions included, and a later value wins. 0 only
reports. Each file short of 100% in a gated metric is listed, and on GitHub Actions the totals
and those files go on the run's summary page. The exit status is COMMAND's when it fails, else 1 when a
metric is below its threshold.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

METRICS = ("lines", "functions", "branches", "regions")
GATED = ("lines", "functions", "branches")
SUMMARY_FILES = 50


def threshold(value):
    """One --fail-under value: PERCENT for every gated metric, or METRIC=PERCENT."""
    metric, _, percent = value.rpartition("=")
    if metric and metric not in METRICS:
        raise argparse.ArgumentTypeError(f"unknown metric {metric!r}; choose from {', '.join(METRICS)}")
    try:
        number = float(percent)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a percentage: {percent!r}") from None
    return {metric: number} if metric else dict.fromkeys(GATED, number)


def parse_args(argv):
    """The options before --, and the test command after it."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--object", dest="objects", type=Path, action="append", required=True,
                        help="instrumented binary whose code counts; repeat for each")
    parser.add_argument("--exclude", action="append", default=[], help="regex of files to leave out")
    parser.add_argument("--fail-under", dest="thresholds", type=threshold, action="append", default=[],
                        metavar="[METRIC=]PERCENT", help="minimum coverage (default 100)")
    parser.add_argument("--profile-dir", type=Path,
                        default=Path(os.environ.get("RUNNER_TEMP") or tempfile.gettempdir(), "ci-cpp-coverage"))
    parser.add_argument("--llvm-cov", default=os.environ.get("LLVM_COV", "llvm-cov"))
    parser.add_argument("--llvm-profdata", default=os.environ.get("LLVM_PROFDATA", "llvm-profdata"))
    parser.add_argument("command", nargs=argparse.REMAINDER, help="the tests, after --")
    args = parser.parse_args(argv)
    if args.command[:1] == ["--"]:
        args.command = args.command[1:]
    if not args.command:
        parser.error("no test command; give it after --")
    args.fail_under = dict.fromkeys(GATED, 100.0)
    for value in args.thresholds:
        args.fail_under.update(value)
    return args


def run_tests(args):
    """Run the command with each process writing its profile into an emptied profile directory."""
    shutil.rmtree(args.profile_dir, ignore_errors=True)
    args.profile_dir.mkdir(parents=True)
    env = dict(os.environ, LLVM_PROFILE_FILE=str(args.profile_dir / "%m-%p.profraw"))
    try:
        status = subprocess.run(args.command, env=env, check=False).returncode
    except OSError as error:
        print(f"ci-cpp-coverage: {error}", file=sys.stderr)
        return 127
    return 128 - status if status < 0 else status


def export(args):
    """llvm-cov's per-file summaries over the merged profiles."""
    profiles = sorted(str(path) for path in args.profile_dir.glob("*.profraw"))
    merged = args.profile_dir / "merged.profdata"
    subprocess.run([args.llvm_profdata, "merge", "-sparse", *profiles, "-o", str(merged)],
                   check=True, capture_output=True, text=True)
    command = [args.llvm_cov, "export", "-summary-only", f"-instr-profile={merged}", str(args.objects[0])]
    for binary in args.objects[1:]:
        command += ["-object", str(binary)]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)["data"][0]["files"]


def select(files, exclude):
    """Each counted file's display name and summary, sorted by name."""
    root = Path.cwd().resolve()
    chosen = {}
    for entry in files:
        path = Path(root, entry["filename"]).resolve()
        if path.is_relative_to(root) and not any(re.search(pattern, str(path)) for pattern in exclude):
            chosen[path.relative_to(root).as_posix()] = entry["summary"]
    return dict(sorted(chosen.items()))


def percent(counts):
    """Covered as a percentage; nothing to cover is fully covered."""
    return 100.0 * counts["covered"] / counts["count"] if counts["count"] else 100.0


def totals(chosen):
    """Covered and count for each metric, summed over the files."""
    total = {metric: {"covered": 0, "count": 0} for metric in METRICS}
    for summary in chosen.values():
        for metric in METRICS:
            total[metric]["covered"] += summary[metric]["covered"]
            total[metric]["count"] += summary[metric]["count"]
    return total


def cell(counts):
    """A metric as covered/count and percent."""
    return f"{counts['covered']}/{counts['count']} ({percent(counts):.2f}%)"


def partial(fail_under, chosen):
    """The files short of full coverage in a gated metric, most missed lines first."""
    files = [(name, summary) for name, summary in chosen.items()
             if any(percent(summary[metric]) < 100 for metric in fail_under)]
    return sorted(files, key=lambda item: item[1]["lines"]["covered"] - item[1]["lines"]["count"])


def headline(total, fail_under):
    """One line of every metric's total, marking those below their threshold."""
    parts = []
    for metric in METRICS:
        mark = " (below {:g}%)".format(fail_under[metric]) if percent(total[metric]) < fail_under.get(metric, 0) else ""
        parts.append(f"{metric} {percent(total[metric]):.2f}%{mark}")
    return "C++ coverage: " + ", ".join(parts)


def print_report(total, partly, fail_under):
    """The files not fully covered, then the totals, on stdout."""
    if partly:
        print("Not fully covered:")
        for name, summary in partly:
            print(f"  {name}: " + ", ".join(f"{metric} {cell(summary[metric])}" for metric in GATED))
    for metric in METRICS:
        print(f"{metric:>9}: {cell(total[metric])}")
    print(headline(total, fail_under))


def write_summary(total, partly, fail_under):
    """The totals and the files not fully covered, on the run's summary page."""
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if not step_summary:
        return
    with open(step_summary, "a", encoding="utf-8") as out:
        out.write(f"### {headline(total, fail_under)}\n\n| Metric | Covered | Threshold |\n|---|---|---|\n")
        for metric in METRICS:
            minimum = f"{fail_under[metric]:g}%" if metric in fail_under else ""
            out.write(f"| {metric} | {cell(total[metric])} | {minimum} |\n")
        if partly:
            out.write("\n| Not fully covered | Lines | Functions | Branches |\n|---|---|---|---|\n")
            out.writelines(f"| `{name}` | " + " | ".join(cell(summary[metric]) for metric in GATED) + " |\n"
                           for name, summary in partly[:SUMMARY_FILES])
            if len(partly) > SUMMARY_FILES:
                out.write(f"\nand {len(partly) - SUMMARY_FILES} more, listed in the log.\n")
        out.write("\n")


def report(args, files):
    """Print and summarize the coverage, and whether every metric meets its threshold."""
    chosen = select(files, args.exclude)
    total = totals(chosen)
    partly = partial(args.fail_under, chosen)
    print_report(total, partly, args.fail_under)
    write_summary(total, partly, args.fail_under)
    failed = [metric for metric, minimum in args.fail_under.items() if percent(total[metric]) < minimum]
    for metric in failed:
        print(f"::error::C++ {metric} coverage is {percent(total[metric]):.2f}%, "
              f"below {args.fail_under[metric]:g}%", flush=True)
    return 1 if failed else 0


def main(argv=None):
    """Run the tests instrumented, then report and gate their coverage."""
    args = parse_args(sys.argv[1:] if argv is None else argv)
    status = run_tests(args)
    if status != 0:
        print(f"ci-cpp-coverage: the tests failed ({status}); no coverage report", file=sys.stderr)
        return status
    if not any(args.profile_dir.glob("*.profraw")):
        print(f"ci-cpp-coverage: no profiles in {args.profile_dir}; build with clang's "
              "-fprofile-instr-generate -fcoverage-mapping", file=sys.stderr)
        return 1
    try:
        files = export(args)
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"ci-cpp-coverage: {error}\n{getattr(error, 'stderr', '') or ''}", file=sys.stderr)
        return 1
    return report(args, files)


if __name__ == "__main__":
    sys.exit(main())

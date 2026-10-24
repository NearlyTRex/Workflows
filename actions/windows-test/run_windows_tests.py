"""Run a folder of Windows test programs on Windows, under its heap checks.

Usage: run_windows_tests.py BUNDLE [--page-heap] [--timeout SECONDS] [--debuggers DIR]

Every *.exe in BUNDLE runs with BUNDLE as its working directory, so the DLLs and data files
beside it are found. A program built elsewhere, such as one cross-compiled with mingw-w64 and
otherwise only ever run under wine, meets the real Windows loader and heap here.

With --page-heap, each program first gets full page heap through gflags: every allocation ends
against an inaccessible page, so an overrun or a use after free faults at the instruction that
did it instead of quietly corrupting a neighbour. A program that fails is run again under cdb,
which prints the faulting instruction and the stack. gflags and cdb come with the Windows SDK's
debugging tools, which GitHub's Windows runners have. Each program's output is a log group, each
failure an error annotation, and the results go on the run's summary page. Exits non-zero when
any program fails.
"""

import argparse
import glob
import os
import shutil
import subprocess
import sys

# Where the Windows SDK installs the debugging tools, which is not on PATH.
DEBUGGERS = r"C:\Program Files (x86)\Windows Kits\10\Debuggers\x64"

# Run to the first break, which is the fault if there is one, then say what it was and where.
# -G skips the break at exit, so a clean run just ends.
CDB_COMMANDS = "g; .lastevent; .ecxr; kn 40; q"


def find_tool(name, directory):
    """The tool on PATH, else in directory, else None."""
    found = shutil.which(name)
    if found:
        return found
    candidate = os.path.join(directory, name)
    return candidate if os.path.isfile(candidate) else None


def set_page_heap(gflags, program, enable):
    """Turn full page heap on or off for one program, which gflags names by file name."""
    command = [gflags, "/p", "/enable" if enable else "/disable", os.path.basename(program)]
    if enable:
        command.append("/full")
    subprocess.run(command, check=True, capture_output=True, text=True)


def run(command, cwd, timeout):
    """(exit status, or None on a timeout; everything it printed)."""
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, f"timed out after {timeout}s\n"
    return result.returncode, result.stdout + result.stderr


def describe(status):
    """A status as Windows means it: one at or above 0x80000000 is an NTSTATUS exception code."""
    if status is None:
        return "timed out"
    if status >= 0x80000000:
        return f"exception {status:#010x}"
    return f"exit status {status}"


def parse_args(argv):
    """The bundle and the options."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("bundle", help="folder of test programs and the DLLs and data they need")
    parser.add_argument("--page-heap", action="store_true", help="run each program under full page heap")
    parser.add_argument("--timeout", type=int, default=300, help="seconds each program may run")
    parser.add_argument("--debuggers", default=DEBUGGERS, help="where gflags.exe and cdb.exe are when not on PATH")
    return parser.parse_args(argv)


def run_one(program, args, tools):
    """Run one program, and again under cdb if it fails. Returns its status."""
    name = os.path.basename(program)
    if args.page_heap:
        set_page_heap(tools["gflags"], program, True)
    try:
        print(f"::group::{name}", flush=True)
        status, output = run([program], args.bundle, args.timeout)
        print(output, end="", flush=True)
        if status != 0 and tools["cdb"]:
            print(f"--- {name} again under cdb", flush=True)
            _, trace = run([tools["cdb"], "-G", "-lines", "-c", CDB_COMMANDS, program], args.bundle, args.timeout)
            print(trace, end="", flush=True)
        print("::endgroup::", flush=True)
    finally:
        if args.page_heap:
            set_page_heap(tools["gflags"], program, False)
    if status != 0:
        print(f"::error::{name} failed: {describe(status)}", flush=True)
    return status


def summarize(results, page_heap):
    """The results on stdout and, on GitHub Actions, the run's summary page. Returns the exit status."""
    failed = [name for name, status in results if status != 0]
    heading = f"Windows tests: {len(results) - len(failed)} of {len(results)} passed"
    if page_heap:
        heading += " under full page heap"
    print(heading)
    for name in failed:
        print(f"  failed: {name}")
    if step_summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(step_summary, "a", encoding="utf-8") as out:
            out.write(f"### {heading}\n\n| Program | Result |\n|---|---|\n")
            out.writelines(f"| `{name}` | {'passed' if status == 0 else describe(status)} |\n"
                           for name, status in results)
            out.write("\n")
    return 1 if failed else 0


def main(argv=None):
    """Run every program in the bundle and report."""
    args = parse_args(sys.argv[1:] if argv is None else argv)
    programs = sorted(glob.glob(os.path.join(args.bundle, "*.exe")))
    if not programs:
        print(f"run_windows_tests: no programs in {args.bundle}", file=sys.stderr)
        return 2
    tools = {"gflags": find_tool("gflags.exe", args.debuggers), "cdb": find_tool("cdb.exe", args.debuggers)}
    if args.page_heap and not tools["gflags"]:
        print(f"run_windows_tests: --page-heap needs gflags.exe, which is neither on PATH nor in {args.debuggers}",
              file=sys.stderr)
        return 2
    if not tools["cdb"]:
        print("run_windows_tests: no cdb.exe, so a failure gets no stack", file=sys.stderr)
    results = [(os.path.basename(program), run_one(program, args, tools)) for program in programs]
    return summarize(results, args.page_heap)


if __name__ == "__main__":
    sys.exit(main())

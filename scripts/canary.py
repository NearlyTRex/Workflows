"""Run real repos' workflows against an unreleased commit of this library, before releasing it.

Usage:
    canary.py REF REPO [REPO ...] [--no-wait] [--timeout MINUTES]
    canary.py --cleanup REPO [REPO ...]

For each REPO (a path, or the name of a checkout next to this library), pushes a
workflows-canary/<sha> branch off its default branch with every library pin moved to
REF, and opens a draft pull request, so its CI and Security run as they would on a
real change. When its security.yml can be run by hand, that is started on the branch
too: a manual run scans the whole git history, as the weekly one does. Then it waits
for every run and reports how each ended.

REF must already be pushed, since the callers fetch the library from GitHub. The
repos' own checkouts are never touched: the branch is made in a temporary worktree.

--cleanup deletes every workflows-canary/ branch in the repos, which closes their
pull requests.
"""

import argparse
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

LIBRARY_ROOT = Path(__file__).resolve().parent.parent
BRANCH_PREFIX = "workflows-canary/"
# A run is only listed once GitHub has picked up the push, so the first polls
# wait for runs to appear.
POLL_SECONDS = 30
SETTLE_SECONDS = 60
PASSING = {"success", "skipped", "neutral"}

_spec = importlib.util.spec_from_file_location("init_repo", LIBRARY_ROOT / "scripts" / "init_repo.py")
init_repo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(init_repo)


class CanaryError(Exception):
    pass


def run(command, cwd=None):
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise CanaryError(f"{' '.join(command)} failed: {(result.stderr or result.stdout).strip()}")
    return result.stdout.strip()


def library_commit(ref):
    """The commit REF names, after checking GitHub has it."""
    try:
        sha = init_repo.git(LIBRARY_ROOT, "rev-parse", "--verify", f"{ref}^{{commit}}")
    except init_repo.InitError:
        raise CanaryError(f"{ref} is not a commit in {LIBRARY_ROOT}") from None
    library = init_repo.library_name()
    try:
        run(["gh", "api", f"repos/{library}/commits/{sha}", "--silent"])
    except CanaryError:
        raise CanaryError(f"{ref} ({sha[:12]}) is not on GitHub yet; push it first") from None
    return sha


def repo_path(name):
    path = Path(name)
    if not path.is_dir():
        path = LIBRARY_ROOT.parent / name
    if not (path / ".git").exists():
        raise CanaryError(f"{name} is not a git checkout")
    return path.resolve()


def default_branch(repo):
    listed = run(["git", "ls-remote", "--symref", "origin", "HEAD"], cwd=repo)
    if match := re.search(r"^ref: refs/heads/(\S+)\tHEAD$", listed, re.M):
        return match[1]
    raise CanaryError(f"can't tell {repo.name}'s default branch")


def push_canary(repo, ref, sha):
    """Push the canary branch and open its pull request. Returns (branch, commit, dispatched)."""
    base = default_branch(repo)
    branch = BRANCH_PREFIX + sha[:12]
    run(["git", "fetch", "--quiet", "origin", base], cwd=repo)
    with tempfile.TemporaryDirectory() as tmp:
        worktree = Path(tmp) / repo.name
        run(["git", "worktree", "add", "--quiet", "--detach", str(worktree), "FETCH_HEAD"], cwd=repo)
        try:
            changed, _, _ = init_repo.repin(worktree, ref)
            if not changed:
                raise CanaryError(f"{repo.name} has no library pins to move to {ref}")
            run(["git", "commit", "--quiet", "--all", "-m", f"Workflows canary: {ref} ({sha[:12]})"], cwd=worktree)
            commit = run(["git", "rev-parse", "HEAD"], cwd=worktree)
            run(["git", "push", "--quiet", "--force", "origin", f"HEAD:refs/heads/{branch}"], cwd=worktree)
            security = worktree / ".github" / "workflows" / "security.yml"
            dispatchable = security.is_file() and "workflow_dispatch" in security.read_text()
        finally:
            run(["git", "worktree", "remove", "--force", str(worktree)], cwd=repo)

    if not run(["gh", "pr", "list", "--head", branch, "--state", "open", "--json", "url", "--jq", ".[].url"],
               cwd=repo):
        run(["gh", "pr", "create", "--draft", "--base", base, "--head", branch,
             "--title", f"Workflows canary: {ref}",
             "--body", f"Runs this repo's workflows against {init_repo.library_name()}@{sha} ({ref}) before it "
                       "is released. Not for merging: `canary.py --cleanup` deletes it."], cwd=repo)
    if dispatchable:
        run(["gh", "workflow", "run", "security.yml", "--ref", branch], cwd=repo)
    return branch, commit, dispatchable


def runs_for(repo, branch, commit):
    listed = run(["gh", "run", "list", "--branch", branch, "--limit", "50",
                  "--json", "workflowName,event,status,conclusion,url,headSha"], cwd=repo)
    return [r for r in json.loads(listed) if r["headSha"] == commit]


def wait_for_runs(repo, branch, commit, timeout, expect_dispatch):
    """Poll until the branch's runs for commit have all finished; returns them."""
    deadline = time.monotonic() + timeout
    time.sleep(SETTLE_SECONDS)
    while True:
        runs = runs_for(repo, branch, commit)
        started = runs and (not expect_dispatch or any(r["event"] == "workflow_dispatch" for r in runs))
        if started and all(r["status"] == "completed" for r in runs):
            return runs
        if time.monotonic() >= deadline:
            raise CanaryError(f"{repo.name}'s runs didn't finish in time; see its workflows-canary pull request")
        time.sleep(POLL_SECONDS)


def cleanup(repo):
    """Delete every canary branch; GitHub closes their pull requests. Returns the branches."""
    listed = run(["git", "ls-remote", "--heads", "origin", f"refs/heads/{BRANCH_PREFIX}*"], cwd=repo)
    branches = [line.split()[1].removeprefix("refs/heads/") for line in listed.splitlines()]
    for branch in branches:
        run(["git", "push", "--quiet", "origin", "--delete", branch], cwd=repo)
    return branches


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("ref", nargs="?", help="Library branch, tag or commit to try")
    parser.add_argument("repos", nargs="*", metavar="repo")
    parser.add_argument("--cleanup", action="store_true", help="Delete the canary branches instead")
    parser.add_argument("--no-wait", action="store_true", help="Push and open the pull requests, then stop")
    parser.add_argument("--timeout", type=float, default=90, help="Minutes to wait for the runs (default 90)")
    args = parser.parse_args(argv)
    # With --cleanup there is no REF, so the first name argparse took as one is a repo.
    names = ([args.ref] if args.cleanup and args.ref else []) + args.repos
    if not names:
        parser.error("name at least one repo")

    failed = False
    if args.cleanup:
        for name in names:
            label = Path(name).name
            try:
                deleted = cleanup(repo_path(name))
            except CanaryError as e:
                print(f"{label}: error: {e}", file=sys.stderr)
                failed = True
                continue
            print(f"{label}: deleted {', '.join(deleted)}" if deleted else f"{label}: no canary branches")
        return 1 if failed else 0

    try:
        sha = library_commit(args.ref)
    except CanaryError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    # Push them all first, so the repos' runs go at the same time.
    pushed = []
    for name in names:
        label = Path(name).name
        try:
            repo = repo_path(name)
            branch, commit, dispatched = push_canary(repo, args.ref, sha)
        except (CanaryError, init_repo.InitError) as e:
            print(f"{label}: error: {e}", file=sys.stderr)
            failed = True
            continue
        extra = ", and a full Security run" if dispatched else ""
        print(f"{label}: pushed {branch} with a draft pull request{extra}")
        pushed.append((name, repo, branch, commit, dispatched))
    if args.no_wait:
        return 1 if failed else 0

    for name, repo, branch, commit, dispatched in pushed:
        try:
            runs = wait_for_runs(repo, branch, commit, args.timeout * 60, dispatched)
        except CanaryError as e:
            print(f"{repo.name}: error: {e}", file=sys.stderr)
            failed = True
            continue
        print(f"{repo.name}:")
        for r in sorted(runs, key=lambda r: (r["workflowName"], r["event"])):
            ok = r["conclusion"] in PASSING
            failed = failed or not ok
            print(f"  {'ok  ' if ok else 'FAIL'} {r['workflowName']} ({r['event']}): {r['conclusion']}  {r['url']}")
    if pushed:
        print(f"\nWhen done: {Path(__file__).name} --cleanup {' '.join(name for name, *_ in pushed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

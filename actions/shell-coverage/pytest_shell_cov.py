"""Shell statement coverage for pytest suites that run bash scripts.

coverage.py never sees shell. With this plugin loaded and enabled, every
non-interactive bash a test starts sources a hook through BASH_ENV that sends
its xtrace to a per-process file in a trace directory. When the session ends
the traces are measured against the scripts named by --shell-cov-source.

The unit is a statement, not a line: bash reports a command that spans several
lines under its first line or its last depending on the construct, so a
statement counts as run when any traced line falls inside it. Statements are
read from each script's syntax tree, built by tree-sitter-bash (see
requirements.txt next to this file).

Enable with at least one of:
    --shell-cov-source PATH   script or directory (searched for *.sh, *.bash);
                              repeatable; relative to pytest's rootdir
    --shell-cov-report        print the report (sources default to rootdir)
    --shell-cov-fail-under N  fail the run below N percent (implies the report)

Narrow or change what is measured with:
    --shell-cov-exclude GLOB  leave out scripts whose path (relative to rootdir)
                              matches, e.g. vendored ones; repeatable
    --shell-cov-branch        also count the right-hand side of each && and ||

Tests just run bash (`subprocess.run(["bash", "script.sh"])`) and are traced
through the inherited environment. A test that builds its own environment
takes it from the `shell_traced_env` fixture instead. Only bash is traced:
`sh` (dash) does not read BASH_ENV.
"""

import fnmatch
import functools
import os
import re
import shutil
import tempfile

import pytest

SCRIPT_EXTENSIONS = (".bash", ".sh")
SKIPPED_DIRS = {".git", ".hg", ".venv", "venv", "node_modules", "__pycache__"}

# Names of the environment variables the hook and the tests share
TRACE_DIR_VARIABLE = "SHELL_COV_TRACE_DIR"
HOOK_VARIABLE = "BASH_ENV"

# Sourced through BASH_ENV, so every non-interactive bash a test starts, and
# every bash those start, traces itself. A no-op when no trace directory is
# named. Each line is: marks (one + per nesting level), the directory bash
# started in, the script, the line number, all separated by \001 -- the start
# directory is recorded because BASH_SOURCE is relative to it. Needs bash 4.1
# (BASH_XTRACEFD, {var} redirections); older bash, such as macOS's 3.2, is left
# untraced rather than broken.
TRACE_HOOK = """if [ -n "${SHELL_COV_TRACE_DIR:-}" ] && [ "${BASH_VERSINFO[0]:-0}${BASH_VERSINFO[1]:-0}" -ge 41 ]; then
    __shell_cov_cwd=$PWD
    exec {__shell_cov_fd}>>"$SHELL_COV_TRACE_DIR/$$.trace"
    BASH_XTRACEFD=$__shell_cov_fd
    PS4=$'+\\001${__shell_cov_cwd}\\001${BASH_SOURCE[0]:-}\\001${LINENO}\\001'
    set -x
fi
"""

TRACE_LINE = re.compile(
    r"^\++\x01(?P<cwd>[^\x01\n]*)\x01(?P<path>[^\x01\n]+)\x01(?P<line>\d+)\x01(?P<command>[^\n]*)")

# A shell script bigger than this is not one a person wrote; refuse rather than
# scan it. Traces are read a line at a time, so they need no bound.
MAX_SCRIPT_BYTES = 4 * 1024 * 1024

###########################################################
# Statements and branches
###########################################################

# What bash's xtrace prints a line for
SIMPLE = {"command", "test_command", "declaration_command", "unset_command", "variable_assignment"}

# Redirections are never walked: a here-document's body runs nothing, and a
# process substitution there is traced on a different line from the one it is
# written on
REDIRECTS = {"file_redirect", "herestring_redirect", "heredoc_redirect"}

# Wrappers that don't change which command is inside them
WRAPPERS = {"redirected_statement", "negated_command"}

# Commands joined by && || and |
CHAINS = {"list", "pipeline"}

SUBSTITUTIONS = {"command_substitution", "process_substitution"}

# Most characters of a missed branch shown in the report
BRANCH_TEXT_WIDTH = 72


class ShellParseError(Exception):
    """A script that can't be measured: unreadable, too big or not parsable."""


@functools.lru_cache(maxsize=1)
def get_parser():
    try:
        import tree_sitter_bash
        from tree_sitter import Language, Parser
    except ImportError as error:
        raise ShellParseError(
            "shell coverage needs tree-sitter-bash; install it with: pip install --require-hashes --no-deps "
            f"-r {os.path.join(os.path.dirname(os.path.abspath(__file__)), 'requirements.txt')}") from error
    return Parser(Language(tree_sitter_bash.language()))


def line_of(point):
    return point[0] + 1


def first_error(node):
    """The deepest node holding the first syntax error under a node that has one."""
    for child in node.children:
        if child.has_error:
            return first_error(child)
    return node


def read_tree(source):
    """The syntax tree of a script, refusing one the parser can't make sense of."""
    root = get_parser().parse(source.encode()).root_node
    if root.has_error:
        raise ShellParseError(f"tree-sitter-bash cannot parse it (near line {line_of(first_error(root).start_point)})")
    return root


def children_of(node):
    """The named children of a node, comments aside."""
    return [child for child in node.children if child.is_named and child.type != "comment"]


def inner(node):
    """The command inside any redirections and negation, or the node itself."""
    while node.type in WRAPPERS:
        found = [child for child in children_of(node) if child.type not in REDIRECTS]
        if not found:
            break
        node = found[0]
    return node


def is_arithmetic(node):
    # `(( ... ))` is a compound_statement with no braces
    return node.type == "compound_statement" and node.text.startswith(b"((")


def is_simple(node):
    node = inner(node)
    return node.type in SIMPLE or is_arithmetic(node)


def is_multi_line_substitution(node):
    # bash numbers the lines inside a multi-line $( ) unreliably, so the
    # substitution is part of the statement that holds it
    return node.type in SUBSTITUTIONS and node.start_point[0] != node.end_point[0]


def operands(node):
    """The commands of a chain, nested lists and pipelines flattened, in order."""
    core = inner(node)
    if core.type in CHAINS:
        return [operand for child in children_of(core) for operand in operands(child)]
    return [node]


def span(node):
    """(first, last) line of a simple command, its here-document's body not included."""
    end = node.end_point
    for child in node.children:
        if child.type == "heredoc_redirect":
            end = max(next(c for c in child.children if c.type == "heredoc_start").end_point,
                      node.children[0].end_point)
    return line_of(node.start_point), line_of(end)


def find_statements(source):
    """(first, last) lines of each statement, one per logical command: a command
    spanning lines, or joined to the next by a trailing && || or |, is one, and
    anything nested inside it (a substitution in its arguments) is part of it."""
    found = []

    def walk(node):
        if node.type in REDIRECTS or is_multi_line_substitution(node):
            return
        core = inner(node)
        if core.type in CHAINS:
            run = None
            for operand in operands(node):
                if is_simple(operand):
                    lines = span(operand)
                    run = lines if run is None else (run[0], lines[1])
                    walk_arguments(operand)
                    continue
                if run:
                    found.append(run)
                    run = None
                walk(operand)
            if run:
                # still open, so the last operand is a simple command: redirections
                # after a chain can be on a later line
                found.append((run[0], max(run[1], span(node)[1])))
        elif is_simple(node):
            found.append(span(node))
            walk_arguments(node)
        elif node.type == "for_statement" or node.type == "c_style_for_statement":
            children = node.children
            body = next(index for index, child in enumerate(children) if child.type == "do_group")
            found.append((line_of(node.start_point), line_of(children[body - 1].end_point)))
            walk_children(node)
        elif node.type == "case_statement":
            keyword = next(child for child in node.children if child.type == "in")
            found.append((line_of(node.start_point), line_of(keyword.end_point)))
            walk_children(node)
        else:
            walk_children(node)

    def walk_children(node):
        for child in children_of(node):
            walk(child)

    def walk_arguments(node):
        """Statements inside a simple command's substitutions."""
        for child in node.children:
            if is_multi_line_substitution(child) or child.type in REDIRECTS:
                continue
            if child.type in SUBSTITUTIONS:
                walk_children(child)
            elif child.child_count:
                walk_arguments(child)

    walk(read_tree(source))
    outermost = []
    furthest = 0
    for first, last in sorted(set(found), key=lambda lines: (lines[0], -lines[1])):
        if last > furthest:   # otherwise it lies inside one already kept: a substitution within a multi-line command
            outermost.append((first, last))
            furthest = last
    return outermost


def command_key_of(node):
    """(line, key) of the command a list operand starts with, named the way a
    trace names it, or None when it isn't a plain literal word."""
    node = inner(node)
    if is_arithmetic(node):
        return line_of(node.start_point), "(("
    kind = node.type
    if kind == "command":
        names = [child for child in node.children if child.type == "command_name"]
        assignments = [child for child in node.children if child.type == "variable_assignment"]
        if assignments and (not names or assignments[0].start_byte < names[0].start_byte):
            return line_of(assignments[0].start_point), assignments[0].children[0].text.decode() + "="
        if names and names[0].children and names[0].children[0].type == "word":
            return line_of(names[0].start_point), names[0].text.decode()
        return None
    if kind == "variable_assignment":
        return line_of(node.start_point), node.children[0].text.decode() + "="
    if kind == "test_command":
        return line_of(node.start_point), "[[" if node.text.startswith(b"[[") else "["
    if kind == "declaration_command":
        return line_of(node.start_point), node.children[0].text.decode()
    if kind == "unset_command":
        return line_of(node.start_point), "unset"
    if kind in ("pipeline", "subshell", "compound_statement"):
        children = children_of(node)
        return command_key_of(children[0]) if children else None
    return None


def chain_operands(node, operator=None):
    """(operand, the && or || token before it) for a chain of lists, in order."""
    found = []
    for child in node.children:
        if child.type in ("&&", "||"):
            operator = child
        elif child.is_named and child.type != "comment":
            if child.type == "list":
                found += chain_operands(child, operator)
            else:
                found.append((child, operator))
            operator = None
    return found


def find_branches(source):
    """The right-hand side of each && and || a trace can speak for, as dicts with
    the `line` its command is on, the `key` the trace names it by and a `text`
    for the report. Left alone: a command that isn't a literal word, a group or
    subshell over several lines (its body has statements of its own) and a
    command named like an earlier one on the same line (a trace of the line
    can't tell them apart)."""
    lines = source.encode().split(b"\n")
    found = {}

    def walk(node):
        if node.type in REDIRECTS or is_multi_line_substitution(node):
            return
        if node.type == "list" and (node.parent is None or node.parent.type != "list"):
            seen = {}
            for operand, operator in chain_operands(node):
                multi_line_group = (operand.type in ("compound_statement", "subshell")
                                    and operand.start_point[0] != operand.end_point[0])
                named = None if multi_line_group else command_key_of(operand)
                if not named:
                    continue
                line, key = named
                if operator is not None and key not in seen.get(line, ()):
                    row, column = operator.start_point
                    text = lines[row][column:].decode(errors="replace")
                    if text.strip() == operator.type:   # the right-hand side is on the next line
                        text += " " + lines[operand.start_point[0]][operand.start_point[1]:].decode(errors="replace")
                    text = " ".join(text.split())
                    if len(text) > BRANCH_TEXT_WIDTH:
                        text = text[:BRANCH_TEXT_WIDTH - 3] + "..."
                    found[(line, key)] = {"line": line, "key": key, "text": text}
                seen.setdefault(line, set()).add(key)
        for child in children_of(node):
            walk(child)

    walk(read_tree(source))
    return [found[key] for key in sorted(found)]

###########################################################
# Traces and measuring
###########################################################


ASSIGNMENT = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\+?=")


def command_key(text):
    """How a command is named in a trace line, to compare with the source: its
    first word, unquoted ('[' is printed quoted), with an assignment reduced to its name."""
    words = text.split(None, 1)
    if not words:
        return ""
    word = words[0].strip("'\"")
    assignment = ASSIGNMENT.match(word)
    return assignment.group(1) + "=" if assignment else word


def parse_trace(lines, words=False):
    """The lines a trace (text, or an iterable of its lines) says were run, per script (real paths).

    With words, also which commands ran on each line: (hits, {path: {line: {command keys}}}).
    """
    if isinstance(lines, str):
        lines = lines.splitlines()
    hits = {}
    commands = {}
    for text in lines or ():
        match = TRACE_LINE.match(text)
        if not match:
            continue
        path = os.path.realpath(os.path.join(match.group("cwd"), match.group("path")))
        line = int(match.group("line"))
        hits.setdefault(path, set()).add(line)
        commands.setdefault(path, {}).setdefault(line, set()).add(command_key(match.group("command")))
    return (hits, commands) if words else hits


def read_traces(trace_dir, words=False):
    """Merged hits of every trace file in a directory (and, with words, the commands run per line)."""
    merged = {}
    commands = {}
    for name in sorted(os.listdir(trace_dir)):
        with open(os.path.join(trace_dir, name), encoding="utf-8", errors="replace") as handle:
            hits, found = parse_trace(handle, words=True)
        for path, lines in hits.items():
            merged.setdefault(path, set()).update(lines)
        for path, by_line in found.items():
            for line, keys in by_line.items():
                commands.setdefault(path, {}).setdefault(line, set()).update(keys)
    return (merged, commands) if words else merged


def read_script(path):
    with open(path, encoding="utf-8", errors="replace") as handle:
        source = handle.read(MAX_SCRIPT_BYTES + 1)
    if len(source) > MAX_SCRIPT_BYTES:
        raise ShellParseError(f"larger than {MAX_SCRIPT_BYTES} bytes; not a shell script to measure")
    return source


def find_scripts(sources):
    """Scripts named by sources: files as given, directories searched."""
    scripts = set()
    for source in sources:
        if os.path.isfile(source):
            scripts.add(source)
            continue
        for root, dirs, files in os.walk(source):
            dirs[:] = [name for name in dirs if name not in SKIPPED_DIRS]
            scripts.update(os.path.join(root, name) for name in files if name.endswith(SCRIPT_EXTENSIONS))
    return sorted(scripts)


def is_excluded(name, patterns):
    """Whether a script's path (relative to the rootdir) matches any --shell-cov-exclude glob.
    A * matches across directories, so `vendor/*` excludes everything under vendor/."""
    name = name.replace(os.sep, "/")
    return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)


def is_statement_run(statement, lines_run):
    first, last = statement
    return any(first <= line <= last for line in lines_run)


def describe_statement(lines, statement, width=72):
    """A statement's first line, squeezed to one line, for the report."""
    first, last = statement
    text = " ".join(lines[first - 1].split())
    if len(text) > width:
        text = text[:width - 3] + "..."
    return text + " ..." if last > first and not text.endswith("...") else text


def measure_script(source, lines_run, commands=None):
    """Statements run and missed. With commands ({line: {command keys}} for this
    script), the right-hand side of each && / || also counts as a statement of its
    own, run when its command was traced on its line."""
    lines = source.split("\n")
    statements = find_statements(source)
    total = len(statements)
    missed = [(statement, describe_statement(lines, statement))
              for statement in statements if not is_statement_run(statement, lines_run)]
    if commands is not None:
        branches = find_branches(source)
        total += len(branches)
        missed += [((branch["line"], branch["line"]), branch["text"])
                   for branch in branches if branch["key"] not in commands.get(branch["line"], ())]
    missed.sort(key=lambda item: item[0])
    return {"statements": total, "covered": total - len(missed), "missed": [span for span, _ in missed],
            "missed_source": [text for _, text in missed]}


def measure(root, sources, trace_dir, branch=False, exclude=()):
    """Measure every script under sources, bar those matching an exclude glob, against the
    traces; keys are paths relative to root."""
    commands = {}
    if branch:
        hits, commands = read_traces(trace_dir, words=True)
    else:
        hits = read_traces(trace_dir)
    results = {}
    for script in find_scripts(sources):
        name = os.path.relpath(script, root)
        if is_excluded(name, exclude):
            continue
        real = os.path.realpath(script)
        try:
            results[name] = measure_script(
                read_script(script), hits.get(real, set()), commands.get(real, {}) if branch else None)
        except ShellParseError as error:
            raise ShellParseError(f"{name}: {error}") from error
    return results


def get_percent(result):
    if result["statements"] == 0:
        return 100.0
    return 100.0 * result["covered"] / result["statements"]


def get_total_percent(results):
    statements = sum(result["statements"] for result in results.values())
    if statements == 0:
        return 100.0
    return 100.0 * sum(result["covered"] for result in results.values()) / statements


def meets_floor(results, floor):
    return floor is None or get_total_percent(results) >= floor


def format_missed(missed, limit=8):
    spans = [f"{first}" if first == last else f"{first}-{last}" for first, last in missed]
    if len(spans) > limit:
        spans = spans[:limit] + ["..."]
    return ", ".join(spans)


# Most missed statements listed per script, so one badly covered script can't flood the log
MISSED_LISTED = 20


def render_missed(ordered):
    """The statements no test ran, with their source, scripts least covered first."""
    lines = []
    for name, result in ordered:
        missed = list(zip(result["missed"], result["missed_source"]))
        for (first, _), text in missed[:MISSED_LISTED]:
            lines.append(f"  {name}:{first}  {text}")
        if len(missed) > MISSED_LISTED:
            lines.append(f"  {name}: ... and {len(missed) - MISSED_LISTED} more")
    return lines


def render_report(results, floor=None, branch=False):
    """The report, scripts least covered first."""
    width = max([len(name) for name in results] + [len("Script")])
    lines = ["Shell coverage" + (" (branches: the right side of each && / || counts as a statement)" if branch else ""), ""]
    lines.append(f"{'Script':<{width}}  {'Stmts':>5}  {'Miss':>5}  {'Cover':>6}  Missing")
    ordered = sorted(results.items(), key=lambda item: (get_percent(item[1]), item[0]))
    for name, result in ordered:
        lines.append(f"{name:<{width}}  {result['statements']:>5}  {len(result['missed']):>5}  "
                     f"{get_percent(result):>5.1f}%  {format_missed(result['missed'])}")
    missed = render_missed(ordered)
    if missed:
        lines += ["", "Missed statements"] + missed
    total = get_total_percent(results)
    lines.append("")
    lines.append(f"Total: {total:.1f}%")
    if floor is not None:
        lines.append(f"Floor: {floor:.1f}% ({'met' if total >= floor else 'NOT MET'})")
    return "\n".join(lines)

###########################################################
# Plugin
###########################################################


def pytest_addoption(parser):
    group = parser.getgroup("shell coverage")
    group.addoption("--shell-cov-source", action="append", default=[], metavar="PATH",
                    help="shell script or directory of *.sh to measure, relative to rootdir (repeatable)")
    group.addoption("--shell-cov-exclude", action="append", default=[], metavar="GLOB",
                    help="leave out scripts whose path (relative to rootdir) matches this glob, e.g. "
                         "'vendor/*' or '*/gradlew.sh'; repeatable")
    group.addoption("--shell-cov-report", action="store_true",
                    help="report how much of the shell the tests ran")
    group.addoption("--shell-cov-branch", action="store_true",
                    help="count the right-hand side of each && and || as a statement of its own, "
                         "run only when its command was traced")
    group.addoption("--shell-cov-fail-under", type=float, default=None, metavar="N",
                    help="fail when shell coverage is below this percentage (implies the report)")


def is_enabled(config):
    return bool(config.getoption("--shell-cov-source") or config.getoption("--shell-cov-report")
                or config.getoption("--shell-cov-fail-under") is not None)


def is_worker(config):
    # pytest-xdist workers inherit the controller's environment, trace
    # directory included; only the controller measures
    return hasattr(config, "workerinput")


def get_sources(config):
    root = str(config.rootpath)
    sources = [os.path.join(root, source) for source in config.getoption("--shell-cov-source")] or [root]
    for source in sources:
        if not os.path.exists(source):
            raise pytest.UsageError(f"--shell-cov-source {source} does not exist")
    return sources


def pytest_configure(config):
    config.shell_cov_scratch = None
    if not is_enabled(config) or is_worker(config):
        return
    get_sources(config)
    try:
        get_parser()
    except ShellParseError as error:
        raise pytest.UsageError(str(error)) from error
    scratch = tempfile.mkdtemp(prefix="shell-cov-")
    trace_dir = os.path.join(scratch, "traces")
    os.makedirs(trace_dir)
    hook_file = os.path.join(scratch, "trace-hook.sh")
    with open(hook_file, "w", encoding="utf-8") as hook:
        hook.write(TRACE_HOOK)
    config.shell_cov_scratch = scratch
    config.shell_cov_saved_env = {name: os.environ.get(name) for name in (HOOK_VARIABLE, TRACE_DIR_VARIABLE)}
    os.environ[HOOK_VARIABLE] = hook_file
    os.environ[TRACE_DIR_VARIABLE] = trace_dir


def pytest_unconfigure(config):
    scratch = getattr(config, "shell_cov_scratch", None)
    if not scratch:
        return
    for name, value in config.shell_cov_saved_env.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    shutil.rmtree(scratch, ignore_errors=True)


@pytest.fixture
def shell_traced_env():
    """Build an environment for running bash that traces it for coverage.

    Pass the environment a test would otherwise give subprocess (default: a
    copy of os.environ). Without shell coverage enabled it returns it as is.
    """
    def build(env=None):
        traced = dict(os.environ if env is None else env)
        if os.environ.get(TRACE_DIR_VARIABLE):
            traced[HOOK_VARIABLE] = os.environ[HOOK_VARIABLE]
            traced[TRACE_DIR_VARIABLE] = os.environ[TRACE_DIR_VARIABLE]
        return traced
    return build


def pytest_sessionfinish(session, exitstatus):
    config = session.config
    if not getattr(config, "shell_cov_scratch", None):
        return
    trace_dir = os.environ[TRACE_DIR_VARIABLE]
    try:
        results = measure(str(config.rootpath), get_sources(config), trace_dir,
                          branch=config.getoption("--shell-cov-branch"),
                          exclude=config.getoption("--shell-cov-exclude"))
    except ShellParseError as error:
        config.shell_cov_error = str(error)
        if session.exitstatus == 0:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
        return
    config.shell_cov_results = results
    floor = config.getoption("--shell-cov-fail-under")
    if not meets_floor(results, floor) and session.exitstatus == 0:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    error = getattr(config, "shell_cov_error", None)
    if error:
        terminalreporter.write_sep("=", "shell coverage")
        terminalreporter.write_line(f"Could not measure: {error}")
        terminalreporter.write_line("Leave it out with --shell-cov-exclude (shell_cov_exclude in the workflow) "
                                    "if it should not be measured, e.g. a vendored script.")
        return
    results = getattr(config, "shell_cov_results", None)
    if results is None:
        return
    terminalreporter.write_sep("=", "shell coverage")
    terminalreporter.write_line(render_report(
        results, config.getoption("--shell-cov-fail-under"), branch=config.getoption("--shell-cov-branch")))

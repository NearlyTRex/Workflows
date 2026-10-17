"""Cross-check the shell coverage plugin's statements against a real bash parser.

pytest_shell_cov reads statements from a tree-sitter-bash syntax tree, which is
an approximation of bash's grammar, so it can read a script differently from
bash. This asks shfmt (mvdan/sh, an independent parser that follows bash
closely and reports exact line numbers) what bash would trace, and compares:

  dropped  a command starts outside every statement the plugin found
  phantom  a statement holds no command, so bash can never trace it and the
           script can never reach 100%
  split    a command runs past the end of the statement it starts in
  merged   one statement swallowed several independent commands, which makes
           coverage look better than it is

Branches (the right-hand side of && and ||, see --shell-cov-branch) are checked
too, as "branch": every one the plugin tracks must be a real operand at that line
with that command word. The operands it leaves alone (a variable as the command,
a keyword, two commands on one line with the same name) are counted as "skipped".

Use it on your own scripts:

    python tests/shfmt_oracle.py path/to/a.sh path/to/b.sh
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys

# What bash's xtrace prints a line for
SIMPLE = {"CallExpr", "DeclClause", "TestClause", "ArithmCmd", "LetClause"}
HEADERS = {"ForClause", "CaseClause"}   # the `for ...` / `case ... in` line only


def find_shfmt():
    return os.environ.get("SHFMT") or shutil.which("shfmt")


def parse(source, shfmt=None):
    """shfmt's JSON syntax tree for a script, or (None, why) when it can't parse it."""
    shfmt = shfmt or find_shfmt()
    error = ""
    for language in ("auto", "bash"):
        result = subprocess.run([shfmt, "--to-json", "-ln", language], input=source,
                                capture_output=True, text=True, check=False)
        if result.returncode == 0:
            return json.loads(result.stdout), None
        error = result.stderr.strip()
    return None, error


def is_statement(node):
    # shfmt's JSON gives statements no "Type"; they are the dicts holding a "Cmd"
    return "Type" not in node and "Pos" in node and ("Cmd" in node or "Redirs" in node)


def command_type(statement):
    return (statement.get("Cmd") or {}).get("Type")


def operands(statement):
    """The statements joined by &&, || and | in a chain, left to right."""
    command = statement.get("Cmd") or {}
    if command.get("Type") == "BinaryCmd":
        return operands(command["X"]) + operands(command["Y"])
    return [statement]


def statement_end(stmt):
    """The last line of a statement, not counting a here-document's body: shfmt
    runs a statement through its terminator when the here-document comes last."""
    end = stmt["End"]["Line"]
    for redirect in stmt.get("Redirs") or []:
        if redirect.get("Hdoc"):
            end = max([(stmt.get("Cmd") or stmt)["End"]["Line"]] +
                      [r["Word"]["End"]["Line"] for r in stmt["Redirs"] if r.get("Word")])
    return end


def find_units(tree):
    """(first_line, last_line) of each thing bash traces, one per logical statement."""
    units = []

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            if is_statement(node):
                statement(node)
            else:
                for value in node.values():
                    if isinstance(value, (dict, list)):
                        walk(value)

    def statement(stmt):
        run = None   # consecutive simple commands in a chain read as one statement
        for operand in operands(stmt):
            kind = command_type(operand)
            if kind in SIMPLE:
                span = (operand["Pos"]["Line"], statement_end(operand))
                run = span if run is None else (run[0], span[1])
                walk(operand.get("Cmd"))    # substitutions inside hold more statements
                continue
            if run:
                units.append(run)
                run = None
            if kind in HEADERS:
                units.append((operand["Pos"]["Line"], operand["Pos"]["Line"]))
            walk(operand.get("Cmd"))        # bodies and conditions
            # Redirections are not walked: a process substitution there is traced
            # on a different line from the one it is written on
        if run:
            units.append(run)

    walk(tree)
    return sorted(set(units))


def literal(word):
    """The text of a word made only of literal parts, else None."""
    parts = (word or {}).get("Parts") or []
    if parts and all(part.get("Type") == "Lit" for part in parts):
        return "".join(part["Value"] for part in parts)
    return None


def first_command(stmt):
    """(line, key) for the command a statement starts with, as bash's trace names it, or None."""
    cmd = stmt.get("Cmd") or {}
    kind = cmd.get("Type")
    if kind == "BinaryCmd":
        return first_command(cmd["X"])
    if kind == "CallExpr":
        assigns = cmd.get("Assigns") or []
        if assigns:
            return assigns[0]["Pos"]["Line"], assigns[0]["Name"]["Value"] + "="
        args = cmd.get("Args") or []
        text = literal(args[0]) if args else None
        return (args[0]["Pos"]["Line"], text) if text else None
    if kind == "DeclClause":
        return cmd["Pos"]["Line"], (cmd.get("Variant") or {}).get("Value")
    if kind == "TestClause":
        return cmd["Pos"]["Line"], "[["
    if kind == "ArithmCmd":
        return cmd["Pos"]["Line"], "(("
    if kind in ("Block", "Subshell"):
        inner = cmd.get("Stmts") or []
        return first_command(inner[0]) if inner else None
    return None


def find_right_hand_sides(tree):
    """{(line, key)} of the command each && / || right-hand side starts with."""
    found = set()

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            if node.get("Type") == "BinaryCmd" and node.get("Op") in ("&&", "||"):
                start = first_command(node["Y"])
                if start:
                    found.add(start)
            for value in node.values():
                if isinstance(value, (dict, list)):
                    walk(value)

    walk(tree)
    return found


def compare(statements, units):
    """Findings of each kind between the plugin's statements and shfmt's units."""
    findings = {"dropped": [], "phantom": [], "split": [], "merged": []}
    for first, last in units:
        host = next(((a, b) for a, b in statements if a <= first <= b), None)
        if host is None:
            findings["dropped"].append((first, last))
        elif last > host[1]:
            findings["split"].append(((first, last), host))
    for a, b in statements:
        inside = [(s, e) for s, e in units if a <= s <= b]
        if not inside:
            findings["phantom"].append((a, b))
            continue
        outermost = [(s, e) for s, e in inside
                     if not any((s2, e2) != (s, e) and s2 <= s and e <= e2 for s2, e2 in inside)]
        if len({s for s, _ in outermost}) > 1:
            findings["merged"].append(((a, b), sorted({s for s, _ in outermost})))
    return findings


def compare_branches(branches, right_hand_sides):
    """(branch, skipped): the tracked branches that match no real operand, and how
    many real operands the plugin chose not to track."""
    tracked = set(branches)
    return sorted(tracked - right_hand_sides), len(right_hand_sides - tracked)


def cross_check(source, find_statements, shfmt=None, find_branches=None):
    """Findings for one script, or None when shfmt can't parse it.

    With find_branches (source -> [(line, key)]), also a "branch" list of the
    tracked branches that match no real operand."""
    tree, _ = parse(source, shfmt)
    if tree is None:
        return None
    findings = compare(find_statements(source), find_units(tree))
    if find_branches is not None:
        findings["branch"], _ = compare_branches(find_branches(source), find_right_hand_sides(tree))
    return findings


def plugin_branches(plugin):
    """source -> [(line, key)] of the branches the plugin tracks."""
    def find(source):
        return [(branch["line"], branch["key"]) for branch in plugin.find_branches(source)]
    return find


def main(argv=None, find_statements=None, find_branches=None):
    if find_statements is None:
        plugin_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "actions",
                                   "shell-coverage", "pytest_shell_cov.py")
        spec = importlib.util.spec_from_file_location("pytest_shell_cov", plugin_path)
        plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin)
        find_statements = plugin.find_statements
        find_branches = plugin_branches(plugin)
    paths = (sys.argv if argv is None else argv)[1:]
    if not paths or not find_shfmt():
        print("usage: shfmt_oracle.py SCRIPT...   (needs shfmt on PATH, or set SHFMT)")
        return 2
    total = 0
    for path in paths:
        with open(path, encoding="utf-8", errors="replace") as handle:
            source = handle.read()
        findings = cross_check(source, find_statements, find_branches=find_branches)
        if findings is None:
            print(f"{path}: shfmt cannot parse it")
            continue
        lines = source.split("\n")
        for kind, items in findings.items():
            for item in items:
                where = item[0] if isinstance(item[0], tuple) else item
                print(f"{path}:{where[0]}  {kind}  {item}  | {lines[where[0] - 1].strip()[:60]}")
                total += 1
    print(f"{total} finding(s) in {len(paths)} script(s)")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())

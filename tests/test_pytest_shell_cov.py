"""The shell coverage plugin: statements, traces, the report and the pytest hooks."""

import importlib.util
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT / "actions" / "shell-coverage"
spec = importlib.util.spec_from_file_location("pytest_shell_cov", PLUGIN_DIR / "pytest_shell_cov.py")
shell_cov = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shell_cov)


# --- traces and measuring -----------------------------------------------

def trace_line(cwd, path, line, marks="+"):
    return f"{marks}\x01{cwd}\x01{path}\x01{line}\x01 echo\n"


def test_parse_trace_resolves_relative_paths_against_start_directory(tmp_path):
    (tmp_path / "s.sh").write_text("echo\n")
    text = trace_line(tmp_path, "s.sh", 1) + trace_line(tmp_path, "./s.sh", 3, "+++") + "noise\n"
    assert shell_cov.parse_trace(text) == {os.path.realpath(tmp_path / "s.sh"): {1, 3}}
    assert shell_cov.parse_trace(None) == {}


def test_measure_and_report(tmp_path):
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "a.sh").write_text("echo 1\necho 2\n")
    (tmp_path / "b.bash").write_text("echo 1\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "hook.sh").write_text("echo\n")
    (tmp_path / "readme.md").write_text("echo\n")
    traces = tmp_path / "traces"
    traces.mkdir()
    (traces / "1.trace").write_text(trace_line(tmp_path, "bin/a.sh", 1))
    (traces / "2.trace").write_text(trace_line(tmp_path, "b.bash", 1))

    results = shell_cov.measure(str(tmp_path), [str(tmp_path)], str(traces))

    assert sorted(results) == ["b.bash", os.path.join("bin", "a.sh")]
    assert results[os.path.join("bin", "a.sh")] == {
        "statements": 2, "covered": 1, "missed": [(2, 2)], "missed_source": ["echo 2"]}
    assert shell_cov.get_total_percent(results) == pytest.approx(200 / 3)
    assert not shell_cov.meets_floor(results, 100)
    assert shell_cov.meets_floor(results, 60)
    assert shell_cov.meets_floor(results, None)
    report = shell_cov.render_report(results, 100)
    assert "Total: 66.7%" in report
    assert "Floor: 100.0% (NOT MET)" in report
    assert report.index(os.path.join("bin", "a.sh")) < report.index("b.bash")


def test_empty_results_are_fully_covered():
    assert shell_cov.get_total_percent({}) == 100.0
    assert shell_cov.get_percent({"statements": 0, "covered": 0, "missed": []}) == 100.0
    assert "Floor" not in shell_cov.render_report({})


def test_find_scripts_accepts_files(tmp_path):
    script = tmp_path / "x.txt"
    script.write_text("echo\n")
    assert shell_cov.find_scripts([str(script)]) == [str(script)]


def test_format_missed_truncates():
    assert shell_cov.format_missed([(1, 1), (3, 5)]) == "1, 3-5"
    assert shell_cov.format_missed([(n, n) for n in range(1, 12)]).endswith(", ...")


# --- plugin hooks -------------------------------------------------------

def make_config(tmp_path, source=(), report=False, floor=None, worker=False, branch=False, exclude=()):
    options = {"--shell-cov-source": list(source), "--shell-cov-report": report, "--shell-cov-fail-under": floor,
               "--shell-cov-branch": branch, "--shell-cov-exclude": list(exclude)}
    config = SimpleNamespace(rootpath=tmp_path, getoption=options.__getitem__)
    if worker:
        config.workerinput = {}
    return config


def test_configure_is_inert_when_disabled_or_on_a_worker(tmp_path, monkeypatch):
    monkeypatch.delenv("BASH_ENV", raising=False)
    for config in (make_config(tmp_path), make_config(tmp_path, report=True, worker=True)):
        shell_cov.pytest_configure(config)
        assert config.shell_cov_scratch is None
        shell_cov.pytest_unconfigure(config)
        shell_cov.pytest_sessionfinish(SimpleNamespace(config=config), 0)
        shell_cov.pytest_terminal_summary(None, 0, config)
    assert "BASH_ENV" not in os.environ


def test_configure_sets_and_restores_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("BASH_ENV", "/orig")
    monkeypatch.delenv("SHELL_COV_TRACE_DIR", raising=False)
    config = make_config(tmp_path, report=True)
    shell_cov.pytest_configure(config)
    scratch = config.shell_cov_scratch
    assert os.path.isdir(os.environ["SHELL_COV_TRACE_DIR"])
    assert os.environ["BASH_ENV"] != "/orig"
    shell_cov.pytest_unconfigure(config)
    assert os.environ["BASH_ENV"] == "/orig"
    assert "SHELL_COV_TRACE_DIR" not in os.environ
    assert not os.path.exists(scratch)


def test_missing_source_is_a_usage_error(tmp_path):
    with pytest.raises(pytest.UsageError, match="does not exist"):
        shell_cov.pytest_configure(make_config(tmp_path, source=["nope"]))


def test_traced_env_without_tracing_is_unchanged(monkeypatch):
    monkeypatch.delenv("SHELL_COV_TRACE_DIR", raising=False)
    build = shell_cov.shell_traced_env.__wrapped__() if hasattr(shell_cov.shell_traced_env, "__wrapped__") \
        else shell_cov.shell_traced_env.__pytest_wrapped__.obj()
    assert build({"A": "1"}) == {"A": "1"}
    assert build()["PATH"] == os.environ["PATH"]


# --- end to end ---------------------------------------------------------

def run_pytest(project, *args):
    env = {k: v for k, v in os.environ.items() if k not in ("BASH_ENV", "SHELL_COV_TRACE_DIR", "PYTEST_ADDOPTS")}
    env["PYTHONPATH"] = str(PLUGIN_DIR)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "pytest_shell_cov", "-p", "no:cacheprovider", "-q", *args],
        cwd=project, env=env, capture_output=True, text=True, check=False)


@pytest.fixture
def project(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "greet.sh").write_text(textwrap.dedent("""\
        #!/bin/bash
        set -eu
        greet() {
            echo "hello $1"
        }
        if [ "${1:-}" = "loud" ]; then
            greet WORLD
        else
            greet world
        fi
        cat <<EOF
        done
        EOF
        """))
    (tmp_path / "test_greet.py").write_text(textwrap.dedent("""\
        import subprocess

        def test_quiet():
            out = subprocess.run(["bash", "scripts/greet.sh"], capture_output=True, text=True, check=True)
            assert "hello world" in out.stdout

        def test_traced_env(shell_traced_env):
            env = shell_traced_env()
            out = subprocess.run(["bash", "scripts/greet.sh", "loud"], env=env, capture_output=True, text=True,
                                 check=True)
            assert "hello WORLD" in out.stdout
        """))
    return tmp_path


@pytest.mark.skipif(not os.path.exists("/bin/bash") and not os.path.exists("/usr/bin/bash"), reason="needs bash")
def test_end_to_end_full_coverage(project):
    result = run_pytest(project, "--shell-cov-source", "scripts", "--shell-cov-fail-under", "100")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Total: 100.0%" in result.stdout
    assert "Floor: 100.0% (met)" in result.stdout


@pytest.mark.skipif(not os.path.exists("/bin/bash") and not os.path.exists("/usr/bin/bash"), reason="needs bash")
def test_end_to_end_missed_branch_fails_the_floor(project):
    (project / "test_greet.py").write_text(textwrap.dedent("""\
        import subprocess

        def test_quiet():
            subprocess.run(["bash", "scripts/greet.sh"], check=True, capture_output=True)
        """))
    result = run_pytest(project, "--shell-cov-source", "scripts", "--shell-cov-fail-under", "100")
    assert result.returncode == 1, result.stdout + result.stderr
    assert os.path.join("scripts", "greet.sh") in result.stdout
    assert "NOT MET" in result.stdout
    assert "passed" in result.stdout


def test_disabled_by_default(project):
    result = run_pytest(project)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "shell coverage" not in result.stdout


# --- parser branches and in-process hooks ---------------------------------

def unwrap(fixture):
    return getattr(fixture, "__wrapped__", None) or fixture.__pytest_wrapped__.obj


def test_addoption_registers_all_options():
    added = []
    group = type("G", (), {"addoption": lambda self, *a, **k: added.append(a[0])})()
    shell_cov.pytest_addoption(type("P", (), {"getgroup": lambda self, name: group})())
    assert added == ["--shell-cov-source", "--shell-cov-exclude", "--shell-cov-report", "--shell-cov-branch",
                     "--shell-cov-fail-under"]


def test_traced_env_adds_hook_when_enabled(monkeypatch):
    build = unwrap(shell_cov.shell_traced_env)()
    monkeypatch.setenv("BASH_ENV", "/hook")
    monkeypatch.setenv("SHELL_COV_TRACE_DIR", "/traces")
    assert build({"A": "1"}) == {"A": "1", "BASH_ENV": "/hook", "SHELL_COV_TRACE_DIR": "/traces"}


def test_session_finish_measures_gates_and_reports(tmp_path, monkeypatch):
    (tmp_path / "s.sh").write_text("echo 1\necho 2\n")
    config = make_config(tmp_path, source=["s.sh"], floor=100.0)
    shell_cov.pytest_configure(config)
    try:
        session = SimpleNamespace(config=config, exitstatus=0)
        shell_cov.pytest_sessionfinish(session, 0)
        assert session.exitstatus == pytest.ExitCode.TESTS_FAILED
        failed = SimpleNamespace(config=config, exitstatus=pytest.ExitCode.TESTS_FAILED)
        shell_cov.pytest_sessionfinish(failed, 0)
        assert failed.exitstatus == pytest.ExitCode.TESTS_FAILED
        config.shell_cov_results = {}
        config.getoption = make_config(tmp_path, floor=0.0).getoption
        passing = SimpleNamespace(config=config, exitstatus=0)
        shell_cov.pytest_sessionfinish(passing, 0)
        assert passing.exitstatus == 0
        lines = []
        reporter = SimpleNamespace(write_sep=lambda *a, **k: lines.append(a[1]), write_line=lines.append)
        shell_cov.pytest_terminal_summary(reporter, 0, config)
        assert lines[0] == "shell coverage" and "Total:" in lines[1]
    finally:
        shell_cov.pytest_unconfigure(config)


def test_oversized_script_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(shell_cov, "MAX_SCRIPT_BYTES", 10)
    (tmp_path / "big.sh").write_text("echo hello world\n")
    (tmp_path / "traces").mkdir()
    with pytest.raises(shell_cov.ShellParseError, match="big.sh: larger than 10 bytes"):
        shell_cov.measure(str(tmp_path), [str(tmp_path)], str(tmp_path / "traces"))


# --- real-world idioms ----------------------------------------------------
#
# The parser is hand-written, so each regression below was a construct it read
# differently from bash. A statement it reports that bash never traces can
# never be marked as run, so it silently caps a script's coverage below 100%.


# The corpus: scripts of real-world shell idioms, each written so that every
# statement runs. They must reach 100% -- anything less means the parser expects
# a statement bash never reports -- and the parser must find as many statements
# as were written: a regression that merges statements would otherwise *raise*
# coverage. When a corpus script changes on purpose, update its count here.
CORPUS_STATEMENTS = {"idioms": 97, "idioms2": 73}
CORPUS_DIR = PLUGIN_DIR.parent.parent / "tests" / "shell_corpus"


@pytest.mark.skipif(not os.path.exists("/bin/bash") and not os.path.exists("/usr/bin/bash"), reason="needs bash")
@pytest.mark.parametrize("name", sorted(CORPUS_STATEMENTS))
def test_corpus_script_reaches_full_coverage(tmp_path, name):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / f"{name}.sh").write_text((CORPUS_DIR / f"{name}.corpus").read_text())
    (tmp_path / "test_corpus.py").write_text(textwrap.dedent(f"""\
        import subprocess

        def test_corpus():
            subprocess.run(["bash", "scripts/{name}.sh"], capture_output=True, text=True, check=True)
        """))
    result = run_pytest(tmp_path, "--shell-cov-source", "scripts", "--shell-cov-fail-under", "100")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Total: 100.0%" in result.stdout
    assert f"{CORPUS_STATEMENTS[name]:>5}      0  100.0%" in result.stdout, result.stdout


# --- the report names what was missed ----------------------------------------

def test_describe_statement_squeezes_and_marks_multiline_statements():
    lines = ["  echo   a  b", "x=$(", "  y", ")", "echo " + "z" * 100]
    assert shell_cov.describe_statement(lines, (1, 1)) == "echo a b"
    assert shell_cov.describe_statement(lines, (2, 4)) == "x=$( ..."
    assert shell_cov.describe_statement(lines, (5, 5)) == "echo " + "z" * 64 + "..."
    # an already-truncated first line gets no second marker
    assert shell_cov.describe_statement(lines, (5, 6), width=10) == "echo zz..."


def test_report_lists_the_source_of_each_missed_statement(tmp_path):
    (tmp_path / "a.sh").write_text("echo ran\nif [ -z \"$x\" ]; then\n    echo missed\nfi\n")
    traces = tmp_path / "traces"
    traces.mkdir()
    (traces / "1.trace").write_text(trace_line(tmp_path, "a.sh", 1) + trace_line(tmp_path, "a.sh", 2))

    report = shell_cov.render_report(shell_cov.measure(str(tmp_path), [str(tmp_path)], str(traces)))

    assert "Missed statements\n  a.sh:3  echo missed" in report
    assert report.index("Missed statements") < report.index("Total:")


def test_report_has_no_missed_section_when_everything_ran(tmp_path):
    (tmp_path / "a.sh").write_text("echo ran\n")
    traces = tmp_path / "traces"
    traces.mkdir()
    (traces / "1.trace").write_text(trace_line(tmp_path, "a.sh", 1))

    report = shell_cov.render_report(shell_cov.measure(str(tmp_path), [str(tmp_path)], str(traces)))

    assert "Missed statements" not in report


def test_report_caps_the_missed_statements_listed_per_script(tmp_path):
    count = shell_cov.MISSED_LISTED + 3
    (tmp_path / "a.sh").write_text("".join(f"echo {n}\n" for n in range(count)))
    traces = tmp_path / "traces"
    traces.mkdir()
    (traces / "1.trace").write_text("")

    report = shell_cov.render_report(shell_cov.measure(str(tmp_path), [str(tmp_path)], str(traces)))

    assert f"  a.sh:{shell_cov.MISSED_LISTED}  echo {shell_cov.MISSED_LISTED - 1}" in report
    assert f"a.sh:{shell_cov.MISSED_LISTED + 1}" not in report
    assert "a.sh: ... and 3 more" in report


# --- branches: the right-hand side of && and || ---------------------------------


@pytest.mark.parametrize("text, key", [
    (" echo hi", "echo"),
    ("  [ -n x ]", "["),
    ("'[' -n x ']'", "["),
    ("FOO=1 cmd", "FOO="),
    ("exit_code=1", "exit_code="),
    ("list+=(a)", "list="),
    ("((  1 > 0  ))", "(("),
    ("   ", ""),
    ("", ""),
])
def test_command_key(text, key):
    assert shell_cov.command_key(text) == key


def test_parse_trace_can_report_the_commands_run_on_each_line(tmp_path):
    script = os.path.realpath(tmp_path / "s.sh")
    text = (trace_line(tmp_path, "s.sh", 4).replace(" echo", " true") + trace_line(tmp_path, "s.sh", 4)
            + trace_line(tmp_path, "s.sh", 6).replace(" echo", " '[' -n x ']'") + "garbage\n")
    hits, commands = shell_cov.parse_trace(text, words=True)
    assert hits == {script: {4, 6}}
    assert commands == {script: {4: {"true", "echo"}, 6: {"["}}}
    assert shell_cov.parse_trace(text) == hits


def test_read_traces_merges_commands_across_trace_files(tmp_path):
    (tmp_path / "1.trace").write_text(trace_line(tmp_path, "s.sh", 4))
    (tmp_path / "2.trace").write_text(trace_line(tmp_path, "s.sh", 4).replace(" echo", " true"))
    script = os.path.realpath(tmp_path / "s.sh")
    hits, commands = shell_cov.read_traces(str(tmp_path), words=True)
    assert hits == {script: {4}} and commands == {script: {4: {"echo", "true"}}}
    assert shell_cov.read_traces(str(tmp_path)) == hits


def test_measure_script_counts_branches_only_when_given_the_commands_run():
    source = "ok && echo yes\nfalse || echo never\n"
    run = {1, 2}
    plain = shell_cov.measure_script(source, run)
    assert plain == {"statements": 2, "covered": 2, "missed": [], "missed_source": []}
    seen = {1: {"ok", "echo"}, 2: {"false"}}
    branched = shell_cov.measure_script(source, run, seen)
    assert branched["statements"] == 4 and branched["covered"] == 3
    assert branched["missed"] == [(2, 2)] and branched["missed_source"] == ["|| echo never"]


def test_a_statement_that_never_ran_misses_its_branches_too():
    result = shell_cov.measure_script("a && b\n", set(), {})
    assert result["statements"] == 2 and result["covered"] == 0
    assert result["missed"] == [(1, 1), (1, 1)]
    assert result["missed_source"] == ["a && b", "&& b"]


def test_measure_in_branch_mode_reads_the_commands_from_the_traces(tmp_path):
    (tmp_path / "a.sh").write_text("true || echo never\ntrue && echo ran\n")
    traces = tmp_path / "traces"
    traces.mkdir()
    (traces / "1.trace").write_text(
        trace_line(tmp_path, "a.sh", 1).replace(" echo", " true")
        + trace_line(tmp_path, "a.sh", 2).replace(" echo", " true") + trace_line(tmp_path, "a.sh", 2))
    results = shell_cov.measure(str(tmp_path), [str(tmp_path)], str(traces), branch=True)
    assert results["a.sh"]["statements"] == 4 and results["a.sh"]["missed_source"] == ["|| echo never"]
    plain = shell_cov.measure(str(tmp_path), [str(tmp_path)], str(traces))
    assert plain["a.sh"]["statements"] == 2 and plain["a.sh"]["missed"] == []


def test_the_report_says_when_it_counts_branches():
    results = {"a.sh": {"statements": 2, "covered": 1, "missed": [(1, 1)], "missed_source": ["|| echo never"]}}
    report = shell_cov.render_report(results, branch=True)
    assert report.startswith("Shell coverage (branches:")
    assert "a.sh:1  || echo never" in report
    assert shell_cov.render_report(results).startswith("Shell coverage\n")


def test_session_finish_measures_branches_when_asked(tmp_path):
    (tmp_path / "s.sh").write_text("true || echo never\n")
    config = make_config(tmp_path, source=["s.sh"], branch=True)
    shell_cov.pytest_configure(config)
    try:
        trace_dir = os.environ["SHELL_COV_TRACE_DIR"]
        with open(os.path.join(trace_dir, "1.trace"), "w") as handle:
            handle.write(trace_line(tmp_path, "s.sh", 1).replace(" echo", " true"))
        shell_cov.pytest_sessionfinish(SimpleNamespace(config=config, exitstatus=0), 0)
        assert config.shell_cov_results["s.sh"]["statements"] == 2
        lines = []
        reporter = SimpleNamespace(write_sep=lambda *a, **k: None, write_line=lines.append)
        shell_cov.pytest_terminal_summary(reporter, 0, config)
        assert "branches" in lines[0]
    finally:
        shell_cov.pytest_unconfigure(config)


BRANCH_STATEMENTS = 50 + 14   # statements and branches in tests/shell_corpus/branches.corpus


@pytest.mark.skipif(not os.path.exists("/bin/bash") and not os.path.exists("/usr/bin/bash"), reason="needs bash")
def test_branch_corpus_reaches_full_branch_coverage(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "branches.sh").write_text((CORPUS_DIR / "branches.corpus").read_text())
    (tmp_path / "test_branches.py").write_text(textwrap.dedent("""\
        import subprocess

        def test_branches():
            subprocess.run(["bash", "scripts/branches.sh"], capture_output=True, text=True, check=True)
        """))
    result = run_pytest(tmp_path, "--shell-cov-source", "scripts", "--shell-cov-branch", "--shell-cov-fail-under", "100")
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{BRANCH_STATEMENTS:>5}      0  100.0%" in result.stdout, result.stdout


@pytest.mark.skipif(not os.path.exists("/bin/bash") and not os.path.exists("/usr/bin/bash"), reason="needs bash")
def test_branch_mode_catches_a_handler_that_statement_mode_cannot(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "deploy.sh").write_text(textwrap.dedent("""\
        #!/bin/bash
        build() { true; }
        build || { echo "build failed"; exit 1; }
        """))
    (tmp_path / "test_deploy.py").write_text(textwrap.dedent("""\
        import subprocess

        def test_deploy():
            subprocess.run(["bash", "scripts/deploy.sh"], check=True, capture_output=True)
        """))
    statements = run_pytest(tmp_path, "--shell-cov-source", "scripts", "--shell-cov-fail-under", "100")
    assert statements.returncode == 0, statements.stdout + statements.stderr
    branches = run_pytest(tmp_path, "--shell-cov-source", "scripts", "--shell-cov-branch", "--shell-cov-fail-under", "100")
    assert branches.returncode == 1, branches.stdout + branches.stderr
    assert 'deploy.sh:3  || { echo "build failed"; exit 1; }' in branches.stdout
    assert "NOT MET" in branches.stdout


# --- comments and quoting -----------------------------------------------------------




# --- statements, read from the syntax tree -----------------------------------------------

def statements(source):
    return shell_cov.find_statements(textwrap.dedent(source))


@pytest.mark.parametrize("source, expected", [
    ("echo a\n# comment\n\necho b\n", [(1, 1), (4, 4)]),
    ("", []),
    ("# only\n# comments\n", []),
    # a command spanning lines, or joined to the next by a trailing operator, is one statement
    ("cmd one \\\n  two\na &&\n  b\n", [(1, 2), (3, 4)]),
    ("if true; then\n  echo hi\nelse\n  echo no\nfi\n", [(1, 1), (2, 2), (4, 4)]),
    ("f() {\n  echo in\n}\ncase \"$1\" in\n  a) echo a ;;\n  b)\n    echo b\n    ;;\nesac\n",
     [(2, 2), (4, 4), (5, 5), (7, 7)]),
    # a here-document's body runs nothing, wherever it hangs
    ("cat <<EOF > /dev/null\nbody && x\nEOF\necho after\n", [(1, 1), (4, 4)]),
    ("while read -r l; do\n  echo \"$l\"\ndone <<EOF\nbody\nEOF\necho after\n", [(1, 1), (2, 2), (6, 6)]),
    # a multi-line $( ) is part of the statement that holds it; bash numbers its lines unreliably
    ("x=$(\n  echo a\n)\necho b\n", [(1, 3), (4, 4)]),
    ("x=$(echo a)\necho b\n", [(1, 1), (2, 2)]),
    ("diff <(\n  echo a\n) f\necho b\n", [(1, 3), (4, 4)]),
    ("for x in a \\\n  b; do\n  echo $x\ndone\n", [(1, 2), (3, 3)]),
    ("for ((i=0;i<2;i++)); do\n  :\ndone\n", [(1, 1), (2, 2)]),
    ("case $1 in\n  a) echo a ;;\nesac\n", [(1, 1), (2, 2)]),
    ("((i++))\n[[ -n x ]]\nlocal x=1\nunset x\ny=1\n! false\n", [(n, n) for n in range(1, 7)]),
    ("printf 'a\\n' | while read -r l; do\n  echo \"$l\"\ndone\n", [(1, 1), (2, 2)]),
    ("while read -r l; do\n  echo \"$l\"\ndone < <(printf 'a\\n')\necho after\n", [(1, 1), (2, 2), (4, 4)]),
    ("if ! a \\\n  | b; then\n  :\nfi\n", [(1, 2), (3, 3)]),
    ("{\n  echo a\n} > /dev/null\n", [(2, 2)]),
    ("> truncated\necho after\n", [(2, 2)]),             # a redirection alone runs no command
    ("(\n  cd /\n  echo a\n)\n", [(2, 2), (3, 3)]),
    # something nested inside a statement is part of it, not a statement of its own
    ('cmd one \\\n  "$(inner)" \\\n  arg\n', [(1, 3)]),
    ('echo "Usage: `basename $0` <path>\n  the path"\necho next\n', [(1, 2), (3, 3)]),
    ('FOO=$(a) \\\n  BAR=$(b) \\\n  "$@"\n', [(1, 3)]),
    # redirections after a chain's last command can be on a later line
    ("a | b \\\n  > f\necho next\n", [(1, 2), (3, 3)]),
    ("if printf x | f \\\n  >/dev/null; then\n  :\nfi\n", [(1, 2), (3, 3)]),
    # a handler group after a continuation is its own statement, not part of the command
    ('token=$(x) \\\n  || { err "bad"; return 2; }\n', [(1, 1), (2, 2)]),
    # a # inside quotes is text, not a comment
    ('echo "value # not a comment"\necho next\n', [(1, 1), (2, 2)]),
    ("echo $'ansi # not'\necho it\\'s\necho next\n", [(1, 1), (2, 2), (3, 3)]),
])
def test_statements(source, expected):
    assert statements(source) == expected


# The Gradle wrapper (gradlew), vendored into many repos, runs a case as the condition of an if
GRADLEW_CONSTRUCT = """\
for arg do
    if
        case $arg in
          -*) false ;;
          /?*) t=${arg#/} t=/${t%%/*}
               [ -e "$t" ] ;;
          *) false ;;
        esac
    then
        arg=$( cygpath --mixed "$arg" )
    fi
done
"""


@pytest.mark.parametrize("source", [
    GRADLEW_CONSTRUCT,
    "cat <<A > f; cat <<B > f\nx\nA\ny\nB\n",   # two here-documents on one line
    "if true; then\n  echo\n",
    "echo a\nif then\n",
])
def test_a_script_the_parser_cannot_read_is_refused_with_where(source):
    with pytest.raises(shell_cov.ShellParseError, match=r"cannot parse it \(near line \d+\)"):
        shell_cov.find_statements(source)
    with pytest.raises(shell_cov.ShellParseError, match="cannot parse"):
        shell_cov.find_branches(source)


def test_a_missing_tree_sitter_says_how_to_install_it(monkeypatch):
    shell_cov.get_parser.cache_clear()
    monkeypatch.setitem(sys.modules, "tree_sitter_bash", None)
    try:
        with pytest.raises(shell_cov.ShellParseError, match="pip install --require-hashes"):
            shell_cov.find_statements("echo a\n")
    finally:
        shell_cov.get_parser.cache_clear()


# --- branches, read from the syntax tree -------------------------------------------------

def branches(source):
    return [(b["line"], b["key"]) for b in shell_cov.find_branches(textwrap.dedent(source))]


def test_branches_are_the_right_hand_sides_a_trace_can_speak_for():
    assert branches("""\
        a && b || c
        [[ x ]] && echo yes
        grep a f \\
            || exit 1
        ok &&
            run_it
        [ -n "$x" ] || { err "bad"; return 2; }
        if [[ a ]] || [ b ]; then
            :
        fi
        $cmd && echo hi
        f() {
          x && y
        }
        for i in 1; do
          z || w
        done
        case $1 in
          a) [[ x ]] && echo hi ;;
        esac
        """) == [(1, "b"), (1, "c"), (2, "echo"), (4, "exit"), (6, "run_it"), (7, "err"), (8, "["),
                 (11, "echo"), (13, "y"), (16, "w"), (19, "echo")]


@pytest.mark.parametrize("source, expected", [
    ("a && $cmd\n", []),                                  # the command is only known at run time
    ("a && 'quoted word'\n", []),
    ("a && if b; then c; fi\n", []),                      # keywords are never traced
    ("a || {\n  b\n}\n", []),                             # the group's body has statements of its own
    ("a || (\n  b\n)\n", []),
    ("a && a\n", []),                                     # same word, same line: a trace can't tell them apart
    ("[[ a ]] || [[ b ]]\n", []),
    ("FOO=1 a && FOO=2 b\n", []),
    ("a \\\n  || a\n", [(2, "a")]),                       # the same word on another line is fine
    ("[[ a && b ]]\n(( a && b ))\n", []),                 # operators inside a test are not lists
    ("echo \"a && b\" 'c || d' # && e\n", []),
    ("cat <<EOF\na && b\nEOF\nc && d\n", [(4, "d")]),     # a here-document's body is not code
    ("x=$(\n  a && b\n)\n", []),                          # inside a multi-line $( )
    ("x=$(a && b)\n", [(1, "b")]),                        # but not inside a one-line one
    ("a || (cd /tmp)\n", [(1, "cd")]),
    ("a && { b; }\n", [(1, "b")]),
    ("a && ! b\n", [(1, "b")]),
    ("a && (( 1 ))\n", [(1, "((")]),
    ("a || x=1\n", [(1, "x=")]),
    ("a && local y=1\n", [(1, "local")]),
    ("a && unset x\n", [(1, "unset")]),
    ("a && b | c\n", [(1, "b")]),
    ("a && b > f\n", [(1, "b")]),
    ("a & b\n", []),
    ("a && # why\n    b\n", [(2, "b")]),                # a comment between the operator and its operand
    ("echo a\necho b\n", []),
    ("", []),
])
def test_branches_left_alone_and_found(source, expected):
    assert branches(source) == expected


def test_branch_text_starts_at_the_operator_and_is_kept_short():
    text = {b["key"]: b["text"] for b in shell_cov.find_branches("a && b || c\nd &&\n  e f\n" + "g && " + "x" * 90 + "\n")}
    assert text["b"] == "&& b || c"
    assert text["c"] == "|| c"
    assert text["e"] == "&& e f"                    # the right-hand side is on the next line
    assert text["x" * 90].endswith("...") and len(text["x" * 90]) == shell_cov.BRANCH_TEXT_WIDTH


def test_branch_text_copes_with_text_that_is_not_ascii():
    assert shell_cov.find_branches('printf "%s" "é" && echo ok\n')[0]["text"] == "&& echo ok"


# --- a script that cannot be measured -----------------------------------------------------

def test_configure_fails_clearly_when_tree_sitter_is_missing(tmp_path, monkeypatch):
    (tmp_path / "s.sh").write_text("echo a\n")

    def missing():
        raise shell_cov.ShellParseError("needs tree-sitter-bash")

    monkeypatch.setattr(shell_cov, "get_parser", missing)
    with pytest.raises(pytest.UsageError, match="needs tree-sitter-bash"):
        shell_cov.pytest_configure(make_config(tmp_path, source=["s.sh"]))


def test_a_script_that_cannot_be_parsed_fails_the_run_and_says_which(tmp_path):
    (tmp_path / "bad.sh").write_text("if then fi (\n")
    config = make_config(tmp_path, source=["bad.sh"])
    shell_cov.pytest_configure(config)
    try:
        session = SimpleNamespace(config=config, exitstatus=0)
        shell_cov.pytest_sessionfinish(session, 0)
        assert session.exitstatus == pytest.ExitCode.TESTS_FAILED
        assert config.shell_cov_error.startswith("bad.sh: tree-sitter-bash cannot parse it")
        already = SimpleNamespace(config=config, exitstatus=pytest.ExitCode.INTERRUPTED)
        shell_cov.pytest_sessionfinish(already, 0)
        assert already.exitstatus == pytest.ExitCode.INTERRUPTED
        lines = []
        reporter = SimpleNamespace(write_sep=lambda *a, **k: lines.append(a[1]), write_line=lines.append)
        shell_cov.pytest_terminal_summary(reporter, 0, config)
        assert lines[0] == "shell coverage"
        assert lines[1].startswith("Could not measure: bad.sh:")
        assert "--shell-cov-exclude" in lines[2] and "vendored" in lines[2]
    finally:
        shell_cov.pytest_unconfigure(config)


def test_end_to_end_an_unparsable_script_is_named_not_a_traceback(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "bad.sh").write_text("cat <<A > f; cat <<B > f\nx\nA\ny\nB\n")
    (tmp_path / "test_nothing.py").write_text("def test_nothing():\n    pass\n")
    result = run_pytest(tmp_path, "--shell-cov-source", "scripts")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Could not measure: scripts/bad.sh: tree-sitter-bash cannot parse it" in result.stdout
    assert "Traceback" not in result.stdout + result.stderr


# --- leaving scripts out ----------------------------------------------------------------------

@pytest.mark.parametrize("name, patterns, excluded", [
    ("vendor/gradlew.sh", ["vendor/*"], True),
    ("vendor/deep/er/x.sh", ["vendor/*"], True),            # a * crosses directories
    ("scripts/gradlew.sh", ["*/gradlew.sh"], True),
    ("gradlew.sh", ["gradlew.sh"], True),
    ("scripts/a.sh", ["vendor/*", "*/gradlew.sh"], False),
    ("scripts/a.sh", [], False),
    ("myvendor/a.sh", ["vendor/*"], False),                 # anchored at the start of the path
])
def test_is_excluded(name, patterns, excluded):
    assert shell_cov.is_excluded(name, patterns) is excluded


def test_measure_leaves_out_excluded_scripts(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "vendor").mkdir()
    (tmp_path / "scripts" / "a.sh").write_text("echo a\n")
    (tmp_path / "vendor" / "gradlew.sh").write_text(GRADLEW_CONSTRUCT)    # the parser cannot read this
    (tmp_path / "traces").mkdir()
    args = (str(tmp_path), [str(tmp_path / "scripts"), str(tmp_path / "vendor")], str(tmp_path / "traces"))
    with pytest.raises(shell_cov.ShellParseError, match="vendor/gradlew.sh: tree-sitter-bash cannot parse it"):
        shell_cov.measure(*args)
    assert sorted(shell_cov.measure(*args, exclude=["vendor/*"])) == [os.path.join("scripts", "a.sh")]


def test_session_finish_honours_the_exclude_option(tmp_path):
    (tmp_path / "bad.sh").write_text(GRADLEW_CONSTRUCT)
    (tmp_path / "good.sh").write_text("echo a\n")
    config = make_config(tmp_path, source=["."], exclude=["bad.sh"])
    shell_cov.pytest_configure(config)
    try:
        session = SimpleNamespace(config=config, exitstatus=0)
        shell_cov.pytest_sessionfinish(session, 0)
        assert session.exitstatus == 0
        assert list(config.shell_cov_results) == ["good.sh"]
    finally:
        shell_cov.pytest_unconfigure(config)


def vendored_project(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "vendor").mkdir()
    (tmp_path / "scripts" / "greet.sh").write_text("echo hello\n")
    (tmp_path / "vendor" / "gradlew.sh").write_text(GRADLEW_CONSTRUCT)
    (tmp_path / "test_greet.py").write_text(textwrap.dedent("""\
        import subprocess

        def test_greet():
            subprocess.run(["bash", "scripts/greet.sh"], check=True, capture_output=True)
        """))
    return tmp_path


def test_end_to_end_a_vendored_script_the_parser_cannot_read_is_named_and_can_be_excluded(tmp_path):
    project = vendored_project(tmp_path)
    refused = run_pytest(project, "--shell-cov-source", ".")
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "Could not measure: vendor/gradlew.sh: tree-sitter-bash cannot parse it" in refused.stdout
    assert "--shell-cov-exclude" in refused.stdout
    assert "Traceback" not in refused.stdout + refused.stderr

    excluded = run_pytest(project, "--shell-cov-source", ".", "--shell-cov-exclude", "vendor/*",
                          "--shell-cov-fail-under", "100")
    assert excluded.returncode == 0, excluded.stdout + excluded.stderr
    assert "scripts/greet.sh" in excluded.stdout and "gradlew" not in excluded.stdout


def test_end_to_end_missing_tree_sitter_says_how_to_install_it(tmp_path):
    # Shadow the real package with one that cannot be imported, so the plugin sees what
    # a repo that never installed it sees
    blocker = tmp_path / "blocker" / "tree_sitter_bash"
    blocker.mkdir(parents=True)
    (blocker / "__init__.py").write_text("raise ImportError('blocked for this test')\n")
    project = tmp_path / "project"
    project.mkdir()
    (project / "s.sh").write_text("echo a\n")
    (project / "test_nothing.py").write_text("def test_nothing():\n    pass\n")
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(tmp_path / "blocker"), str(PLUGIN_DIR)]))
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "pytest_shell_cov", "-p", "no:cacheprovider", "-q",
         "--shell-cov-source", "s.sh"],
        cwd=project, env=env, capture_output=True, text=True, check=False)
    assert result.returncode == pytest.ExitCode.USAGE_ERROR, result.stdout + result.stderr
    assert "shell coverage needs tree-sitter-bash" in result.stderr
    assert "pip install --require-hashes --no-deps -r" in result.stderr
    assert "actions/shell-coverage/requirements.txt" in result.stderr
    assert "Traceback" not in result.stdout + result.stderr

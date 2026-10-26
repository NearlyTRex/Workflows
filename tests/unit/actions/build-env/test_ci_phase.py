import subprocess
import sys

from script_helpers import ROOT, load_script

ci_phase = load_script("actions/build-env/ci_phase.py")


def test_a_phase_is_a_group_and_a_row(tmp_path, monkeypatch, capfd):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert ci_phase.main(["Configure", sys.executable, "-c", "print('configuring')"]) == 0
    assert ci_phase.main(["Build", sys.executable, "-c", "pass"]) == 0
    out = capfd.readouterr().out
    assert out.index("::group::Configure") < out.index("configuring") < out.index("::endgroup::")
    assert "::error" not in out
    # One table for the step, a row per phase.
    assert summary.read_text() == "| Phase | Time |\n|---|---|\n| Configure | 0m 0s |\n| Build | 0m 0s |\n"


def test_a_table_starts_after_other_summary_content(tmp_path, monkeypatch):
    summary = tmp_path / "summary.md"
    summary.write_text("### Earlier\n\n")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    ci_phase.main(["Test", sys.executable, "-c", "pass"])
    assert summary.read_text() == "### Earlier\n\n| Phase | Time |\n|---|---|\n| Test | 0m 0s |\n"


def test_a_phase_that_writes_to_the_summary_gets_a_new_table(tmp_path, monkeypatch):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    ci_phase.main(["Build", sys.executable, "-c", "pass"])
    writes = f"open({str(summary)!r}, 'a').write('### Tidy\\nall clean')"
    ci_phase.main(["Tidy", sys.executable, "-c", writes])
    ci_phase.main(["Test", sys.executable, "-c", "pass"])
    assert summary.read_text() == ("| Phase | Time |\n|---|---|\n| Build | 0m 0s |\n### Tidy\nall clean"
                                   "\n| Phase | Time |\n|---|---|\n| Tidy | 0m 0s |\n| Test | 0m 0s |\n")


def test_a_failing_phase_is_marked_and_keeps_its_status(tmp_path, monkeypatch, capfd):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert ci_phase.main(["Tidy", sys.executable, "-c", "raise SystemExit(3)"]) == 3
    assert "::error::Tidy failed after 0m 0s" in capfd.readouterr().out
    assert summary.read_text().endswith("| Tidy | 0m 0s (failed) |\n")


def test_a_signal_is_reported_as_a_shell_would(monkeypatch):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert ci_phase.main(["Crash", "sh", "-c", "kill -TERM $$"]) == 128 + 15


def test_the_command_is_found_on_path(tmp_path, monkeypatch, capfd):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    tool = tmp_path / "some-tool"
    tool.write_text(f"#!{sys.executable}\nprint('found')\n")
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{ci_phase.os.pathsep}{ci_phase.os.environ['PATH']}")
    assert ci_phase.main(["Tool", "some-tool"]) == 0
    assert "found" in capfd.readouterr().out


def test_a_missing_command(monkeypatch, capfd):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert ci_phase.main(["Missing", "/no/such/command"]) == 127
    captured = capfd.readouterr()
    assert "ci-phase:" in captured.err and "::endgroup::" in captured.out


def test_usage(capsys):
    assert ci_phase.main(["only-a-name"]) == 2
    assert "usage: ci-phase" in capsys.readouterr().err


def test_durations():
    assert ci_phase.duration(0.4) == "0m 0s"
    assert ci_phase.duration(125.6) == "2m 6s"


def test_reads_the_command_line(monkeypatch):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(sys, "argv", ["ci-phase", "Echo", sys.executable, "-c", "pass"])
    assert ci_phase.main() == 0


def test_runs_as_a_script(tmp_path):
    result = subprocess.run([sys.executable, str(ROOT / "actions" / "build-env" / "ci_phase.py"), "x"],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 2

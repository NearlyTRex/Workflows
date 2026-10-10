"""ccache_stats.py, with a fake ccache on PATH."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "actions" / "ccache-stats" / "ccache_stats.py"
spec = importlib.util.spec_from_file_location("ccache_stats", SCRIPT)
ccache_stats = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ccache_stats)


def run(tmp_path, monkeypatch, capfd, print_stats, maxsize=None):
    fake = tmp_path / "bin" / "ccache"
    fake.parent.mkdir()
    fake.write_text(f"""#!/bin/sh
if [ "$1" = --print-stats ]; then printf '{print_stats}'; else echo "stats: $*"; fi
""")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake.parent}{os.pathsep}{os.environ['PATH']}")
    if maxsize is None:
        monkeypatch.delenv("CCACHE_MAXSIZE", raising=False)
    else:
        monkeypatch.setenv("CCACHE_MAXSIZE", maxsize)
    assert ccache_stats.main() == 0
    return capfd.readouterr().out


@pytest.mark.parametrize("print_stats", ["cache_size_kibibyte\\t100\\ncleanups_performed\\t0\\n",
                                         "cache_size_kibibyte\\t100\\n"])
def test_prints_the_stats_without_a_warning(tmp_path, monkeypatch, capfd, print_stats):
    assert run(tmp_path, monkeypatch, capfd, print_stats) == "stats: --show-stats --verbose\n"


def test_warns_when_the_cache_filled_up(tmp_path, monkeypatch, capfd):
    out = run(tmp_path, monkeypatch, capfd, "direct_cache_hit\\t87\\ncleanups_performed\\t300\\n", "500M")
    assert out.startswith("stats: --show-stats --verbose\n")
    assert "::warning title=ccache is too small::" in out
    assert "cleaned up 300 times" in out and "Raise ccache-max-size (now 500M)" in out


def test_names_the_default_size_when_none_was_set(tmp_path, monkeypatch, capfd):
    assert "(now ccache's default)" in run(tmp_path, monkeypatch, capfd, "cleanups_performed\\t2\\n")


def test_runs_as_a_script(tmp_path, monkeypatch):
    fake = tmp_path / "ccache"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    result = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True,
                            env={**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}"})
    assert result.returncode == 0, result.stderr

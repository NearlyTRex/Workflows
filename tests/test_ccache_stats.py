"""ccache_stats.sh, run as the action runs it, with a fake ccache on PATH."""

import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "actions" / "ccache-stats" / "ccache_stats.sh"


def run(tmp_path, print_stats, **env):
    fake = tmp_path / "bin" / "ccache"
    fake.parent.mkdir()
    fake.write_text(f"""#!/bin/sh
if [ "$1" = --print-stats ]; then printf '{print_stats}'; else echo "stats: $*"; fi
""")
    fake.chmod(0o755)
    result = subprocess.run(["bash", str(SCRIPT)], env={"PATH": f"{fake.parent}:/usr/bin:/bin", **env},
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.parametrize("print_stats", ["cache_size_kibibyte\\t100\\ncleanups_performed\\t0\\n",
                                         "cache_size_kibibyte\\t100\\n"])
def test_prints_the_stats_without_a_warning(tmp_path, print_stats):
    out = run(tmp_path, print_stats)
    assert out == "stats: --show-stats --verbose\n"


def test_warns_when_the_cache_filled_up(tmp_path):
    out = run(tmp_path, "direct_cache_hit\\t87\\ncleanups_performed\\t300\\n", CCACHE_MAXSIZE="500M")
    assert out.startswith("stats: --show-stats --verbose\n")
    assert "::warning title=ccache is too small::" in out
    assert "cleaned up 300 times" in out and "Raise ccache-max-size (now 500M)" in out


def test_names_the_default_size_when_none_was_set(tmp_path):
    out = run(tmp_path, "cleanups_performed\\t2\\n")
    assert "(now ccache's default)" in out

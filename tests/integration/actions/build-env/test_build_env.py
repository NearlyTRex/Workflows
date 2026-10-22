"""build_env.py's launchers, run as a later step runs them."""

import os
import subprocess
import sys

import pytest

from script_helpers import ROOT, load_script

ACTION = ROOT / "actions" / "build-env"
build_env = load_script("actions/build-env/build_env.py")

pytestmark = pytest.mark.requires_binary("bash")


def test_the_launchers_run_the_tools_with_this_python(tmp_path):
    env = {"GITHUB_ENV": str(tmp_path / "env"), "GITHUB_PATH": str(tmp_path / "path"), "GITHUB_WORKSPACE": "/work",
           "NUMBER_OF_PROCESSORS": "4", "RUNNER_TEMP": str(tmp_path / "temp"), "TOOLS": str(ACTION), "RUNNER_OS": "Linux"}
    assert build_env.main(env) == 0
    bin_dir = tmp_path / "temp" / "ci-tools"

    phase = subprocess.run([str(bin_dir / "ci-phase"), "Check", sys.executable, "-c", "print('ran')"],
                           capture_output=True, text=True, check=False, env={"PATH": os.environ["PATH"]})
    tidy = subprocess.run([str(bin_dir / "ci-clang-tidy"), "--help"], capture_output=True, text=True, check=False)

    assert phase.returncode == 0 and "::group::Check\nran\n::endgroup::" in phase.stdout
    assert tidy.returncode == 0 and "compile_commands.json" in tidy.stdout

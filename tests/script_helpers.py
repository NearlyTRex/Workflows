"""The library's scripts, loaded by path: they live in action folders, not an importable package."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_script(relative_path, name=None):
    """The module at relative_path from the repository root, under name or its file's stem."""
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name or path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def written(path):
    """The NAME=value lines a script appended to a GITHUB_ENV file, as a dict."""
    return dict(line.split("=", 1) for line in path.read_text().splitlines())


def finds(*present):
    """A stand-in for importlib's find_spec or shutil.which that finds only the names in present."""
    return lambda name: object() if name in present else None

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("version", ROOT / "actions" / "version" / "version.py")
version = importlib.util.module_from_spec(spec)
spec.loader.exec_module(version)

FILES = {
    "pyproject.toml": ('[tool.other]\nversion = "9.9.9"\n\n[project]\nname = "x"\nversion = "1.2.3"\n\n'
                       '[tool.after]\nversion = "8.8.8"\n'),
    "package.json": '{\n  "name": "x",\n  "version": "1.2.3",\n  "dependencies": {"y": "2.0.0"}\n}\n',
    "CMakeLists.txt": "cmake_minimum_required(VERSION 3.20)\nproject(Thing\n  VERSION 1.2.3\n  LANGUAGES CXX)\n",
    "VERSION": "1.2.3\n",
}


@pytest.mark.parametrize("name", FILES)
def test_read(tmp_path, name):
    path = tmp_path / name
    path.write_text(FILES[name])
    assert version.read(path) == "1.2.3"


@pytest.mark.parametrize("name", FILES)
def test_bump_changes_only_the_version(tmp_path, name):
    path = tmp_path / name
    path.write_text(FILES[name])
    assert version.bump(path, "minor") == "1.3.0"
    assert path.read_text() == FILES[name].replace("1.2.3", "1.3.0")


@pytest.mark.parametrize("spec,expected", [
    ("patch", "1.2.4"), ("minor", "1.3.0"), ("major", "2.0.0"), ("1.4.0", "1.4.0"), ("v3.0.0", "3.0.0"),
])
def test_next_version(spec, expected):
    assert version.next_version("1.2.3", spec) == expected


@pytest.mark.parametrize("spec", ["1.2.3", "1.2.2", "0.9.0", "banana", "1.2", ""])
def test_next_version_refuses(spec):
    with pytest.raises(version.VersionError):
        version.next_version("1.2.3", spec)


@pytest.mark.parametrize("name,text", [
    ("pyproject.toml", '[tool.x]\nversion = "1.0.0"\n'),
    ("pyproject.toml", '[project]\nname = "x"\ndynamic = ["version"]\n'),
    ("CMakeLists.txt", "project(Thing LANGUAGES CXX)\n"),
    ("VERSION", "1.0\n"),
])
def test_missing_version(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text)
    with pytest.raises(version.VersionError):
        version.read(path)


def test_cli(tmp_path, capsys):
    path = tmp_path / "VERSION"
    path.write_text("0.1.0\n")
    assert version.main(["bump", str(path), "patch"]) == 0
    assert capsys.readouterr().out == "0.1.1\n"
    assert version.main(["read", str(path)]) == 0
    assert capsys.readouterr().out == "0.1.1\n"
    assert version.main(["bump", str(path), "0.0.1"]) == 1
    assert version.main(["nonsense"]) == 2

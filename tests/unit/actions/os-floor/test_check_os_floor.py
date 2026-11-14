"""check_os_floor.py, over binaries built in memory and the archives a release uploads."""

import os
import runpy
import sys
import tarfile
import zipfile

import pytest

from binary_helpers import (PLATFORM_IOS, build_elf, build_fat, build_macho, build_pe, build_version,
                            other_command, version_min)
from script_helpers import ROOT, load_script

check_os_floor = load_script("actions/os-floor/check_os_floor.py")


def needs_of(tmp_path, data):
    path = tmp_path / "binary"
    path.write_bytes(data)
    return check_os_floor.file_needs(str(path))


def test_parses_floors_by_kind():
    assert check_os_floor.parse_floors("glibc:2.35  MacOS:11.0") == {"glibc": (2, 35), "macos": (11, 0)}


@pytest.mark.parametrize("word", ["glibc", ":2.35", "glibc:", "glibc:2.x"])
def test_refuses_a_word_that_is_not_kind_and_version(word):
    with pytest.raises(ValueError, match=f"'{word}' is not KIND:VERSION"):
        check_os_floor.parse_floors(f"macos:11.0 {word}")


@pytest.mark.parametrize("wide, little", [(True, True), (False, False)])
def test_reads_every_version_an_elf_needs_by_prefix(tmp_path, wide, little):
    data = build_elf({"libc.so.6": ["GLIBC_2.2.5", "GLIBC_2.34", "GLIBC_PRIVATE"],
                      "libstdc++.so.6": ["GLIBCXX_3.4.30", "CXXABI_1.3.13", "unversioned"]},
                     wide=wide, little=little)
    assert needs_of(tmp_path, data) == {"glibc": [(2, 2, 5), (2, 34)], "glibcxx": [(3, 4, 30)],
                                        "cxxabi": [(1, 3, 13)]}


def test_an_elf_that_needs_no_versions_needs_nothing(tmp_path):
    assert needs_of(tmp_path, build_elf({})) == {}


@pytest.mark.parametrize("wide", [True, False])
def test_reads_a_thin_macho_build_version(tmp_path, wide):
    data = build_macho([other_command(), build_version((11, 0, 1))], wide=wide)
    assert needs_of(tmp_path, data) == {"macos": [(11, 0, 1)]}


def test_reads_the_older_minimum_command(tmp_path):
    assert needs_of(tmp_path, build_macho([version_min((10, 15))])) == {"macos": [(10, 15, 0)]}


def test_a_macho_for_another_platform_names_no_macos(tmp_path):
    assert needs_of(tmp_path, build_macho([build_version((17, 0), platform=PLATFORM_IOS)])) == {}


@pytest.mark.parametrize("wide", [False, True])
def test_reads_each_slice_of_a_universal_binary(tmp_path, wide):
    data = build_fat([build_macho([build_version((11, 0))]), build_macho([]),
                      build_macho([build_version((26, 0))])], wide=wide)
    assert needs_of(tmp_path, data) == {"macos": [(11, 0, 0), (26, 0, 0)]}


@pytest.mark.parametrize("count", [0, 65])
def test_a_class_file_or_empty_fat_header_is_no_binary(tmp_path, count):
    assert needs_of(tmp_path, b"\xca\xfe\xba\xbe" + count.to_bytes(4, "big") + bytes(8)) == {}


@pytest.mark.parametrize("pe32_plus", [True, False])
def test_a_pe_needs_the_newer_of_its_os_and_subsystem_versions(tmp_path, pe32_plus):
    data = build_pe(os_version=(4, 0), subsystem_version=(6, 2), pe32_plus=pe32_plus)
    assert needs_of(tmp_path, data) == {"windows": [(6, 2)]}


@pytest.mark.parametrize("data", [b"MZ" + bytes(0x80), b"MZ" + bytes(8), b"#!/bin/sh\n"])
def test_anything_else_is_no_binary(tmp_path, data):
    assert needs_of(tmp_path, data) == {}


# A release stage: a binary at the top, a library deeper in, a link to it, and text beside them
@pytest.fixture(name="stage")
def fixture_stage(tmp_path):
    root = tmp_path / "app"
    (root / "lib").mkdir(parents=True)
    (root / "app").write_bytes(build_elf({"libc.so.6": ["GLIBC_2.34"], "libstdc++.so.6": ["GLIBCXX_3.4.30"]}))
    (root / "lib" / "libSDL2.so.0").write_bytes(build_elf({"libc.so.6": ["GLIBC_2.17"]}))
    os.symlink("lib/libSDL2.so.0", root / "libSDL2.so")
    (root / "README.md").write_text("readme", encoding="utf-8")
    return root


def run_main(capsys, floors, *paths):
    status = check_os_floor.main({"OS_FLOORS": floors, "OS_FLOOR_PATHS": "\n".join(str(p) for p in paths) + "\n"})
    return status, capsys.readouterr().out.splitlines()


def test_passes_a_directory_within_its_floors(stage, capsys):
    status, lines = run_main(capsys, "glibc:2.35 glibcxx:3.4.30", stage)
    assert status == 0
    assert lines == [f"glibc floor 2.35: highest need 2.34 ({stage}/app)",
                     f"glibcxx floor 3.4.30: highest need 3.4.30 ({stage}/app)"]


@pytest.mark.parametrize("suffix", [".tar.gz", ".tar.xz", ".zip"])
def test_checks_inside_each_archive_a_glob_matches(stage, tmp_path, capsys, suffix):
    (stage / "lib" / "libnew.so").write_bytes(build_elf({"libc.so.6": ["GLIBC_2.43"]}))
    archive = tmp_path / f"app-1.0{suffix}"
    if suffix == ".zip":
        with zipfile.ZipFile(archive, "w") as out:
            out.write(stage / "app", "app/app")
            out.write(stage / "lib" / "libnew.so", "app/lib/libnew.so")
    else:
        with tarfile.open(archive, "w:" + suffix.rsplit(".", 1)[1]) as out:
            out.add(stage, "app")
    status, lines = run_main(capsys, "glibc:2.35", tmp_path / "app-*")
    assert status == 1
    assert lines == [f"glibc floor 2.35: highest need 2.43 ({archive}/app/lib/libnew.so)",
                     f"::error::{archive}/app/lib/libnew.so needs glibc 2.43, above the 2.35 floor"]


def test_a_single_file_is_checked_as_itself(stage, capsys):
    status, lines = run_main(capsys, "glibc:2.30", stage / "app")
    assert status == 1
    assert lines[-1] == f"::error::{stage}/app needs glibc 2.34, above the 2.30 floor"


def test_a_pattern_without_matches_and_an_unchecked_floor_fail(stage, tmp_path, capsys):
    status, lines = run_main(capsys, "glibc:2.35 macos:11.0", stage, tmp_path / "missing-*")
    assert status == 1
    assert lines[1:] == [f"::error::{tmp_path}/missing-* matches nothing",
                         "::error::no binary was checked against the macos floor"]


@pytest.mark.parametrize("env, message", [
    ({"OS_FLOORS": "glibc", "OS_FLOOR_PATHS": "x"}, "::error::OS_FLOORS: 'glibc' is not KIND:VERSION"),
    ({"OS_FLOORS": "glibc:2.35", "OS_FLOOR_PATHS": " \n"}, "::error::OS_FLOORS and OS_FLOOR_PATHS must each"),
    ({}, "::error::OS_FLOORS and OS_FLOOR_PATHS must each"),
])
def test_refuses_settings_it_cannot_use(capsys, env, message):
    assert check_os_floor.main(env) == 1
    assert capsys.readouterr().out.startswith(message)


def test_reads_the_environment_when_run_as_a_script(stage, monkeypatch):
    monkeypatch.setenv("OS_FLOORS", "glibc:2.35")
    monkeypatch.setenv("OS_FLOOR_PATHS", str(stage))
    monkeypatch.setattr(sys, "argv", ["check_os_floor.py"])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(str(ROOT / "actions" / "os-floor" / "check_os_floor.py"), run_name="__main__")
    assert exit_info.value.code == 0

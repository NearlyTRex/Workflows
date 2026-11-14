"""Check that every binary a build ships runs on the oldest system it promises.

Reads OS_FLOORS and OS_FLOOR_PATHS from the environment.

OS_FLOORS       space-separated KIND:VERSION floors. glibc:2.35 and glibcxx:3.4.30 hold
                each ELF's GLIBC_ and GLIBCXX_ symbol versions (any NAME_ prefix works),
                macos:11.0 each Mach-O slice's minimum macOS, and windows:10.0 each PE's
                OS and subsystem versions.
OS_FLOOR_PATHS  newline-separated paths or globs: files, directories, and .zip or .tar
                archives (gzip, bzip2 or xz), which are opened and checked inside.

A binary asks for the system that built it unless told otherwise, so a runner image moving on
raises every floor without a commit, and nothing fails until a player on the old system starts
it. Each floor must be checked against at least one binary, so a path that holds none fails.
"""

import glob
import os
import struct
import sys
import tarfile
import tempfile
import zipfile

SHT_GNU_VERNEED = 0x6ffffffe
LC_VERSION_MIN_MACOSX = 0x24
LC_BUILD_VERSION = 0x32
PLATFORM_MACOS = 1
# Universal binaries share 0xcafebabe with Java class files, whose next word is
# a version number in the forties or more; a fat header counts its slices.
MAX_FAT_SLICES = 30
TAR_SUFFIXES = (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")


def parse_version(text):
    """A dotted version as a tuple of ints; ValueError if a part is not a number."""
    return tuple(int(part) for part in text.split("."))


def format_version(version):
    """A version tuple written back as dotted text."""
    return ".".join(str(part) for part in version)


def trimmed(version):
    """The version without trailing zeros, so 11.0.0 and 11.0 compare equal."""
    parts = list(version)
    while parts and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def newer(version, than):
    """Whether version is newer than than, trailing zeros aside."""
    return trimmed(version) > trimmed(than)


def parse_floors(text):
    """{kind: version} from KIND:VERSION words; ValueError naming the first that isn't one."""
    floors = {}
    for word in text.split():
        kind, _, version = word.partition(":")
        try:
            if not kind:
                raise ValueError
            floors[kind.lower()] = parse_version(version)
        except ValueError:
            raise ValueError(f"'{word}' is not KIND:VERSION") from None
    return floors


def c_string(data, start):
    """The NUL-terminated ASCII string at start."""
    return data[start:data.index(b"\0", start)].decode("ascii")


def elf_layout(data):
    """The ELF's byte order, section table offset, entry size, count and entry format."""
    order = "<" if data[5] == 1 else ">"
    if data[4] == 2:
        shoff, = struct.unpack_from(order + "Q", data, 0x28)
        shentsize, shnum = struct.unpack_from(order + "HH", data, 0x3a)
        return order, shoff, shentsize, shnum, order + "IIQQQQII"
    shoff, = struct.unpack_from(order + "I", data, 0x20)
    shentsize, shnum = struct.unpack_from(order + "HH", data, 0x2e)
    return order, shoff, shentsize, shnum, order + "IIIIIIII"


def verneed_names(data, order, offset, count, strings):
    """Every version name one .gnu.version_r section asks for."""
    names = []
    entry = offset
    for _ in range(count):
        _, aux_count, _, aux, following = struct.unpack_from(order + "HHIII", data, entry)
        aux_entry = entry + aux
        for _ in range(aux_count):
            _, _, _, name, aux_next = struct.unpack_from(order + "IHHII", data, aux_entry)
            names.append(c_string(data, strings + name))
            aux_entry += aux_next
        entry += following
    return names


def elf_needs(data):
    """{prefix: [version, ...]} from the ELF's version needs, prefixes lower-cased."""
    order, shoff, shentsize, shnum, section_format = elf_layout(data)
    sections = [struct.unpack_from(section_format, data, shoff + i * shentsize) for i in range(shnum)]
    needs = {}
    for _, kind, _, _, offset, _, link, count in sections:
        if kind != SHT_GNU_VERNEED:
            continue
        for name in verneed_names(data, order, offset, count, sections[link][4]):
            prefix, _, version = name.rpartition("_")
            if prefix and version.replace(".", "").isdigit():
                needs.setdefault(prefix.lower(), []).append(parse_version(version))
    return needs


def decode_macho_version(packed):
    """A Mach-O xxxx.yy.zz nibble-packed version as (major, minor, patch)."""
    return packed >> 16, (packed >> 8) & 0xff, packed & 0xff


def macho_slice_minimum(data, start):
    """The minimum macOS one Mach-O slice names, or None if it names none."""
    magic, = struct.unpack_from("<I", data, start)
    count, = struct.unpack_from("<I", data, start + 16)
    command = start + (32 if magic == 0xfeedfacf else 28)
    for _ in range(count):
        kind, size = struct.unpack_from("<II", data, command)
        if kind == LC_BUILD_VERSION:
            platform, minimum = struct.unpack_from("<II", data, command + 8)
            if platform == PLATFORM_MACOS:
                return decode_macho_version(minimum)
        elif kind == LC_VERSION_MIN_MACOSX:
            return decode_macho_version(struct.unpack_from("<I", data, command + 8)[0])
        command += size
    return None


def macho_minimums(data):
    """Each slice's minimum macOS: one for a thin file, one per slice for a universal one."""
    magic, = struct.unpack_from(">I", data, 0)
    if magic in (0xcafebabe, 0xcafebabf):
        count, = struct.unpack_from(">I", data, 4)
        entry_size, offset_format = (32, ">Q") if magic == 0xcafebabf else (20, ">I")
        offsets = [struct.unpack_from(offset_format, data, 8 + i * entry_size + 8)[0] for i in range(count)]
    else:
        offsets = [0]
    return [macho_slice_minimum(data, offset) for offset in offsets]


def pe_minimum(data):
    """The newer of a PE's operating system and subsystem versions."""
    optional = struct.unpack_from("<I", data, 0x3c)[0] + 24
    os_version = struct.unpack_from("<HH", data, optional + 40)
    subsystem_version = struct.unpack_from("<HH", data, optional + 48)
    return max(os_version, subsystem_version)


def is_macho(data):
    """Whether data is a thin or universal Mach-O, not a Java class file sharing its magic."""
    if data[:4] in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe"):
        return True
    return (data[:4] in (b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf")
            and 0 < struct.unpack_from(">I", data, 4)[0] <= MAX_FAT_SLICES)


def is_pe(data):
    """Whether data is a PE image: an MZ stub pointing at a PE signature."""
    if data[:2] != b"MZ" or len(data) < 0x40:
        return False
    pe, = struct.unpack_from("<I", data, 0x3c)
    return data[pe:pe + 4] == b"PE\0\0"


def file_needs(path):
    """{kind: [version, ...]} for one file; empty for a file that is no binary."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] == b"\x7fELF":
        return elf_needs(data)
    if is_macho(data):
        minimums = [m for m in macho_minimums(data) if m is not None]
        return {"macos": minimums} if minimums else {}
    if is_pe(data):
        return {"windows": [pe_minimum(data)]}
    return {}


def tree_files(directory, label):
    """(path, label) for every regular file under directory; links name files already seen."""
    for root, _, files in os.walk(directory):
        for name in sorted(files):
            path = os.path.join(root, name)
            if not os.path.islink(path):
                yield path, os.path.join(label, os.path.relpath(path, directory))


def unpack(path, into):
    """Extract an archive into a directory, keeping its entries inside it."""
    if path.endswith(".zip"):
        with zipfile.ZipFile(path) as archive:
            archive.extractall(into)
    else:
        with tarfile.open(path) as archive:
            archive.extractall(into, filter="data")


def binaries(path, scratch):
    """(path, label) for each file a path names: itself, its tree, or an archive's contents."""
    if os.path.isdir(path):
        yield from tree_files(path, path)
    elif path.endswith(TAR_SUFFIXES + (".zip",)):
        into = tempfile.mkdtemp(dir=scratch)
        unpack(path, into)
        yield from tree_files(into, path)
    else:
        yield path, path


def weigh(needs, label, floors, highest, failures):
    """Fold one binary's needs into the highest so far, and add a line for each above its floor."""
    for kind, versions in needs.items():
        if kind not in floors:
            continue
        need = max(versions)
        if kind not in highest or newer(need, highest[kind][0]):
            highest[kind] = (need, label)
        if newer(need, floors[kind]):
            failures.append(f"{label} needs {kind} {format_version(need)}, "
                            f"above the {format_version(floors[kind])} floor")


def check(paths, floors, scratch):
    """The highest need of each kind, as (version, label), and a line for each binary above its floor."""
    highest, failures = {}, []
    for path in paths:
        for binary, label in binaries(path, scratch):
            weigh(file_needs(binary), label, floors, highest, failures)
    return highest, failures


def expand(patterns):
    """Each pattern's matches, sorted; a pattern that matches nothing is returned as its own error."""
    paths, missing = [], []
    for pattern in patterns:
        matches = sorted(glob.glob(pattern))
        paths += matches
        if not matches:
            missing.append(pattern)
    return paths, missing


def main(env=None):
    """Check every binary the paths hold against the floors; 1 when any needs more."""
    env = os.environ if env is None else env
    try:
        floors = parse_floors(env.get("OS_FLOORS", ""))
    except ValueError as error:
        print(f"::error::OS_FLOORS: {error}")
        return 1
    patterns = [line.strip() for line in env.get("OS_FLOOR_PATHS", "").splitlines() if line.strip()]
    if not floors or not patterns:
        print("::error::OS_FLOORS and OS_FLOOR_PATHS must each name at least one")
        return 1
    paths, missing = expand(patterns)
    with tempfile.TemporaryDirectory() as scratch:
        highest, failures = check(paths, floors, scratch)
    failures = [f"{pattern} matches nothing" for pattern in missing] + failures
    for kind, floor in sorted(floors.items()):
        if kind in highest:
            need, label = highest[kind]
            print(f"{kind} floor {format_version(floor)}: highest need {format_version(need)} ({label})")
        else:
            failures.append(f"no binary was checked against the {kind} floor")
    for failure in failures:
        print(f"::error::{failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

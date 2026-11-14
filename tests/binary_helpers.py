"""Small ELF, Mach-O and PE images, holding only what a loader reads to decide the system is new enough."""

import struct

SHT_STRTAB = 3
SHT_GNU_VERNEED = 0x6ffffffe
LC_SEGMENT_64 = 0x19
LC_VERSION_MIN_MACOSX = 0x24
LC_BUILD_VERSION = 0x32
PLATFORM_MACOS = 1
PLATFORM_IOS = 2


def verneed_section(needs, order, string):
    """A .gnu.version_r naming needs = {library: [version name, ...]}."""
    section = bytearray()
    entries = list(needs.items())
    for index, (library, versions) in enumerate(entries):
        aux = bytearray()
        for aux_index, version in enumerate(versions):
            following = 16 if aux_index < len(versions) - 1 else 0
            aux += struct.pack(order + "IHHII", 0, 0, 0, string(version), following)
        following = 16 + len(aux) if index < len(entries) - 1 else 0
        section += struct.pack(order + "HHIII", 1, len(versions), string(library), 16, following) + aux
    return bytes(section)


def build_elf(needs, wide=True, little=True):
    """An ELF whose version needs are needs, after an unrelated string table; ELFCLASS64 when wide."""
    order = "<" if little else ">"
    strings = bytearray(b"\0")

    def string(text):
        strings.extend(text.encode("ascii") + b"\0")
        return len(strings) - len(text) - 1

    verneed = verneed_section(needs, order, string)
    header_size = 64 if wide else 52
    verneed_offset = header_size + len(strings)
    sections_offset = verneed_offset + len(verneed)
    section_format, section_size = (order + "IIQQQQIIQQ", 64) if wide else (order + "IIIIIIIIII", 40)
    sections = [
        struct.pack(section_format, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        struct.pack(section_format, 0, SHT_STRTAB, 0, 0, header_size, len(strings), 0, 0, 1, 0),
        struct.pack(section_format, 0, SHT_GNU_VERNEED, 0, 0, verneed_offset, len(verneed), 1, len(needs), 4, 0),
    ]
    ident = b"\x7fELF" + bytes([2 if wide else 1, 1 if little else 2, 1]) + bytes(9)
    fields = order + ("HHIQQQIHHHHHH" if wide else "HHIIIIIHHHHHH")
    header = ident + struct.pack(fields, 3, 62, 1, 0, 0, sections_offset, 0, header_size, 0, 0,
                                 section_size, len(sections), 1)
    return header + bytes(strings) + verneed + b"".join(sections)


def pack_version(version):
    major, minor, patch = (tuple(version) + (0, 0))[:3]
    return major << 16 | minor << 8 | patch


def build_version(version, platform=PLATFORM_MACOS):
    """An LC_BUILD_VERSION load command."""
    return struct.pack("<IIIIII", LC_BUILD_VERSION, 24, platform, pack_version(version), 0, 0)


def version_min(version):
    """An LC_VERSION_MIN_MACOSX load command, as older linkers wrote."""
    return struct.pack("<IIII", LC_VERSION_MIN_MACOSX, 16, pack_version(version), 0)


def other_command():
    return struct.pack("<II", LC_SEGMENT_64, 8)


def build_macho(commands, wide=True):
    """A thin Mach-O slice holding the given load commands."""
    body = b"".join(commands)
    magic = 0xfeedfacf if wide else 0xfeedface
    header = struct.pack("<IiiIIII", magic, 0, 0, 2, len(commands), len(body), 0)
    return header + (b"\0" * 4 if wide else b"") + body


def build_fat(slices, wide=False):
    """A universal binary of the given slices, with the 64-bit fat header when wide."""
    magic, entry_format = (0xcafebabf, ">iiQQII") if wide else (0xcafebabe, ">iiIII")
    offset = 8 + struct.calcsize(entry_format) * len(slices)
    entries, body = b"", b""
    for data in slices:
        fields = (0, 0, offset + len(body), len(data), 0) + ((0,) if wide else ())
        entries += struct.pack(entry_format, *fields)
        body += data
    return struct.pack(">II", magic, len(slices)) + entries + body


def build_pe(os_version=(0, 0), subsystem_version=(0, 0), pe32_plus=True):
    """A PE header and optional header carrying the given (major, minor) versions."""
    optional = bytearray(240 if pe32_plus else 224)
    struct.pack_into("<H", optional, 0, 0x20b if pe32_plus else 0x10b)
    struct.pack_into("<HH", optional, 40, *os_version)
    struct.pack_into("<HH", optional, 48, *subsystem_version)
    coff = struct.pack("<HHIIIHH", 0x8664, 0, 0, 0, 0, len(optional), 0x22)
    image = bytearray(0x40)
    image[0:2] = b"MZ"
    struct.pack_into("<I", image, 0x3c, 0x40)
    return bytes(image) + b"PE\0\0" + coff + bytes(optional)

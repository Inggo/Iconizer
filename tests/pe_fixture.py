"""Build minimal Windows executables in memory, for icon extraction tests.

Just enough PE for a loader to find a resource section: a DOS stub pointer, a
COFF header, a PE32 optional header whose resource directory points at one
`.rsrc` section, and the three-level resource tree (type -> id -> language).
Nothing here runs; it only has to parse.
"""

from __future__ import annotations

import struct

import cv2
import numpy as np

RT_ICON = 3
RT_GROUP_ICON = 14

_SECTION_RVA = 0x1000
_FILE_ALIGN = 0x200


def _align(value: int, to: int) -> int:
    return (value + to - 1) // to * to


def _resource_section(tree: dict[int, dict[int, bytes]]) -> bytes:
    """Serialise {type: {id: payload}} with one language (en-US) per id."""
    language = 1033
    types = sorted(tree)

    # Layout pass: directories first, then data entries, then payloads.
    offset = 16 + 8 * len(types)
    name_dirs = {}
    for kind in types:
        name_dirs[kind] = offset
        offset += 16 + 8 * len(tree[kind])
    lang_dirs = {}
    for kind in types:
        for rid in sorted(tree[kind]):
            lang_dirs[kind, rid] = offset
            offset += 16 + 8
    data_entries = {}
    for kind in types:
        for rid in sorted(tree[kind]):
            data_entries[kind, rid] = offset
            offset += 16
    payloads = {}
    for kind in types:
        for rid in sorted(tree[kind]):
            offset = _align(offset, 4)
            payloads[kind, rid] = offset
            offset += len(tree[kind][rid])

    section = bytearray(offset)

    def directory(at: int, entries: list[tuple[int, int]]) -> None:
        struct.pack_into("<IIHHHH", section, at, 0, 0, 0, 0, 0, len(entries))
        for index, (key, target) in enumerate(entries):
            struct.pack_into("<II", section, at + 16 + 8 * index, key, target)

    subdir = 0x80000000
    directory(0, [(kind, name_dirs[kind] | subdir) for kind in types])
    for kind in types:
        directory(
            name_dirs[kind],
            [(rid, lang_dirs[kind, rid] | subdir) for rid in sorted(tree[kind])],
        )
        for rid in sorted(tree[kind]):
            directory(lang_dirs[kind, rid], [(language, data_entries[kind, rid])])
            blob = tree[kind][rid]
            # Data entries hold an RVA, not a section offset.
            struct.pack_into(
                "<IIII", section, data_entries[kind, rid],
                _SECTION_RVA + payloads[kind, rid], len(blob), 0, 0,
            )
            section[payloads[kind, rid] : payloads[kind, rid] + len(blob)] = blob
    return bytes(section)


def build_pe(tree: dict[int, dict[int, bytes]] | None) -> bytes:
    """A PE32 image whose only section holds `tree` as resources (or nothing)."""
    rsrc = _resource_section(tree) if tree else b""
    raw_size = _align(max(len(rsrc), 1), _FILE_ALIGN)

    dos = bytearray(0x40)
    dos[0:2] = b"MZ"
    struct.pack_into("<I", dos, 0x3C, 0x40)

    coff = struct.pack("<HHIIIHH", 0x14C, 1, 0, 0, 0, 224, 0x0102)
    directories = [(0, 0)] * 16
    if tree:
        directories[2] = (_SECTION_RVA, len(rsrc))
    optional = struct.pack(
        "<HBBIIIIIIIIIHHHHHHIIIIHHIIIIII",
        0x10B, 14, 0,                      # magic, linker version
        0, raw_size, 0,                    # code / initialised / uninitialised sizes
        0, 0, 0,                           # entry point, base of code, base of data
        0x400000, _SECTION_RVA, _FILE_ALIGN,
        6, 0, 0, 0, 6, 0,                  # OS, image, subsystem versions
        0,                                 # Win32VersionValue
        _SECTION_RVA + _align(raw_size, _SECTION_RVA), _FILE_ALIGN,
        0, 2, 0,                           # checksum, subsystem (GUI), dll flags
        0x100000, 0x1000, 0x100000, 0x1000, 0, 16,
    ) + b"".join(struct.pack("<II", *entry) for entry in directories)
    assert len(optional) == 224

    section = struct.pack(
        "<8sIIIIIIHHI", b".rsrc", len(rsrc), _SECTION_RVA, raw_size, _FILE_ALIGN,
        0, 0, 0, 0, 0x40000040,
    )
    headers = bytes(dos) + b"PE\0\0" + coff + optional + section
    headers += b"\0" * (_FILE_ALIGN - len(headers))
    return headers + rsrc + b"\0" * (raw_size - len(rsrc))


def disc_icon(size: int) -> np.ndarray:
    """BGRA: an opaque dark disc on full transparency."""
    image = np.zeros((size, size, 4), np.uint8)
    rows, cols = np.mgrid[0:size, 0:size]
    disc = (rows - size / 2) ** 2 + (cols - size / 2) ** 2 < (size * 0.35) ** 2
    image[disc] = (40, 30, 20, 255)
    return image


def as_png(bgra: np.ndarray) -> bytes:
    ok, encoded = cv2.imencode(".png", bgra)
    assert ok
    return encoded.tobytes()


def as_dib(bgra: np.ndarray) -> bytes:
    """A 32bpp icon DIB: BITMAPINFOHEADER at double height, bottom-up pixels,
    then a 1bpp AND mask -- the classic (pre-PNG) RT_ICON encoding."""
    height, width = bgra.shape[:2]
    header = struct.pack("<IiiHHIIiiII", 40, width, height * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    pixels = bgra[::-1].tobytes()
    mask_row = _align(width, 32) // 8
    return header + pixels + b"\0" * (mask_row * height)


def icon_resources(images: list[tuple[np.ndarray, str]]) -> dict[int, dict[int, bytes]]:
    """One icon group containing each (BGRA image, "png"|"dib") as an RT_ICON."""
    icons, entries = {}, []
    for rid, (image, encoding) in enumerate(images, start=1):
        blob = as_png(image) if encoding == "png" else as_dib(image)
        icons[rid] = blob
        side = image.shape[0]
        entries.append(struct.pack(
            "<BBBBHHIH", side % 256, side % 256, 0, 0, 1, 32, len(blob), rid
        ))
    group = struct.pack("<HHH", 0, 1, len(entries)) + b"".join(entries)
    return {RT_ICON: icons, RT_GROUP_ICON: {1: group}}

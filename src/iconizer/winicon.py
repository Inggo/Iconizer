"""Pull the application icon out of a Windows executable or DLL."""

from __future__ import annotations

import io
import struct
from pathlib import Path

import numpy as np
import pefile
from PIL import Image

_RT_ICON = pefile.RESOURCE_TYPE["RT_ICON"]
_RT_GROUP_ICON = pefile.RESOURCE_TYPE["RT_GROUP_ICON"]

# GRPICONDIRENTRY (in the executable) and ICONDIRENTRY (in a .ico file) share
# their first twelve bytes; they differ only in how the image is found -- a
# resource id in one, a file offset in the other.
_GROUP_HEADER = struct.Struct("<HHH")
_GROUP_ENTRY = struct.Struct("<BBBBHHIH")
_ICO_ENTRY = struct.Struct("<BBBBHHII")


def is_executable(head: bytes) -> bool:
    """PE, NE and plain DOS executables all start with the DOS stub's magic."""
    return head[:2] == b"MZ"


def _leaves(pe: pefile.PE, type_id: int) -> list[tuple[int | str, bytes]]:
    """(resource id, payload) for every language of every entry of one type."""
    root = getattr(pe, "DIRECTORY_ENTRY_RESOURCE", None)
    if root is None:
        return []
    found = []
    for kind in root.entries:
        if kind.id != type_id or not hasattr(kind, "directory"):
            continue
        for entry in kind.directory.entries:
            if not hasattr(entry, "directory"):
                continue
            key = entry.id if entry.id is not None else str(entry.name)
            for language in entry.directory.entries:
                data = language.data.struct
                found.append((key, pe.get_data(data.OffsetToData, data.Size)))
    return found


def _group_to_ico(group: bytes, images: dict[int | str, bytes]) -> bytes:
    """Reassemble one RT_GROUP_ICON and its RT_ICON images as a .ico file."""
    _, _, count = _GROUP_HEADER.unpack_from(group, 0)
    entries = []
    for index in range(count):
        offset = _GROUP_HEADER.size + index * _GROUP_ENTRY.size
        if offset + _GROUP_ENTRY.size > len(group):
            break
        *fields, _size, icon_id = _GROUP_ENTRY.unpack_from(group, offset)
        # Trust the image's real length, not the group's claim about it.
        if icon_id in images:
            entries.append((fields, images[icon_id]))

    header = _GROUP_HEADER.pack(0, 1, len(entries))
    cursor = len(header) + len(entries) * _ICO_ENTRY.size
    directory, blobs = [], []
    for fields, blob in entries:
        directory.append(_ICO_ENTRY.pack(*fields, len(blob), cursor))
        blobs.append(blob)
        cursor += len(blob)
    return header + b"".join(directory) + b"".join(blobs)


def extract_icon(source: bytes | str | Path) -> np.ndarray:
    """The executable's main icon at its largest size, as a BGRA array.

    BGRA to match what OpenCV hands back for a transparent PNG, so the caller
    flattens both the same way. The main icon is the first icon group, which is
    the one Explorer shows. Raises ValueError when there is nothing to extract.
    """
    try:
        if isinstance(source, (bytes, bytearray, memoryview)):
            pe = pefile.PE(data=bytes(source), fast_load=True)
        else:
            # By name, so pefile memory-maps it: Electron apps and games are
            # hundreds of megabytes, and the icons are a few kilobytes of that.
            pe = pefile.PE(name=str(source), fast_load=True)
    except pefile.PEFormatError as exc:
        raise ValueError("not a Windows executable (no PE header)") from exc

    try:
        pe.parse_data_directories(
            directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]]
        )
        groups = _leaves(pe, _RT_GROUP_ICON)
        images = dict(_leaves(pe, _RT_ICON))
    except pefile.PEFormatError as exc:
        raise ValueError(f"damaged resource section: {exc}") from exc
    finally:
        pe.close()

    if not groups or not images:
        raise ValueError("this executable contains no icon")

    ico = _group_to_ico(groups[0][1], images)
    try:
        with Image.open(io.BytesIO(ico)) as picture:
            # Pillow opens an .ico at its largest size (and, among equal sizes,
            # the deepest colour), which is exactly the one worth tracing.
            rgba = np.asarray(picture.convert("RGBA"))
    except (OSError, SyntaxError, ValueError) as exc:
        raise ValueError(f"the icon inside this executable is unreadable: {exc}") from exc
    return np.ascontiguousarray(rgba[:, :, [2, 1, 0, 3]])

import pytest

from iconizer import iconize, iconize_file, load_image
from iconizer.pipeline import decode_image
from iconizer.winicon import extract_icon

from pe_fixture import as_png, build_pe, disc_icon, icon_resources


@pytest.fixture
def exe_bytes() -> bytes:
    """Two sizes in two encodings, smaller first, so picking the first entry
    or failing to decode a DIB would both be caught."""
    return build_pe(icon_resources([(disc_icon(32), "dib"), (disc_icon(64), "png")]))


def test_largest_icon_is_extracted(exe_bytes):
    bgra = extract_icon(exe_bytes)
    assert bgra.shape == (64, 64, 4)
    assert bgra[0, 0, 3] == 0      # transparent corner
    assert bgra[32, 32, 3] == 255  # opaque disc


def test_dib_encoded_icons_decode_with_alpha():
    bgra = extract_icon(build_pe(icon_resources([(disc_icon(48), "dib")])))
    assert bgra.shape == (48, 48, 4)
    assert bgra[0, 0, 3] == 0
    assert bgra[24, 24].tolist() == [40, 30, 20, 255]


def test_extracted_icon_traces(exe_bytes):
    rgb = decode_image(exe_bytes)
    assert rgb.shape == (64, 64, 3)
    assert rgb[0, 0].tolist() == [255, 255, 255]  # transparency flattened as for PNG
    assert iconize(rgb).paths >= 1


def test_load_image_reads_an_exe_from_disk(tmp_path, exe_bytes):
    source = tmp_path / "App.exe"
    source.write_bytes(exe_bytes)
    assert load_image(source).shape == (64, 64, 3)
    assert iconize_file(source).svg.startswith("<svg")


def test_executable_without_icons_is_reported(tmp_path):
    source = tmp_path / "console.exe"
    source.write_bytes(build_pe(None))
    with pytest.raises(ValueError, match="no icon"):
        load_image(source)


def test_dos_stub_without_pe_header_is_reported():
    with pytest.raises(ValueError, match="not a Windows executable"):
        decode_image(b"MZ" + b"\0" * 200)


def test_ico_files_load_through_pillow(tmp_path):
    """OpenCV has no .ico codec; the Pillow fallback covers it."""
    import struct

    png = as_png(disc_icon(48))
    ico = struct.pack("<HHH", 0, 1, 1) + struct.pack(
        "<BBBBHHII", 48, 48, 0, 0, 1, 32, len(png), 22
    ) + png
    source = tmp_path / "app.ico"
    source.write_bytes(ico)
    assert load_image(source).shape == (48, 48, 3)

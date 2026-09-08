import numpy as np

from iconizer.config import Config, TRACE_RESOLUTION
from iconizer.trace import path_count, trace


def _disc_mask() -> np.ndarray:
    mask = np.zeros((TRACE_RESOLUTION, TRACE_RESOLUTION), dtype=np.uint8)
    rows, cols = np.mgrid[0:TRACE_RESOLUTION, 0:TRACE_RESOLUTION]
    centre = TRACE_RESOLUTION // 2
    mask[(rows - centre) ** 2 + (cols - centre) ** 2 < (centre // 2) ** 2] = 255
    return mask


def test_trace_produces_paths():
    svg = trace(_disc_mask(), Config())
    assert svg.startswith("<svg")
    assert path_count(svg) >= 1


def test_output_is_square_and_colour_inheriting():
    svg = trace(_disc_mask(), Config(size=64))
    assert 'width="64" height="64"' in svg
    assert f'viewBox="0 0 {TRACE_RESOLUTION} {TRACE_RESOLUTION}"' in svg
    assert 'fill="currentColor"' in svg
    assert "#" not in svg, "vtracer's own colours must not survive by default"


def test_explicit_colour_replaces_every_fill():
    svg = trace(_disc_mask(), Config(color="white"))
    assert 'fill="white"' in svg
    assert "currentColor" not in svg


def test_hex_colour_is_accepted():
    assert 'fill="#ff0055"' in trace(_disc_mask(), Config(color="#ff0055"))

"""Turn a binary ink mask into a normalised SVG."""

from __future__ import annotations

import re

import numpy as np
import cv2
import vtracer

from .config import Config, TRACE_RESOLUTION

# Tuned for icons rather than for faithful reproduction: aggressive speckle and
# length filtering, splines over polygons, and low coordinate precision. The
# goal is a handful of smooth paths, not a pixel-accurate trace.
_TRACE_OPTIONS = dict(
    colormode="binary",
    mode="spline",
    filter_speckle=16,
    corner_threshold=70,
    length_threshold=6.0,
    splice_threshold=45,
    path_precision=2,
)

_FILL_RE = re.compile(r'fill="#[0-9a-fA-F]{3,8}"')
_DIMENSION_RE = re.compile(r'^<svg[^>]*>', re.MULTILINE)


def trace(mask: np.ndarray, config: Config) -> str:
    """Trace `mask` (255 = ink) and return icon-ready SVG markup."""
    # vtracer traces dark-on-light, so ink must become black.
    rgb = cv2.cvtColor(255 - mask, cv2.COLOR_GRAY2RGB)
    ok, buffer = cv2.imencode(".png", rgb)
    if not ok:
        raise RuntimeError("failed to encode mask for tracing")

    svg = vtracer.convert_raw_image_to_svg(
        bytes(buffer), img_format="png", **_TRACE_OPTIONS
    )
    return _normalise(svg, config)


def _normalise(svg: str, config: Config) -> str:
    """Rewrite vtracer's output into something usable as an icon asset.

    Three changes matter: the XML prolog and generator comment go, so the markup
    can be pasted inline into HTML; a viewBox at the requested size makes the
    icon scale; and every fill becomes `config.color`, which defaults to
    `currentColor` so the icon inherits colour from surrounding CSS.
    """
    start = svg.index("<svg")
    svg = svg[start:]
    # config.color is pattern-validated in Config.__post_init__, so it is safe
    # to interpolate into an attribute here.
    svg = _FILL_RE.sub(f'fill="{config.color}"', svg)

    size = config.size
    header = (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{size}" height="{size}" '
        f'viewBox="0 0 {TRACE_RESOLUTION} {TRACE_RESOLUTION}" fill="{config.color}">'
    )
    svg = _DIMENSION_RE.sub(header, svg, count=1)
    return svg.strip() + "\n"


def path_count(svg: str) -> int:
    """How many paths the icon is made of -- the practical complexity metric."""
    return svg.count("<path")

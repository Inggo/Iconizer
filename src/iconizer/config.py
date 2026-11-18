"""Tunable knobs for the icon pipeline."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Mode = Literal["silhouette", "outline"]

# Colours are interpolated straight into SVG markup, so the accepted shapes are
# restricted rather than escaped: a keyword, or a hex triplet. Anything able to
# carry a quote or an angle bracket out of a form field is rejected outright.
COLOR_PATTERN = re.compile(r"^(currentColor|#[0-9a-fA-F]{3,8}|[a-zA-Z]{3,20})$")

# Detail level -> working resolution, in pixels.
#
# This is *the* lever in the whole pipeline. Everything is extracted at this
# resolution, so it caps how much detail can physically survive into the trace:
# a 192px mask cannot express more than a handful of shapes no matter how the
# tracer is tuned. Raising it is how you get more detail; lowering it is how you
# get something that still reads at 16px.
DETAIL_RESOLUTIONS = {1: 128, 2: 192, 3: 320, 4: 512, 5: 768}

# Resolution the mask is upsampled to before tracing. Higher than any working
# resolution, so the tracer sees smooth edges rather than the staircase left by
# thresholding a small mask.
TRACE_RESOLUTION = 1024


@dataclass(frozen=True)
class CropBox:
    """A square region of the source image, in source pixel coordinates."""

    x: int
    y: int
    size: int

    def __post_init__(self) -> None:
        if self.size <= 0:
            raise ValueError("crop size must be positive")

    def clamped(self, width: int, height: int) -> "CropBox":
        """Fit the box to a width x height image, keeping it square.

        The box may be as large as the image's *long* side, so a landscape
        image can sit whole and centred in a square; along an axis the box
        overhangs, the overhang is padding (see `crop.apply_box`). The rule per
        axis: a box that fits stays inside the image, and a box that does not
        fit must still cover the image along that axis -- it can never slide
        off into pure padding.

        Size is fitted first: the offset range depends on it.
        """
        size = max(1, min(self.size, max(width, height)))

        def place(offset: int, extent: int) -> int:
            low, high = sorted((0, extent - size))
            return int(min(max(offset, low), high))

        return CropBox(x=place(self.x, width), y=place(self.y, height), size=size)


@dataclass(frozen=True)
class Config:
    detail: int = 3
    mode: Mode = "silhouette"
    size: int = 512
    """Side length written into the output SVG's viewBox."""

    stroke_width: float = 0.02
    """Outline mode only, as a fraction of the working resolution."""

    color: str = "currentColor"
    """Ink colour. The default inherits from surrounding CSS; a literal colour
    (``white``, ``#fff``) is what you want for an icon viewed standalone, since
    ``currentColor`` resolves to black with no CSS around it."""

    invert: bool | None = None
    """Force figure/ground polarity. None = decide automatically."""

    smart_crop: bool = True
    """Centre the square crop on the busiest region instead of the frame.
    Ignored when `crop` names an explicit region."""

    crop: CropBox | None = None
    """Explicit crop region. None = choose one automatically."""

    def __post_init__(self) -> None:
        if not COLOR_PATTERN.match(self.color):
            raise ValueError(f"unsupported colour: {self.color!r}")

    def working_resolution(self) -> int:
        if self.detail not in DETAIL_RESOLUTIONS:
            raise ValueError(
                f"detail must be one of {sorted(DETAIL_RESOLUTIONS)}, got {self.detail}"
            )
        return DETAIL_RESOLUTIONS[self.detail]

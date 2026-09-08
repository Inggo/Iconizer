"""Tunable knobs for the icon pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Mode = Literal["silhouette", "outline"]

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
class Config:
    detail: int = 3
    mode: Mode = "silhouette"
    size: int = 512
    """Side length written into the output SVG's viewBox."""

    stroke_width: float = 0.02
    """Outline mode only, as a fraction of the working resolution."""

    invert: bool | None = None
    """Force figure/ground polarity. None = decide automatically."""

    smart_crop: bool = True
    """Centre the square crop on the busiest region instead of the frame."""

    def working_resolution(self) -> int:
        if self.detail not in DETAIL_RESOLUTIONS:
            raise ValueError(
                f"detail must be one of {sorted(DETAIL_RESOLUTIONS)}, got {self.detail}"
            )
        return DETAIL_RESOLUTIONS[self.detail]

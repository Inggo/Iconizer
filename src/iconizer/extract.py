"""Reduce a square image to a binary ink mask: 255 where the icon has ink."""

from __future__ import annotations

import numpy as np
import cv2

from .config import Config, TRACE_RESOLUTION


def _border_is_inked(mask: np.ndarray) -> bool:
    """True when the mask has marked the *frame* rather than the subject."""
    band = max(2, mask.shape[0] // 16)
    border = np.concatenate(
        [
            mask[:band].ravel(),
            mask[-band:].ravel(),
            mask[:, :band].ravel(),
            mask[:, -band:].ravel(),
        ]
    )
    return bool((border > 0).mean() > 0.5)


def silhouette(square: np.ndarray, config: Config) -> np.ndarray:
    """Filled shapes: threshold the subject away from its background.

    Otsu picks the split point but not which side is the subject, so polarity is
    settled separately -- see `_border_is_inked`. Getting this backwards is the
    single most visible failure mode of the whole pipeline (you get the negative
    of the icon), which is why `Config.invert` exists as a manual override.
    """
    resolution = config.working_resolution()
    gray = cv2.cvtColor(
        cv2.resize(square, (resolution, resolution), interpolation=cv2.INTER_AREA),
        cv2.COLOR_RGB2GRAY,
    )
    # Edge-preserving blur: kills texture that would otherwise become speckle,
    # without softening the subject outline we are about to threshold on.
    gray = cv2.bilateralFilter(gray, 9, 90, 90)

    _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)

    invert = _border_is_inked(mask) if config.invert is None else config.invert
    if invert:
        mask = 255 - mask

    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    return mask


def outline(square: np.ndarray, config: Config) -> np.ndarray:
    """Line art: the contour of the silhouette, as a band of ink.

    Derived from the silhouette rather than from an edge detector. Canny/XDoG on
    a photograph finds every texture edge and traces into hundreds of unusable
    paths; the silhouette boundary is closed, few, and reads as a drawing.
    """
    filled = silhouette(square, config)
    thickness = max(1, int(round(config.stroke_width * filled.shape[0])))
    # Elliptical, not square: a square kernel leaves visibly blocky corners on
    # every curve once the mask is upsampled for tracing.
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (thickness * 2 + 1, thickness * 2 + 1)
    )
    return cv2.morphologyEx(filled, cv2.MORPH_GRADIENT, kernel)


def ink_mask(square: np.ndarray, config: Config) -> np.ndarray:
    """Ink mask for `config.mode`, upsampled to tracing resolution."""
    extractor = {"silhouette": silhouette, "outline": outline}[config.mode]
    mask = extractor(square, config)
    mask = cv2.resize(
        mask, (TRACE_RESOLUTION, TRACE_RESOLUTION), interpolation=cv2.INTER_CUBIC
    )
    # Blur *then* threshold. Upsampling a small mask leaves a staircase along
    # every diagonal; blurring turns those steps into a ramp and the threshold
    # cuts a smooth curve through it. Without this the traced paths are visibly
    # pixelated at the resolution the icon is actually viewed at.
    blur = max(3, (TRACE_RESOLUTION // config.working_resolution()) | 1)
    mask = cv2.GaussianBlur(mask, (0, 0), blur)
    _, mask = cv2.threshold(mask, 127, 255, cv2.THRESH_BINARY)
    return mask

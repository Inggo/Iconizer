"""Reduce an arbitrary image to the square the icon should be made from."""

from __future__ import annotations

import numpy as np
import cv2

_SALIENCY_RESOLUTION = 128


def energy_map(rgb: np.ndarray) -> np.ndarray:
    """Coarse 'where is the interesting stuff' map, normalised to 0..1.

    Gradient magnitude, heavily blurred. Not real saliency -- it has no notion
    of objects -- but it reliably separates a textured subject from flat sky or
    studio backdrop, which is all the crop needs.
    """
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    small = cv2.resize(
        gray, (_SALIENCY_RESOLUTION, _SALIENCY_RESOLUTION), interpolation=cv2.INTER_AREA
    )
    gx = cv2.Sobel(small, cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(small, cv2.CV_32F, 0, 1)
    magnitude = cv2.GaussianBlur(cv2.magnitude(gx, gy), (0, 0), 6)
    return magnitude / (float(magnitude.max()) + 1e-6)


def square_crop(rgb: np.ndarray, smart: bool = True) -> np.ndarray:
    """Crop to the largest square, centred on the subject when `smart`.

    The square is always the full short side, so nothing is scaled away; the
    only decision is where along the long axis to take it.
    """
    height, width = rgb.shape[:2]
    side = min(height, width)
    if height == width:
        return rgb

    if not smart:
        top, left = (height - side) // 2, (width - side) // 2
        return rgb[top : top + side, left : left + side]

    energy = energy_map(rgb)
    rows, cols = np.mgrid[0 : energy.shape[0], 0 : energy.shape[1]]
    total = float(energy.sum()) + 1e-6
    # Centroid of energy, in map space, rescaled to full-resolution pixels.
    centre_y = float((energy * rows).sum()) / total / energy.shape[0] * height
    centre_x = float((energy * cols).sum()) / total / energy.shape[1] * width

    top = int(np.clip(centre_y - side / 2, 0, height - side))
    left = int(np.clip(centre_x - side / 2, 0, width - side))
    return rgb[top : top + side, left : left + side]

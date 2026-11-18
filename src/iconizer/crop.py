"""Reduce an arbitrary image to the square the icon should be made from."""

from __future__ import annotations

import numpy as np
import cv2

from .config import CropBox

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


def auto_box(rgb: np.ndarray, smart: bool = True) -> CropBox:
    """The crop the automatic path would choose.

    Always the full short side, so nothing is scaled away; the only decision is
    where along the long axis to take it. Exposed separately from the cropping
    itself so the UI can seed a draggable box with the automatic answer instead
    of making the user start from scratch.
    """
    height, width = rgb.shape[:2]
    side = min(height, width)

    if height == width:
        return CropBox(x=0, y=0, size=side)

    if not smart:
        return CropBox(x=(width - side) // 2, y=(height - side) // 2, size=side)

    energy = energy_map(rgb)
    rows, cols = np.mgrid[0 : energy.shape[0], 0 : energy.shape[1]]
    total = float(energy.sum()) + 1e-6
    # Centroid of energy, in map space, rescaled to full-resolution pixels.
    centre_y = float((energy * rows).sum()) / total / energy.shape[0] * height
    centre_x = float((energy * cols).sum()) / total / energy.shape[1] * width

    return CropBox(
        x=int(np.clip(centre_x - side / 2, 0, width - side)),
        y=int(np.clip(centre_y - side / 2, 0, height - side)),
        size=side,
    )


def pad_color(rgb: np.ndarray) -> tuple[int, int, int]:
    """Colour to fill a crop's overhang with: the median of the image's edge.

    Matching the existing background keeps the frame looking like background
    to `_border_is_inked`, and leaves no hard seam for the threshold to trace.
    Edge replication would be the obvious alternative, but it smears any
    subject that touches the frame into stripes across the padding.
    """
    edge = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
    return tuple(int(channel) for channel in np.median(edge, axis=0))


def apply_box(rgb: np.ndarray, box: CropBox) -> np.ndarray:
    height, width = rgb.shape[:2]
    fitted = box.clamped(width, height)
    if fitted.size <= min(width, height):  # clamped() keeps these inside
        return rgb[
            fitted.y : fitted.y + fitted.size, fitted.x : fitted.x + fitted.size
        ]

    # The box overhangs: paste the image into a padded square.
    canvas = np.empty((fitted.size, fitted.size, rgb.shape[2]), dtype=rgb.dtype)
    canvas[:] = pad_color(rgb)
    src_x, src_y = max(fitted.x, 0), max(fitted.y, 0)
    src_x2 = min(fitted.x + fitted.size, width)
    src_y2 = min(fitted.y + fitted.size, height)
    dst_x, dst_y = src_x - fitted.x, src_y - fitted.y
    canvas[dst_y : dst_y + src_y2 - src_y, dst_x : dst_x + src_x2 - src_x] = rgb[
        src_y:src_y2, src_x:src_x2
    ]
    return canvas


def square_crop(
    rgb: np.ndarray, smart: bool = True, box: CropBox | None = None
) -> np.ndarray:
    """Crop to a square: `box` if given, otherwise the automatic choice."""
    if box is None:
        height, width = rgb.shape[:2]
        if height == width:
            return rgb
        box = auto_box(rgb, smart)
    return apply_box(rgb, box)

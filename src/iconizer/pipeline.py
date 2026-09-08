"""The whole conversion, end to end."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import cv2

from .config import Config
from .crop import square_crop
from .extract import ink_mask
from .trace import path_count, trace


@dataclass(frozen=True)
class Icon:
    svg: str
    paths: int
    source_size: tuple[int, int]

    def write(self, destination: str | Path) -> Path:
        destination = Path(destination)
        destination.write_text(self.svg, encoding="utf-8")
        return destination


def load_image(source: str | Path) -> np.ndarray:
    """Read any image OpenCV understands as RGB, flattening transparency.

    Alpha is composited onto white rather than dropped: a transparent PNG logo
    otherwise thresholds against whatever garbage sits in its hidden colour
    channels.
    """
    source = Path(source)
    image = cv2.imread(str(source), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ValueError(f"could not read an image from {source}")

    if image.ndim == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
    if image.shape[2] == 4:
        bgr = image[:, :, :3].astype(np.float32)
        alpha = (image[:, :, 3:4].astype(np.float32)) / 255.0
        flattened = bgr * alpha + 255.0 * (1.0 - alpha)
        image = flattened.astype(np.uint8)
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def iconize(image: np.ndarray, config: Config | None = None) -> Icon:
    """RGB array in, icon out."""
    config = config or Config()
    height, width = image.shape[:2]
    square = square_crop(image, smart=config.smart_crop)
    mask = ink_mask(square, config)
    svg = trace(mask, config)
    return Icon(svg=svg, paths=path_count(svg), source_size=(width, height))


def iconize_file(source: str | Path, config: Config | None = None) -> Icon:
    return iconize(load_image(source), config)

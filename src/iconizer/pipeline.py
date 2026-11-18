"""The whole conversion, end to end."""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import cv2

from . import winicon
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


def _to_rgb(image: np.ndarray) -> np.ndarray:
    """OpenCV-ordered gray/BGR/BGRA -> RGB, flattening transparency.

    Alpha is composited onto white rather than dropped: a transparent PNG logo
    otherwise thresholds against whatever garbage sits in its hidden colour
    channels.
    """
    if image.ndim == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
    if image.shape[2] == 4:
        bgr = image[:, :, :3].astype(np.float32)
        alpha = (image[:, :, 3:4].astype(np.float32)) / 255.0
        flattened = bgr * alpha + 255.0 * (1.0 - alpha)
        image = flattened.astype(np.uint8)
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def _decode_with_pillow(payload: bytes) -> np.ndarray | None:
    """Formats OpenCV has no codec for, .ico chief among them."""
    from PIL import Image

    try:
        with Image.open(io.BytesIO(payload)) as picture:
            rgba = np.asarray(picture.convert("RGBA"))
    except (OSError, SyntaxError, ValueError):
        return None
    return np.ascontiguousarray(rgba[:, :, [2, 1, 0, 3]])


def decode_image(payload: bytes) -> np.ndarray:
    """Image file contents -> RGB array.

    Accepts anything OpenCV or Pillow can decode, plus Windows executables and
    DLLs, whose main icon is extracted. Raises ValueError otherwise.
    """
    if winicon.is_executable(payload):
        image = winicon.extract_icon(payload)
    else:
        image = cv2.imdecode(np.frombuffer(payload, np.uint8), cv2.IMREAD_UNCHANGED)
        if image is None:
            image = _decode_with_pillow(payload)
        if image is None:
            raise ValueError("that file is not a readable image")
    return _to_rgb(image)


def load_image(source: str | Path) -> np.ndarray:
    """Read an image file (or an executable's icon) as RGB."""
    source = Path(source)
    try:
        with source.open("rb") as handle:
            head = handle.read(2)
        if winicon.is_executable(head):
            # By path, not bytes: pefile memory-maps it rather than reading a
            # multi-hundred-megabyte binary into memory for a 256px icon.
            return _to_rgb(winicon.extract_icon(source))
        return decode_image(source.read_bytes())
    except (OSError, ValueError) as exc:
        raise ValueError(f"could not read an image from {source}: {exc}") from exc


def iconize(image: np.ndarray, config: Config | None = None) -> Icon:
    """RGB array in, icon out."""
    config = config or Config()
    height, width = image.shape[:2]
    square = square_crop(image, smart=config.smart_crop, box=config.crop)
    mask = ink_mask(square, config)
    svg = trace(mask, config)
    return Icon(svg=svg, paths=path_count(svg), source_size=(width, height))


def iconize_file(source: str | Path, config: Config | None = None) -> Icon:
    return iconize(load_image(source), config)

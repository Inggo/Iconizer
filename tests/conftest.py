import numpy as np
import pytest


def _canvas(width: int, height: int, background: int) -> np.ndarray:
    return np.full((height, width, 3), background, dtype=np.uint8)


@pytest.fixture
def dark_on_light() -> np.ndarray:
    """A dark disc, off-centre, on a light background. 900x600."""
    image = _canvas(900, 600, 235)
    rows, cols = np.mgrid[0:600, 0:900]
    disc = (rows - 300) ** 2 + (cols - 640) ** 2 < 180**2
    image[disc] = 30
    return image


@pytest.fixture
def light_on_dark(dark_on_light: np.ndarray) -> np.ndarray:
    """The same scene inverted -- the figure/ground trap."""
    return 255 - dark_on_light


@pytest.fixture
def square_subject() -> np.ndarray:
    image = _canvas(400, 400, 240)
    image[120:280, 120:280] = 20
    return image

import numpy as np
import pytest

from iconizer.config import Config, TRACE_RESOLUTION
from iconizer.crop import square_crop
from iconizer.extract import ink_mask, outline, silhouette


def _ink_fraction(mask: np.ndarray) -> float:
    return float((mask > 0).mean())


def test_silhouette_marks_the_subject_not_the_background(dark_on_light):
    mask = silhouette(square_crop(dark_on_light), Config())
    # The disc is a minority of the frame; if polarity flipped we would see >50%.
    assert 0.05 < _ink_fraction(mask) < 0.5


def test_polarity_survives_inversion(light_on_dark):
    """A light subject on a dark ground must still come out as ink, not a negative."""
    mask = silhouette(square_crop(light_on_dark), Config())
    assert 0.05 < _ink_fraction(mask) < 0.5


def test_invert_override_beats_detection(dark_on_light):
    square = square_crop(dark_on_light)
    automatic = silhouette(square, Config())
    forced = silhouette(square, Config(invert=True))
    assert _ink_fraction(forced) > _ink_fraction(automatic)


def test_outline_is_thinner_than_silhouette(square_subject):
    config = Config()
    filled = silhouette(square_subject, config)
    outlined = outline(square_subject, config)
    assert 0 < _ink_fraction(outlined) < _ink_fraction(filled)


def test_detail_level_controls_resolution(square_subject):
    """Higher detail must retain at least as much structure, and the mask always
    leaves the extractor at tracing resolution."""
    for detail in (1, 3, 5):
        mask = ink_mask(square_subject, Config(detail=detail))
        assert mask.shape == (TRACE_RESOLUTION, TRACE_RESOLUTION)
        assert set(np.unique(mask)) <= {0, 255}


@pytest.mark.parametrize(
    "colour", ["currentColor", "white", "black", "#fff", "#ff0055", "#ff0055aa"]
)
def test_valid_colours_are_accepted(colour):
    assert Config(color=colour).color == colour


@pytest.mark.parametrize(
    "colour",
    [
        '"><script>alert(1)</script>',   # would break out of the fill attribute
        'red" onload="evil()',
        "url(#x)",
        "",
        "#zzz",
    ],
)
def test_dangerous_colours_are_rejected(colour):
    """config.color is interpolated into SVG markup, so validation is the only
    thing standing between a form field and injected attributes."""
    with pytest.raises(ValueError, match="unsupported colour"):
        Config(color=colour)

import numpy as np
import pytest

from iconizer import Config, iconize
from iconizer.config import CropBox
from iconizer.crop import apply_box, auto_box, square_crop


class TestCropBox:
    def test_rejects_non_positive_size(self):
        with pytest.raises(ValueError, match="positive"):
            CropBox(x=0, y=0, size=0)

    def test_clamp_pulls_box_inside_the_image(self):
        assert CropBox(x=900, y=900, size=100).clamped(400, 300) == CropBox(
            x=300, y=200, size=100
        )

    def test_clamp_shrinks_a_box_bigger_than_the_image(self):
        """Size is fitted to the *long* side first; the box then fills the
        width exactly and may only move within its vertical overhang."""
        assert CropBox(x=50, y=50, size=9999).clamped(400, 300) == CropBox(
            x=0, y=0, size=400
        )

    def test_box_may_overhang_the_short_axis(self):
        """A 400px box on a 400x300 image centres it with y=-50."""
        assert CropBox(x=0, y=-50, size=400).clamped(400, 300) == CropBox(
            x=0, y=-50, size=400
        )

    def test_overhang_cannot_uncover_the_image(self):
        """Along the overhanging axis the box must still span the image, so it
        can never slide off into pure padding."""
        assert CropBox(x=0, y=-500, size=400).clamped(400, 300) == CropBox(
            x=0, y=-100, size=400
        )
        assert CropBox(x=0, y=80, size=400).clamped(400, 300) == CropBox(
            x=0, y=0, size=400
        )

    def test_overhang_works_for_portrait_images_too(self):
        assert CropBox(x=-50, y=0, size=400).clamped(300, 400) == CropBox(
            x=-50, y=0, size=400
        )

    def test_a_box_that_fits_still_cannot_overhang(self):
        assert CropBox(x=0, y=-50, size=200).clamped(400, 300) == CropBox(
            x=0, y=0, size=200
        )

    def test_clamp_rejects_negative_offsets(self):
        assert CropBox(x=-40, y=-10, size=100).clamped(400, 300) == CropBox(
            x=0, y=0, size=100
        )


class TestAutoBox:
    def test_uses_the_full_short_side(self, dark_on_light):
        assert auto_box(dark_on_light).size == 600

    def test_centre_mode_is_centred(self, dark_on_light):
        assert auto_box(dark_on_light, smart=False) == CropBox(x=150, y=0, size=600)

    def test_smart_mode_follows_the_subject(self, dark_on_light):
        """The disc sits right of centre, so the smart box should too."""
        assert auto_box(dark_on_light, smart=True).x > auto_box(
            dark_on_light, smart=False
        ).x

    def test_square_image_gives_a_full_box(self, square_subject):
        assert auto_box(square_subject) == CropBox(x=0, y=0, size=400)


class TestApplyBox:
    def test_extracts_the_named_region(self, dark_on_light):
        cropped = apply_box(dark_on_light, CropBox(x=100, y=50, size=200))
        assert cropped.shape == (200, 200, 3)
        assert np.array_equal(cropped, dark_on_light[50:250, 100:300])

    def test_out_of_bounds_box_still_yields_a_square(self, dark_on_light):
        cropped = apply_box(dark_on_light, CropBox(x=880, y=580, size=300))
        assert cropped.shape == (300, 300, 3)

    def test_overhanging_box_pads_with_the_background(self, dark_on_light):
        """The 900x600 fixture centred in a 900px square: 150px of padding
        above and below, in the fixture's background grey, image untouched."""
        cropped = apply_box(dark_on_light, CropBox(x=0, y=-150, size=900))
        assert cropped.shape == (900, 900, 3)
        assert (cropped[:150] == 235).all()
        assert (cropped[750:] == 235).all()
        assert np.array_equal(cropped[150:750], dark_on_light)

    def test_padding_follows_the_background_polarity(self, light_on_dark):
        cropped = apply_box(light_on_dark, CropBox(x=0, y=-150, size=900))
        assert (cropped[:150] == 255 - 235).all()

    def test_overhanging_crop_traces_the_same_subject(self, dark_on_light):
        """Padding must read as background: the centred icon still has the
        disc, and the padded frame does not flip figure/ground."""
        from iconizer.extract import ink_mask

        square = apply_box(dark_on_light, CropBox(x=0, y=-150, size=900))
        ink = (ink_mask(square, Config()) > 0).mean()
        # The disc covers ~13% of the square; a flipped polarity would be ~87%.
        assert 0.05 < ink < 0.25

    def test_explicit_box_overrides_smart_crop(self, dark_on_light):
        box = CropBox(x=0, y=0, size=120)
        assert np.array_equal(
            square_crop(dark_on_light, smart=True, box=box),
            dark_on_light[0:120, 0:120],
        )


def test_config_crop_reaches_the_pipeline(dark_on_light):
    """Cropping empty background and cropping the subject's edge must differ.

    Both regions have to contain *structure* for this to mean anything: a crop
    lying wholly inside the disc is as featureless as one lying wholly outside
    it, and the two produce the same degenerate icon.
    """
    background = iconize(dark_on_light, Config(crop=CropBox(x=0, y=0, size=150)))
    edge = iconize(dark_on_light, Config(crop=CropBox(x=400, y=200, size=200)))
    assert background.svg != edge.svg
    assert edge.paths >= 1

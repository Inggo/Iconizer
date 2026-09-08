import numpy as np

from iconizer.crop import square_crop


def test_crop_is_square_and_full_short_side(dark_on_light):
    cropped = square_crop(dark_on_light)
    assert cropped.shape[0] == cropped.shape[1] == 600


def test_already_square_is_untouched(square_subject):
    assert square_crop(square_subject) is square_subject


def test_smart_crop_follows_the_subject(dark_on_light):
    """The disc sits at x=640; a centre crop (x 150..750) clips it, and a smart
    crop should sit further right than that."""
    smart = square_crop(dark_on_light, smart=True)
    centred = square_crop(dark_on_light, smart=False)

    def ink_centroid(image):
        dark = image.mean(axis=2) < 128
        return dark.sum(), np.argwhere(dark)[:, 1].mean()

    smart_area, smart_x = ink_centroid(smart)
    centred_area, _ = ink_centroid(centred)
    assert smart_area >= centred_area
    # The subject should land nearer the middle of the smart crop.
    assert abs(smart_x - 300) < 150

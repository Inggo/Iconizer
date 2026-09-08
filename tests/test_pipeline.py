import numpy as np
import pytest
from typer.testing import CliRunner

from iconizer import Config, iconize, iconize_file, load_image
from iconizer.cli import app


def test_iconize_end_to_end(dark_on_light):
    icon = iconize(dark_on_light)
    assert icon.paths >= 1
    assert icon.source_size == (900, 600)
    assert icon.svg.startswith("<svg")


def test_detail_budget_bounds_complexity(dark_on_light):
    """The whole point of the detail lever: low detail must not out-complicate
    high detail."""
    simple = iconize(dark_on_light, Config(detail=1))
    detailed = iconize(dark_on_light, Config(detail=5))
    assert simple.paths <= detailed.paths


def test_alpha_is_flattened_onto_white(tmp_path):
    import cv2

    bgra = np.zeros((100, 100, 4), dtype=np.uint8)
    bgra[25:75, 25:75, :3] = 10
    bgra[25:75, 25:75, 3] = 255  # opaque square, fully transparent surround
    source = tmp_path / "logo.png"
    cv2.imwrite(str(source), bgra)

    rgb = load_image(source)
    assert rgb.shape == (100, 100, 3)
    assert rgb[0, 0].tolist() == [255, 255, 255]  # transparent -> white
    assert rgb[50, 50].mean() < 50


def test_unreadable_source_is_reported(tmp_path):
    broken = tmp_path / "not-an-image.png"
    broken.write_text("nope")
    with pytest.raises(ValueError, match="could not read"):
        iconize_file(broken)


def test_cli_writes_svg_next_to_source(tmp_path, dark_on_light):
    import cv2

    source = tmp_path / "photo.png"
    cv2.imwrite(str(source), cv2.cvtColor(dark_on_light, cv2.COLOR_RGB2BGR))

    result = CliRunner().invoke(app, [str(source)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "photo.svg").read_text().startswith("<svg")


def test_cli_rejects_unknown_mode(tmp_path, dark_on_light):
    import cv2

    source = tmp_path / "photo.png"
    cv2.imwrite(str(source), cv2.cvtColor(dark_on_light, cv2.COLOR_RGB2BGR))

    result = CliRunner().invoke(app, [str(source), "--mode", "sketch"])
    assert result.exit_code != 0

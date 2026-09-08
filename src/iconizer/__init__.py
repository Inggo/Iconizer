"""Turn any image into square vector line art usable as an icon."""

from .config import Config
from .pipeline import Icon, iconize, iconize_file, load_image

__all__ = ["Config", "Icon", "iconize", "iconize_file", "load_image"]

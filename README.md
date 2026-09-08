# Iconizer

Turn an image into square vector line art you can use as an icon.

Iconizer crops the image to a square centred on its subject, reduces it to a
binary ink mask at a deliberately low resolution, and traces that into a small
SVG with `currentColor` fills.

## Install

Requires [uv](https://docs.astral.sh/uv/). Everything else is pure wheels, so
the same commands work on Linux, macOS and Windows.

```sh
uv sync
```

## Use

```sh
uv run iconizer photo.jpg                      # -> photo.svg
uv run iconizer photo.jpg -o icon.svg -d 2     # bolder, simpler
uv run iconizer photo.jpg --mode outline       # line art instead of filled
uv run iconizer photo.jpg --center-crop        # don't chase the subject
uv run iconizer photo.jpg --invert             # fix figure/ground by hand
```

`--detail` (1-5) is the main lever: it sets the resolution the icon is extracted
at, which caps how much can survive into the trace. Level 1 gives a bold mark
that still reads at 16px; level 5 keeps noticeably more structure.

### Previews

Rendering an SVG back to PNG needs `libcairo`, a system library, so it is kept
out of the default install:

```sh
uv sync --extra preview
uv run iconizer photo.jpg --preview preview.png
```

## Library

```python
from iconizer import Config, iconize_file

icon = iconize_file("photo.jpg", Config(detail=2, mode="outline"))
icon.write("icon.svg")
print(icon.paths)
```

## Tests

```sh
uv run pytest
```

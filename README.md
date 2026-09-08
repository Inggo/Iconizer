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

`uv sync` creates a project-local virtualenv at `.venv/` and installs everything
into it -- nothing is installed system-wide, and `.venv/` is gitignored. `uv run`
uses that environment automatically, so there is nothing to activate. If you
prefer an activated shell, `source .venv/bin/activate` works too, and then the
`iconizer` and `iconizer-ui` commands are on your PATH directly.

## Graphical use

```sh
uv run iconizer-ui
```

This starts a small local web server and opens your browser. Drop an image in,
adjust the controls, and the icon re-renders live; **Save SVG** downloads it. The **Ink** control switches between auto, black and
white, and the preview backdrop flips to keep white ink visible.

It serves on **127.0.0.1:8791** by default, and if that port is busy it quietly
picks a free one instead -- the URL it actually chose is always printed on
startup. `--port N` pins a specific port (and fails loudly if N is taken);
`--no-open-browser` skips launching the browser.

The UI needs no `libcairo`: browsers render SVG natively, so the preview is the
real vector output rather than a rasterisation of it.

## Command line

```sh
uv run iconizer photo.jpg                      # -> photo.svg
uv run iconizer photo.jpg -o icon.svg -d 2     # bolder, simpler
uv run iconizer photo.jpg --mode outline       # line art instead of filled
uv run iconizer photo.jpg --center-crop        # don't chase the subject
uv run iconizer photo.jpg --color white        # white ink, for dark backgrounds
uv run iconizer photo.jpg --color '#ff0055'    # any hex colour
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

## Icon colour

By default every fill is `currentColor`, so the icon inherits colour from the
CSS around it -- drop it into a dark theme and it turns white on its own. That
is usually what you want for an inline SVG.

It is *not* what you want for a file viewed on its own: with no CSS in scope,
`currentColor` resolves to black. Pass `--color white` (or any hex colour) to
bake the colour into the file instead.

Colours are validated against a small pattern rather than escaped, since they
are interpolated straight into SVG markup.

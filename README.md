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

The crop starts wherever the automatic cropper put it, drawn as a box over the
source. Drag it to move, drag a corner to resize, scroll over it to zoom, and
**Reset** returns to the automatic choice. Everything stays square, so the icon
is never distorted. The box can grow past the short side of a wide or tall
image; **Fit** centres the whole image in one click. The overhang is filled
with the image's background colour, previewed behind the box.

Windows `.exe` and `.dll` files can be dropped in too (see
[Icons from executables](#icons-from-executables)).

Tracing is too slow to run on every pointer event, so the icon re-renders once
you have left the crop box alone for a second. The preview dims while a render
is pending.

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
uv run iconizer photo.jpg --crop 120,40,600    # exact crop: X,Y,SIZE in source pixels
uv run iconizer wide.jpg --crop 0,-150,900     # 900x600 image, whole and centred
uv run iconizer App.exe -o app.svg             # trace an executable's icon
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

## Cropping

By default the crop is the largest square the image allows, positioned over the
busiest region (`--smart-crop`) or the middle (`--center-crop`).

`--crop X,Y,SIZE` overrides that with an exact square in source pixels, which is
what the GUI's draggable box produces. Out-of-range boxes are clamped rather
than rejected: the size is fitted first, then the offsets are pulled in.

SIZE can be as large as the image's *long* side. Along the short axis the box
then overhangs, and the overhang is padded with the median colour of the
image's edge, so it reads as more background. The box must still span the image
along that axis: on a 900x600 image a 900px box takes Y from -300 to 0, with
-150 centring it.

Cropping into a featureless region -- open sky, or the middle of a solid shape
-- gives an empty or solid icon. That is the extractor working correctly on an
image with no structure in it, not a failure.

## Icons from executables

Pass a Windows `.exe` or `.dll` anywhere an image is accepted, in the CLI or
the UI, and its main icon (the one Explorer shows) is extracted at its largest
size and traced. `.ico` files work directly too. This works on every platform,
since reading the resources needs no Windows APIs.

Icons are small: modern apps ship 256px, older ones often stop at 32 or 48px.
The detail levels above that size have nothing extra to find. Executables
without an icon, such as many console tools, are reported as such.

# AGENTS.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```sh
uv sync                          # install (dev group included)
uv sync --extra preview          # adds cairosvg, needed for --preview and preview tests
uv run pytest                    # full suite
uv run pytest tests/test_extract.py::test_polarity_survives_inversion   # single test
uv run iconizer IMAGE -o icon.svg --preview preview.png                 # manual check
uv run iconizer-ui --no-open-browser                                    # web UI (:8791)
```

Dependencies live in the project-local `.venv/` that `uv sync` creates; never
install into system Python.

There is no linter or formatter configured.

## Architecture

`image -> square_crop -> ink_mask -> trace -> normalised SVG`, wired together in
`pipeline.py`. Each stage is a pure function over numpy arrays, so any stage can
be exercised on its own in a test or a REPL.

| Module | Role |
| --- | --- |
| `config.py` | `Config` dataclass and the detail-level table |
| `crop.py` | gradient-energy map, subject-centred square crop |
| `extract.py` | image -> binary ink mask (the hard part) |
| `trace.py` | mask -> SVG via vtracer, then normalisation |
| `pipeline.py` | orchestration, image decoding, the `Icon` result |
| `winicon.py` | main icon out of a Windows `.exe`/`.dll` |
| `cli.py` | Typer wrapper; no logic of its own |
| `server.py` | FastAPI app behind the web UI |
| `web/index.html` | the entire front end: no build step, no CDN |

### Detail level is a resolution budget, not a tracer setting

`Config.detail` (1-5) maps through `DETAIL_RESOLUTIONS` to the resolution the
ink mask is extracted at, and that is the only thing that meaningfully controls
output complexity. A 192px mask physically cannot express more than a few
shapes, whatever the tracer options are. If asked to make output simpler or
busier, change the working resolution rather than reaching for vtracer's
parameters in `_TRACE_OPTIONS`.

### Cropping is a box, chosen automatically or by hand

`crop.auto_box` decides *where* to crop and `apply_box` does it; `square_crop`
just glues them together. They are separate so the UI can seed a draggable box
with the automatic answer and let the user adjust from there, and so
`/api/autocrop` can answer "where would auto put it?" without rendering.

`CropBox.clamped` fits size before offsets, since the offset range depends on
it. Out-of-range boxes are clamped, never rejected, so a stale box from the UI
renders instead of 500ing.

A box may be as large as the image's *long* side, so a landscape image can sit
whole and centred. Per axis: a box that fits stays inside the image; one that
overhangs must still span the image (offset in `[extent - size, 0]`), so it can
never slide off into pure padding. `apply_box` fills the overhang with
`pad_color` — the median of the image's edge — so the padding reads as
background to `_border_is_inked`. Edge replication smears subjects touching the
frame into stripes; don't switch to it. `clampCrop` in the front end mirrors
this rule and must stay in step with it.

A crop over a featureless region produces an empty or solid icon. That is
correct behaviour, and it makes uniform regions useless as test fixtures: assert
against a crop that straddles an edge, or two featureless crops will compare
equal and the test will prove nothing.

### Every source goes through `decode_image`

The CLI (`load_image`) and the upload endpoint share `pipeline.decode_image`:
OpenCV first, Pillow as the fallback (OpenCV has no `.ico` codec), and anything
starting with `MZ` goes to `winicon.extract_icon`. All of them return
OpenCV-ordered arrays so `_to_rgb` flattens alpha onto white in one place.

`extract_icon` rebuilds the first `RT_GROUP_ICON` and its `RT_ICON` images as
an in-memory `.ico` and lets Pillow pick the largest size, which also handles
the legacy DIB encoding. `load_image` hands pefile a *path* so it memory-maps
large binaries; the server reads executables under a larger upload cap
(`_MAX_EXECUTABLE_BYTES`), as Electron apps and games dwarf the image limit.
Tests build a minimal PE in memory (`tests/pe_fixture.py`) rather than
committing a binary.

### Figure/ground is decided separately from thresholding

Otsu finds *where* to split light from dark but not which side is the subject.
`_border_is_inked` settles it by assuming background touches the frame and the
subject does not. Get this wrong and the output is a photographic negative of
the intended icon — the most visible failure mode in the project, and why
`Config.invert` exists as a manual override. Both polarities are covered by
tests (`dark_on_light` / `light_on_dark` fixtures).

### Outlines come from the silhouette, never from an edge detector

`outline()` takes the morphological gradient of the silhouette. Canny and XDoG
were both tried on photographs and are not viable: they find every texture edge
and produce path soup (measured: 1184 paths / 631KB on a textured photo, versus
28 paths / 50KB through the silhouette route). The silhouette boundary is
closed, sparse, and reads as a drawing. Don't reintroduce edge detection here
without re-measuring path counts on a textured photo.

### Blur, then threshold, before tracing

`ink_mask` upsamples the small mask to `TRACE_RESOLUTION`, Gaussian-blurs it,
and only then re-thresholds. The blur converts the upsampling staircase into a
ramp that the threshold cuts a smooth curve through. Removing it produces
visibly pixelated paths and roughly doubles the file size.

### Output is normalised for use as an icon asset

`_normalise` strips vtracer's XML prolog and generator comment so the markup can
be pasted inline into HTML, rewrites every fill to `Config.color`, and sets a
square viewBox.

`Config.color` defaults to `currentColor`, which makes an inline icon inherit
its colour from surrounding CSS; a literal colour is for files viewed standalone,
where `currentColor` resolves to black. Colour is a separate axis from `mode`
(which selects *what is extracted*) — don't add colour variants as new modes.

**`Config.color` is interpolated directly into SVG markup.** It is validated
against `COLOR_PATTERN` in `Config.__post_init__` rather than escaped, so a form
field cannot break out of the `fill="…"` attribute. Any future string that
reaches the markup needs the same treatment; `test_dangerous_colours_are_rejected`
covers it.

Anything that renders a preview must pick a backdrop from the ink colour
(`cli._is_light`, `applyBackdrop` in the front end) — white ink on the default
white backdrop is an invisible icon.

### The web UI is a thin shell over the same pipeline

`server.py` holds no image logic beyond decoding an upload; it validates the
form fields, builds a `Config`, and calls `iconize`. New pipeline options should
surface as a `Config` field, a CLI flag, and a form field -- in that order. The
front end is one dependency-free `index.html`; keep it that way so the UI works
offline and needs no build step.

The default port is 8791, not 8000/8080/3000 -- those collide with whatever else
the developer is running. A busy default silently falls back to an OS-assigned
free port, while an explicit `--port` fails loudly instead; either way the chosen
URL is printed (flushed, since stdout block-buffers when redirected). See
`resolve_port`.

Cropper interaction never renders directly: drags, resizes, wheel zoom and
pointer release all go through `scheduleRender`, which re-renders only after
`CROP_IDLE_MS` of stillness. Release is activity, not a commit — it is usually
followed by another nudge. Discrete commands (Reset, the control buttons) render
immediately; `autoCrop` is async, so anything resetting the box must await it
before rendering or it renders the previous crop.

Two front-end traps, both found only by rendering the page in a real browser:
a large `box-shadow` spread is *not* confined by an ancestor's `overflow: hidden`
and will paint over the rest of the page (the crop dimming is four explicit
panels for that reason), and crop geometry must be drawn from the `<img>`'s own
`load` event rather than a `requestAnimationFrame` guess.

Uploads are cached in memory by opaque id (`_images`, capped at
`_MAX_CACHED_IMAGES`) so moving a slider re-renders from the decoded array
instead of re-uploading. A miss returns 404 and the page asks for the image
again -- that is the expected path after a server restart, not a bug.

## Portability constraint

The project must install from wheels alone on Linux, macOS and Windows, so
`uv sync` never needs a compiler or system libraries. `cairosvg` is the one
exception — it needs system `libcairo` — so it is confined to the `preview`
extra and the dev group, and is imported lazily inside `cli._write_preview`.
Keep it out of `[project.dependencies]`, and prefer dependencies that ship
wheels when adding anything new. `uvicorn` is deliberately installed without its
`[standard]` extra, which would pull compiled speedups (and uvloop, which has no
Windows wheel) that a single-user local tool does not need.

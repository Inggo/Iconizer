# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```sh
uv sync                          # install (dev group included)
uv sync --extra preview          # adds cairosvg, needed for --preview and preview tests
uv run pytest                    # full suite
uv run pytest tests/test_extract.py::test_polarity_survives_inversion   # single test
uv run iconizer IMAGE -o icon.svg --preview preview.png                 # manual check
```

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
| `pipeline.py` | orchestration, image loading, the `Icon` result |
| `cli.py` | Typer wrapper; no logic of its own |

### Detail level is a resolution budget, not a tracer setting

`Config.detail` (1-5) maps through `DETAIL_RESOLUTIONS` to the resolution the
ink mask is extracted at, and that is the only thing that meaningfully controls
output complexity. A 192px mask physically cannot express more than a few
shapes, whatever the tracer options are. If asked to make output simpler or
busier, change the working resolution rather than reaching for vtracer's
parameters in `_TRACE_OPTIONS`.

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
be pasted inline into HTML, rewrites every fill to `currentColor` so the icon
inherits colour from CSS, and sets a square viewBox. `test_output_is_colour_inheriting`
asserts no `#` survives — a hard-coded colour defeats the point.

## Portability constraint

The project must install from wheels alone on Linux, macOS and Windows, so
`uv sync` never needs a compiler or system libraries. `cairosvg` is the one
exception — it needs system `libcairo` — so it is confined to the `preview`
extra and the dev group, and is imported lazily inside `cli._write_preview`.
Keep it out of `[project.dependencies]`, and prefer dependencies that ship
wheels when adding anything new.

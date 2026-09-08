"""Command line entry point."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from .config import Config, DETAIL_RESOLUTIONS
from .pipeline import iconize_file

app = typer.Typer(add_completion=False, help="Turn an image into a vector icon.")


@app.command()
def main(
    source: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="SVG destination (default: <source>.svg)."
    ),
    detail: int = typer.Option(
        3, "--detail", "-d", min=min(DETAIL_RESOLUTIONS), max=max(DETAIL_RESOLUTIONS),
        help="1 = boldest and simplest, 5 = most detail.",
    ),
    mode: str = typer.Option(
        "silhouette", "--mode", "-m", help="silhouette (filled) or outline (line art)."
    ),
    size: int = typer.Option(512, "--size", "-s", help="SVG width/height attribute."),
    color: str = typer.Option(
        "currentColor", "--color", "-c",
        help="Ink colour, e.g. white or '#fff'. Default inherits from CSS.",
    ),
    invert: Optional[bool] = typer.Option(
        None, "--invert/--no-invert",
        help="Override automatic figure/ground detection.",
    ),
    smart_crop: bool = typer.Option(
        True, "--smart-crop/--center-crop", help="Centre the crop on the subject."
    ),
    preview: Optional[Path] = typer.Option(
        None, "--preview", help="Also write a PNG preview (needs the 'preview' extra)."
    ),
) -> None:
    if mode not in ("silhouette", "outline"):
        raise typer.BadParameter("mode must be 'silhouette' or 'outline'")

    try:
        config = Config(
            detail=detail, mode=mode, size=size, color=color,
            invert=invert, smart_crop=smart_crop,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    icon = iconize_file(source, config)
    destination = icon.write(output or source.with_suffix(".svg"))

    typer.echo(f"{destination}  ({icon.paths} paths, {len(icon.svg) // 1024} KB)")

    if preview is not None:
        _write_preview(destination, preview, size, config.color)
        typer.echo(f"{preview}  (preview)")


def _is_light(color: str) -> bool:
    """Rough luminance test, used only to pick a readable preview backdrop."""
    if color.lower() in ("white", "ivory", "snow", "azure"):
        return True
    if color.startswith("#"):
        hexpart = color[1:]
        if len(hexpart) in (3, 4):
            hexpart = "".join(c * 2 for c in hexpart[:3])
        if len(hexpart) >= 6:
            r, g, b = (int(hexpart[i : i + 2], 16) for i in (0, 2, 4))
            return (0.299 * r + 0.587 * g + 0.114 * b) > 160
    return False


def _write_preview(svg: Path, destination: Path, size: int, color: str) -> None:
    try:
        import cairosvg
    except ImportError as exc:  # pragma: no cover - depends on system libcairo
        raise typer.BadParameter(
            "--preview needs cairosvg: uv sync --extra preview"
        ) from exc

    # A white icon on a white backdrop is an empty PNG; flip the backdrop to
    # match the ink so the preview always shows something.
    cairosvg.svg2png(
        url=str(svg),
        write_to=str(destination),
        output_width=size,
        background_color="#1c1f25" if _is_light(color) else "white",
    )

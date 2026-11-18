"""Local web UI: a browser is the most portable SVG viewer available."""

from __future__ import annotations

import secrets
import socket
import threading
import webbrowser
from collections import OrderedDict
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

from . import winicon
from .config import COLOR_PATTERN, Config, CropBox, DETAIL_RESOLUTIONS
from .crop import auto_box, pad_color
from .pipeline import decode_image, iconize

_WEB_ROOT = Path(__file__).parent / "web"

# Deliberately not 8000/8080/3000/5000/8888: those collide with whatever else
# the developer has running. Overridable with --port, and auto-avoided below.
DEFAULT_PORT = 8791

# Uploads live in memory, keyed by an opaque id, so dragging a slider re-renders
# from the decoded array instead of re-uploading the file. Bounded because this
# is a desktop-scale tool, not a service.
_MAX_CACHED_IMAGES = 8
_MAX_UPLOAD_BYTES = 32 * 1024 * 1024
_MAX_EXECUTABLE_BYTES = 512 * 1024 * 1024

_images: "OrderedDict[str, np.ndarray]" = OrderedDict()
_lock = threading.Lock()

app = FastAPI(title="Iconizer")


def _remember(image: np.ndarray) -> str:
    token = secrets.token_urlsafe(12)
    with _lock:
        _images[token] = image
        while len(_images) > _MAX_CACHED_IMAGES:
            _images.popitem(last=False)
    return token


def _recall(token: str) -> np.ndarray:
    with _lock:
        image = _images.get(token)
    if image is None:
        # Evicted from the cache, or the server restarted under an open tab.
        raise HTTPException(status_code=404, detail="image expired; re-upload it")
    return image


@app.get("/")
def index() -> FileResponse:
    return FileResponse(_WEB_ROOT / "index.html")


async def _read_capped(file: UploadFile) -> bytes:
    """Read an upload, stopping as soon as it is over its size limit.

    The limit is chosen from the first bytes: an executable is mostly code the
    icon extractor never touches, and Electron apps or games routinely exceed
    the image limit by a wide margin.
    """
    chunks: list[bytes] = []
    total, limit = 0, _MAX_UPLOAD_BYTES
    while chunk := await file.read(1024 * 1024):
        if not chunks and winicon.is_executable(chunk):
            limit = _MAX_EXECUTABLE_BYTES
        chunks.append(chunk)
        total += len(chunk)
        if total > limit:
            raise HTTPException(
                status_code=413, detail=f"file is too large ({limit // 2**20} MB limit)"
            )
    return b"".join(chunks)


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)) -> dict:
    payload = await _read_capped(file)
    if not payload:
        raise HTTPException(status_code=400, detail="empty upload")

    try:
        rgb = decode_image(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    height, width = rgb.shape[:2]
    suggested = auto_box(rgb)
    red, green, blue = pad_color(rgb)
    return {
        "id": _remember(rgb),
        "width": width,
        "height": height,
        "name": file.filename or "image",
        # Browsers cannot display an .exe (or, reliably, an .ico); the page
        # shows /api/source instead, which is also exactly what gets traced.
        "extracted": winicon.is_executable(payload),
        # Seeds the draggable crop handle with the automatic answer.
        "crop": {"x": suggested.x, "y": suggested.y, "size": suggested.size},
        # What an overhanging crop is filled with, so the page can show it.
        "pad": f"#{red:02x}{green:02x}{blue:02x}",
    }


@app.get("/api/source/{token}")
def source(token: str) -> Response:
    """The decoded image as PNG, for sources a browser cannot display itself."""
    ok, encoded = cv2.imencode(".png", cv2.cvtColor(_recall(token), cv2.COLOR_RGB2BGR))
    if not ok:  # pragma: no cover - PNG encoding of a uint8 array does not fail
        raise HTTPException(status_code=500, detail="could not encode preview")
    return Response(content=encoded.tobytes(), media_type="image/png")


@app.post("/api/autocrop")
def autocrop(id: str = Form(...), smart_crop: bool = Form(True)) -> dict:
    """Where the automatic crop lands, without rendering anything.

    Lets the UI reset a hand-dragged box, or follow the Subject/Centre toggle,
    using exactly the same code the pipeline would.
    """
    box = auto_box(_recall(id), smart=smart_crop)
    return {"x": box.x, "y": box.y, "size": box.size}


@app.post("/api/render")
def render(
    id: str = Form(...),
    detail: int = Form(3),
    mode: str = Form("silhouette"),
    size: int = Form(512),
    color: str = Form("currentColor"),
    invert: str = Form("auto"),
    smart_crop: bool = Form(True),
    # Floats, not ints: crop geometry comes from pointer drags and wheel zoom,
    # so fractional pixels are normal input. Rejecting them with a 422 pushes a
    # rounding rule onto every client instead of settling it in one place.
    crop_x: Optional[float] = Form(None),
    crop_y: Optional[float] = Form(None),
    crop_size: Optional[float] = Form(None),
) -> Response:
    if detail not in DETAIL_RESOLUTIONS:
        raise HTTPException(status_code=400, detail="detail must be 1-5")
    if mode not in ("silhouette", "outline"):
        raise HTTPException(status_code=400, detail="unknown mode")
    if invert not in ("auto", "on", "off"):
        raise HTTPException(status_code=400, detail="unknown invert setting")
    if not COLOR_PATTERN.match(color):
        raise HTTPException(status_code=400, detail="unsupported colour")

    box: Optional[CropBox] = None
    if crop_size is not None:
        # Not `size`: that is the SVG's output dimension, and shadowing it here
        # makes any crop silently resize the icon.
        side = round(crop_size)
        if side <= 0:
            raise HTTPException(status_code=400, detail="crop size must be positive")
        # Offsets default to 0 so a size-only request still means something.
        box = CropBox(x=round(crop_x or 0), y=round(crop_y or 0), size=side)

    config = Config(
        detail=detail,
        mode=mode,
        size=size,
        color=color,
        crop=box,
        invert={"auto": None, "on": True, "off": False}[invert],
        smart_crop=smart_crop,
    )
    icon = iconize(_recall(id), config)
    return Response(
        content=icon.svg,
        media_type="image/svg+xml",
        headers={"X-Icon-Paths": str(icon.paths)},
    )


def _is_free(host: str, port: int) -> bool:
    with socket.socket() as probe:
        try:
            probe.bind((host, port))
            return True
        except OSError:
            return False


def resolve_port(host: str, port: Optional[int]) -> int:
    """Pick the port to serve on.

    An explicit --port is honoured or fails loudly; the default quietly steps
    aside to a free port, so a busy 8791 never blocks a one-off launch.
    """
    if port is not None:
        if _is_free(host, port):
            return port
        raise SystemExit(f"port {port} is already in use")

    if _is_free(host, DEFAULT_PORT):
        return DEFAULT_PORT
    with socket.socket() as probe:
        probe.bind((host, 0))  # 0 = let the OS hand out a free one
        return int(probe.getsockname()[1])


def run(
    host: str = "127.0.0.1",
    port: Optional[int] = None,
    open_browser: bool = True,
) -> None:
    import uvicorn

    # Small race: the port can be taken between probing and uvicorn binding.
    # Acceptable for a single-user local tool.
    chosen = resolve_port(host, port)
    url = f"http://{host}:{chosen}"
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    # flush: stdout is block-buffered when redirected, and with a fallback port
    # the URL is the one thing the user cannot guess.
    print(f"Iconizer UI -> {url}  (ctrl-c to stop)", flush=True)
    uvicorn.run(app, host=host, port=chosen, log_level="warning")


def main() -> None:
    """Console-script entry point; exposes --host/--port/--no-open-browser."""
    import typer

    typer.run(run)

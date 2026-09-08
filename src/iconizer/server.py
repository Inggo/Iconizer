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

from .config import COLOR_PATTERN, Config, DETAIL_RESOLUTIONS
from .pipeline import iconize

_WEB_ROOT = Path(__file__).parent / "web"

# Deliberately not 8000/8080/3000/5000/8888: those collide with whatever else
# the developer has running. Overridable with --port, and auto-avoided below.
DEFAULT_PORT = 8791

# Uploads live in memory, keyed by an opaque id, so dragging a slider re-renders
# from the decoded array instead of re-uploading the file. Bounded because this
# is a desktop-scale tool, not a service.
_MAX_CACHED_IMAGES = 8
_MAX_UPLOAD_BYTES = 32 * 1024 * 1024

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


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)) -> dict:
    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="empty upload")
    if len(payload) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="image is too large (32 MB limit)")

    decoded = cv2.imdecode(np.frombuffer(payload, np.uint8), cv2.IMREAD_UNCHANGED)
    if decoded is None:
        raise HTTPException(status_code=400, detail="that file is not a readable image")

    if decoded.ndim == 2:
        rgb = cv2.cvtColor(decoded, cv2.COLOR_GRAY2RGB)
    elif decoded.shape[2] == 4:
        # Same alpha-onto-white flattening as pipeline.load_image.
        bgr = decoded[:, :, :3].astype(np.float32)
        alpha = decoded[:, :, 3:4].astype(np.float32) / 255.0
        rgb = cv2.cvtColor(
            (bgr * alpha + 255.0 * (1.0 - alpha)).astype(np.uint8), cv2.COLOR_BGR2RGB
        )
    else:
        rgb = cv2.cvtColor(decoded, cv2.COLOR_BGR2RGB)

    height, width = rgb.shape[:2]
    return {
        "id": _remember(rgb),
        "width": width,
        "height": height,
        "name": file.filename or "image",
    }


@app.post("/api/render")
def render(
    id: str = Form(...),
    detail: int = Form(3),
    mode: str = Form("silhouette"),
    size: int = Form(512),
    color: str = Form("currentColor"),
    invert: str = Form("auto"),
    smart_crop: bool = Form(True),
) -> Response:
    if detail not in DETAIL_RESOLUTIONS:
        raise HTTPException(status_code=400, detail="detail must be 1-5")
    if mode not in ("silhouette", "outline"):
        raise HTTPException(status_code=400, detail="unknown mode")
    if invert not in ("auto", "on", "off"):
        raise HTTPException(status_code=400, detail="unknown invert setting")
    if not COLOR_PATTERN.match(color):
        raise HTTPException(status_code=400, detail="unsupported colour")

    config = Config(
        detail=detail,
        mode=mode,
        size=size,
        color=color,
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

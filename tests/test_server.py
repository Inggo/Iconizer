import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from iconizer.server import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def png_bytes(dark_on_light):
    ok, buf = cv2.imencode(".png", cv2.cvtColor(dark_on_light, cv2.COLOR_RGB2BGR))
    assert ok
    return bytes(buf)


def _upload(client, payload, name="photo.png"):
    return client.post("/api/upload", files={"file": (name, payload, "image/png")})


def test_index_serves_the_page(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "<title>Iconizer</title>" in response.text


def test_upload_then_render(client, png_bytes):
    upload = _upload(client, png_bytes)
    assert upload.status_code == 200
    body = upload.json()
    assert (body["width"], body["height"]) == (900, 600)

    render = client.post("/api/render", data={"id": body["id"], "detail": 3})
    assert render.status_code == 200
    assert render.headers["content-type"].startswith("image/svg+xml")
    assert render.text.startswith("<svg")
    assert int(render.headers["X-Icon-Paths"]) >= 1


def test_render_honours_settings(client, png_bytes):
    token = _upload(client, png_bytes).json()["id"]

    simple = client.post("/api/render", data={"id": token, "detail": 1})
    detailed = client.post("/api/render", data={"id": token, "detail": 5})
    assert int(simple.headers["X-Icon-Paths"]) <= int(detailed.headers["X-Icon-Paths"])

    sized = client.post("/api/render", data={"id": token, "size": 64})
    assert 'width="64" height="64"' in sized.text


def test_unknown_image_id_is_404(client):
    assert client.post("/api/render", data={"id": "nope"}).status_code == 404


def test_rejects_non_image_upload(client):
    response = _upload(client, b"this is not an image", name="notes.txt")
    assert response.status_code == 400


def test_rejects_empty_upload(client):
    assert _upload(client, b"").status_code == 400


def test_render_validates_enums(client, png_bytes):
    token = _upload(client, png_bytes).json()["id"]
    assert client.post("/api/render", data={"id": token, "mode": "sketch"}).status_code == 400
    assert client.post("/api/render", data={"id": token, "detail": 9}).status_code == 400
    assert client.post("/api/render", data={"id": token, "invert": "maybe"}).status_code == 400


def test_cache_evicts_oldest(client, png_bytes):
    from iconizer.server import _MAX_CACHED_IMAGES

    tokens = [_upload(client, png_bytes).json()["id"] for _ in range(_MAX_CACHED_IMAGES + 2)]
    assert client.post("/api/render", data={"id": tokens[0]}).status_code == 404
    assert client.post("/api/render", data={"id": tokens[-1]}).status_code == 200


class TestPortSelection:
    """The default port must never be the reason a launch fails."""

    def test_default_is_used_when_free(self):
        from iconizer.server import DEFAULT_PORT, resolve_port

        assert resolve_port("127.0.0.1", None) in (DEFAULT_PORT, *range(1024, 65536))

    def test_explicit_busy_port_fails_loudly(self):
        import socket

        from iconizer.server import resolve_port

        with socket.socket() as taken:
            taken.bind(("127.0.0.1", 0))
            taken.listen()
            busy = taken.getsockname()[1]
            with pytest.raises(SystemExit, match="already in use"):
                resolve_port("127.0.0.1", busy)

    def test_explicit_free_port_is_honoured(self):
        import socket

        from iconizer.server import resolve_port

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            free = probe.getsockname()[1]
        assert resolve_port("127.0.0.1", free) == free

    def test_busy_default_falls_back_to_a_free_port(self, monkeypatch):
        import socket

        import iconizer.server as server

        with socket.socket() as taken:
            taken.bind(("127.0.0.1", 0))
            taken.listen()
            monkeypatch.setattr(server, "DEFAULT_PORT", taken.getsockname()[1])

            chosen = server.resolve_port("127.0.0.1", None)
            assert chosen != server.DEFAULT_PORT
            assert server._is_free("127.0.0.1", chosen)


def test_render_accepts_a_colour(client, png_bytes):
    token = _upload(client, png_bytes).json()["id"]
    response = client.post("/api/render", data={"id": token, "color": "white"})
    assert response.status_code == 200
    assert 'fill="white"' in response.text
    assert "currentColor" not in response.text


def test_render_rejects_injected_colour(client, png_bytes):
    token = _upload(client, png_bytes).json()["id"]
    response = client.post(
        "/api/render", data={"id": token, "color": '"><script>alert(1)</script>'}
    )
    assert response.status_code == 400

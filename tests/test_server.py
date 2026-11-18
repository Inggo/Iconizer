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


def test_upload_suggests_a_crop(client, png_bytes):
    body = _upload(client, png_bytes).json()
    assert body["crop"]["size"] == 600  # the short side of the 900x600 fixture
    assert 0 <= body["crop"]["x"] <= 300


def test_autocrop_follows_the_smart_flag(client, png_bytes):
    token = _upload(client, png_bytes).json()["id"]

    smart = client.post("/api/autocrop", data={"id": token, "smart_crop": True}).json()
    centred = client.post("/api/autocrop", data={"id": token, "smart_crop": False}).json()
    assert centred == {"x": 150, "y": 0, "size": 600}
    assert smart["x"] > centred["x"]


def test_autocrop_unknown_id_is_404(client):
    assert client.post("/api/autocrop", data={"id": "nope"}).status_code == 404


def test_render_honours_an_explicit_crop(client, png_bytes):
    token = _upload(client, png_bytes).json()["id"]
    base = {"id": token, "detail": 3}

    background = client.post(
        "/api/render", data={**base, "crop_x": 0, "crop_y": 0, "crop_size": 150}
    )
    edge = client.post(
        "/api/render", data={**base, "crop_x": 400, "crop_y": 200, "crop_size": 200}
    )
    assert background.status_code == edge.status_code == 200
    assert background.text != edge.text


def test_render_rejects_a_zero_crop(client, png_bytes):
    token = _upload(client, png_bytes).json()["id"]
    response = client.post("/api/render", data={"id": token, "crop_size": 0})
    assert response.status_code == 400


def test_out_of_bounds_crop_is_clamped_not_rejected(client, png_bytes):
    """The UI can only ever send a box it drew, but a stale one after a new
    upload should still render rather than 500."""
    token = _upload(client, png_bytes).json()["id"]
    response = client.post(
        "/api/render",
        data={"id": token, "crop_x": 5000, "crop_y": 5000, "crop_size": 4000},
    )
    assert response.status_code == 200


def test_render_accepts_fractional_crop(client, png_bytes):
    """Pointer drags and wheel zoom produce fractional pixels; the API rounds
    them rather than answering 422."""
    token = _upload(client, png_bytes).json()["id"]
    response = client.post(
        "/api/render",
        data={"id": token, "crop_x": 400.4, "crop_y": 200.6, "crop_size": 199.5},
    )
    assert response.status_code == 200


def test_fractional_crop_rounding_to_zero_is_rejected(client, png_bytes):
    token = _upload(client, png_bytes).json()["id"]
    response = client.post("/api/render", data={"id": token, "crop_size": 0.4})
    assert response.status_code == 400


def test_crop_does_not_change_the_output_size(client, png_bytes):
    """`size` is the SVG's dimension and `crop_size` is a region of the source;
    they are unrelated, and a crop must not resize the icon."""
    token = _upload(client, png_bytes).json()["id"]
    response = client.post(
        "/api/render",
        data={"id": token, "size": 64, "crop_x": 400, "crop_y": 200, "crop_size": 200},
    )
    assert response.status_code == 200
    assert 'width="64" height="64"' in response.text


def test_upload_accepts_an_executable(client):
    from pe_fixture import build_pe, disc_icon, icon_resources

    exe = build_pe(icon_resources([(disc_icon(64), "png")]))
    upload = client.post(
        "/api/upload", files={"file": ("App.exe", exe, "application/x-msdownload")}
    )
    assert upload.status_code == 200, upload.text
    body = upload.json()
    assert (body["width"], body["height"], body["extracted"]) == (64, 64, True)

    preview = client.get(f"/api/source/{body['id']}")
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/png"
    decoded = cv2.imdecode(np.frombuffer(preview.content, np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape == (64, 64, 3)

    render = client.post("/api/render", data={"id": body["id"]})
    assert render.status_code == 200
    assert int(render.headers["X-Icon-Paths"]) >= 1


def test_executable_without_an_icon_explains_itself(client):
    from pe_fixture import build_pe

    response = client.post("/api/upload", files={"file": ("tool.exe", build_pe(None))})
    assert response.status_code == 400
    assert "no icon" in response.json()["detail"]


def test_images_keep_the_smaller_upload_limit(client, monkeypatch):
    import iconizer.server as server
    from pe_fixture import build_pe, disc_icon, icon_resources

    monkeypatch.setattr(server, "_MAX_UPLOAD_BYTES", 1024)
    big_png = cv2.imencode(".png", np.random.default_rng(0).integers(
        0, 255, (200, 200, 3), dtype=np.uint8))[1].tobytes()
    assert _upload(client, big_png).status_code == 413

    exe = build_pe(icon_resources([(disc_icon(64), "png")]))
    assert len(exe) > 1024
    assert client.post("/api/upload", files={"file": ("App.exe", exe)}).status_code == 200


def test_source_preview_unknown_id_is_404(client):
    assert client.get("/api/source/nope").status_code == 404


def test_upload_reports_the_padding_colour(client, png_bytes):
    assert _upload(client, png_bytes).json()["pad"] == "#ebebeb"  # the fixture's 235 grey


def test_render_accepts_an_overhanging_crop(client, png_bytes):
    token = _upload(client, png_bytes).json()["id"]
    response = client.post(
        "/api/render", data={"id": token, "crop_x": 0, "crop_y": -150, "crop_size": 900}
    )
    assert response.status_code == 200
    assert int(response.headers["X-Icon-Paths"]) >= 1

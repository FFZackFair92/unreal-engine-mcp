"""Server HTTP che emula Poly Haven, ambientCG e kenney.nl per i test di download."""

from __future__ import annotations

import hashlib
import io
import json
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


def _sample_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("PavingStones036_2K_Color.jpg", b"fake-color-map")
        archive.writestr("PavingStones036_2K_Normal.jpg", b"fake-normal-map")
    return buffer.getvalue()


TEXTURE_BYTES = b"fake-texture-bytes"
BIN_BYTES = b"fake-bin-bytes"
KENNEY_ZIP = None  # inizializzato sotto


def _kenney_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Models/GLB/character.glb", b"fake-glb")
        archive.writestr("License.txt", b"CC0")
    return buffer.getvalue()


class FakeWebServer:
    """Espone gli endpoint usati da assets.py, con contenuti minimi ma realistici."""

    def __init__(self):
        self.hits: list[str] = []
        server_self = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, body: bytes, content_type="application/json", status=200):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                server_self.hits.append(self.path)
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                base = "http://127.0.0.1:%d" % server_self.port

                # ---- Poly Haven
                if parsed.path == "/ph/assets":
                    self._send(json.dumps({
                        "brick_wall_02": {"name": "Brick Wall 02", "type": 1,
                                          "categories": ["brick"], "tags": ["wall", "red"]},
                        "wooden_table": {"name": "Wooden Table", "type": 2,
                                         "categories": ["furniture"], "tags": ["wood"]},
                    }).encode())
                    return
                if parsed.path.startswith("/ph/files/"):
                    asset_id = parsed.path.rsplit("/", 1)[-1]
                    if asset_id != "brick_wall_02":
                        self._send(json.dumps({}).encode())
                        return
                    md5 = hashlib.md5(TEXTURE_BYTES).hexdigest()  # noqa: S324
                    self._send(json.dumps({
                        "Diffuse": {
                            "2k": {"jpg": {"url": base + "/files/diff_2k.jpg", "md5": md5, "size": len(TEXTURE_BYTES)}},
                            "4k": {"jpg": {"url": base + "/files/diff_4k.jpg", "md5": md5, "size": len(TEXTURE_BYTES)}},
                        },
                        "gltf": {
                            "2k": {"gltf": {
                                "url": base + "/files/model_2k.gltf",
                                "md5": md5,
                                "include": {
                                    "textures/diff_2k.jpg": {"url": base + "/files/diff_2k.jpg", "md5": md5},
                                    "model.bin": {"url": base + "/files/model.bin",
                                                  "md5": hashlib.md5(BIN_BYTES).hexdigest()},  # noqa: S324
                                },
                            }}
                        },
                    }).encode())
                    return

                # ---- ambientCG
                if parsed.path == "/acg/full_json":
                    self._send(json.dumps({"foundAssets": [
                        {"assetId": "PavingStones036", "dataType": "Material",
                         "displayName": "Paving Stones 036", "displayCategory": "Ground"},
                        {"assetId": "Concrete034", "dataType": "Material",
                         "displayName": "Concrete 034", "displayCategory": "Concrete"},
                    ][: int(query.get("limit", ["20"])[0])]}).encode())
                    return
                if parsed.path == "/acg/get":
                    self._send(_sample_zip(), "application/zip")
                    return

                # ---- Kenney
                if parsed.path.startswith("/kenney/"):
                    slug = parsed.path.rsplit("/", 1)[-1]
                    if slug != "mini-characters-1":
                        self._send(b"not found", "text/html", 404)
                        return
                    html = (
                        '<html><body><a href="/files/%s.zip">Download</a></body></html>' % slug
                    )
                    self._send(html.encode(), "text/html")
                    return

                # ---- file statici
                if parsed.path.endswith(".zip"):
                    self._send(_kenney_zip(), "application/zip")
                    return
                if parsed.path == "/files/model.bin":
                    self._send(BIN_BYTES, "application/octet-stream")
                    return
                if parsed.path.startswith("/files/"):
                    self._send(TEXTURE_BYTES, "application/octet-stream")
                    return

                self._send(b'{"error":"not found"}', status=404)

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._httpd.server_address[1]
        self.base = "http://127.0.0.1:%d" % self.port
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)

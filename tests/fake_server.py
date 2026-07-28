"""Server HTTP che emula la Remote Control API di Unreal.

Esegue davvero il codice Python ricevuto, con il modulo `unreal` finto: così i
test verificano l'intera catena tool -> snippet -> harness -> risultato JSON.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fake_unreal import build_fake_unreal

PYTHON_LIBRARY_PATH = "/Script/PythonScriptPlugin.Default__PythonScriptLibrary"


class FakeUnrealServer:
    def __init__(self, tmp_path):
        self.unreal = build_fake_unreal(tmp_path)
        self.calls: list[dict] = []
        sys.modules["unreal"] = self.unreal

        server_self = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silenzia il log su stderr
                pass

            def _send(self, payload, status=200):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/remote/info":
                    self._send({"HttpServerPort": 30010, "Name": "FakeUnreal"})
                else:
                    self._send({"error": "not found"}, 404)

            def do_PUT(self):
                length = int(self.headers.get("Content-Length", 0))
                request = json.loads(self.rfile.read(length) or b"{}")
                server_self.calls.append(request)

                if self.path != "/remote/object/call":
                    self._send({"error": "not found"}, 404)
                    return
                if request.get("objectPath") != PYTHON_LIBRARY_PATH:
                    self._send({"error": "objectPath inatteso"}, 400)
                    return

                code = request.get("parameters", {}).get("pythonCommand", "")
                buffer = io.StringIO()
                try:
                    with contextlib.redirect_stdout(buffer):
                        exec(compile(code, "<unreal-mcp>", "exec"), {"__name__": "__mcp__"})
                    ok = True
                except Exception:  # noqa: BLE001
                    buffer.write(traceback.format_exc())
                    ok = False

                self._send(
                    {
                        "ReturnValue": ok,
                        "CommandResult": "",
                        "LogOutput": [{"Type": "Info", "Output": buffer.getvalue()}],
                    }
                )

            do_POST = do_PUT

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def state(self) -> dict:
        return self.unreal._state

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)

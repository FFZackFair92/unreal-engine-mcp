"""Editor finto che parla il protocollo di Python remote execution.

Come `fake_server.py` per l'HTTP, ma sui socket veri: risponde al ping in
multicast, si ricollega in TCP quando riceve `open_connection` ed esegue
davvero il codice ricevuto con il modulo `unreal` finto. Serve a testare il
trasporto per intero — framing incluso — senza avere Unreal.
"""

from __future__ import annotations

import contextlib
import io
import json
import socket
import struct
import threading
import traceback
import uuid

from unreal_mcp.pyremote import (
    PROTOCOL_MAGIC,
    PROTOCOL_VERSION,
    TYPE_CLOSE_CONNECTION,
    TYPE_COMMAND,
    TYPE_COMMAND_RESULT,
    TYPE_OPEN_CONNECTION,
    TYPE_PING,
    TYPE_PONG,
)


class FakePyRemoteNode:
    """Un editor finto in ascolto sul gruppo multicast indicato."""

    def __init__(
        self,
        group: str = "239.0.0.1",
        port: int = 6766,
        project_name: str = "MyGame",
        engine_version: str = "5.8.0-fake",
        esegui: bool = True,
    ) -> None:
        self.node_id = str(uuid.uuid4())
        self.group = group
        self.port = port
        self.project_name = project_name
        self.engine_version = engine_version
        #: Con esegui=False il nodo risponde alla scoperta ma non si collega
        #: mai al canale comandi: è il caso "firewall".
        self.esegui = esegui
        self.comandi: list[str] = []
        self._stop = threading.Event()

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("", port))
        self._sock.setsockopt(
            socket.IPPROTO_IP,
            socket.IP_ADD_MEMBERSHIP,
            struct.pack("4s4s", socket.inet_aton(group), socket.inet_aton("0.0.0.0")),  # noqa: S104
        )
        self._sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 0)
        self._sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        self._sock.settimeout(0.25)

        self._thread = threading.Thread(target=self._ciclo, daemon=True)
        self._thread.start()

    # ------------------------------------------------------------- protocollo

    def _invia(self, msg_type, dest=None, data=None):
        messaggio = {
            "version": PROTOCOL_VERSION,
            "magic": PROTOCOL_MAGIC,
            "source": self.node_id,
            "type": msg_type,
        }
        if dest is not None:
            messaggio["dest"] = dest
        if data is not None:
            messaggio["data"] = data
        self._sock.sendto(json.dumps(messaggio).encode("utf-8"), (self.group, self.port))

    def _ciclo(self):
        while not self._stop.is_set():
            try:
                payload, _ = self._sock.recvfrom(65536)
            except (TimeoutError, OSError):
                continue
            try:
                messaggio = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(messaggio, dict) or messaggio.get("magic") != PROTOCOL_MAGIC:
                continue
            if messaggio.get("source") == self.node_id:
                continue

            tipo = messaggio.get("type")
            if tipo == TYPE_PING:
                self._invia(
                    TYPE_PONG,
                    data={
                        "user": "tester",
                        "machine": "finta",
                        "engine_version": self.engine_version,
                        "engine_root": "/finto/UE_5.8",
                        "project_root": "/finto/MyGame",
                        "project_name": self.project_name,
                    },
                )
            elif tipo == TYPE_OPEN_CONNECTION and self.esegui:
                dati = messaggio.get("data") or {}
                threading.Thread(
                    target=self._servi,
                    args=(dati.get("command_ip"), int(dati.get("command_port", 0))),
                    daemon=True,
                ).start()

    def _servi(self, host, porta):
        """Si collega al canale comandi ed esegue quello che arriva."""
        try:
            canale = socket.create_connection((host, porta), timeout=5)
        except OSError:
            return
        buffer = b""
        try:
            while not self._stop.is_set():
                canale.settimeout(0.5)
                try:
                    chunk = canale.recv(65536)
                except (TimeoutError, OSError):
                    continue
                if not chunk:
                    return
                buffer += chunk
                try:
                    messaggio = json.loads(buffer.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                buffer = b""
                tipo = messaggio.get("type")
                if tipo == TYPE_CLOSE_CONNECTION:
                    return
                if tipo != TYPE_COMMAND:
                    continue
                codice = (messaggio.get("data") or {}).get("command", "")
                self.comandi.append(codice)
                canale.sendall(self._esegui(codice, messaggio.get("source")))
        finally:
            with contextlib.suppress(OSError):
                canale.close()

    def _esegui(self, codice: str, dest: str | None) -> bytes:
        buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(buffer):
                exec(compile(codice, "<unreal-mcp>", "exec"), {"__name__": "__mcp__"})
            ok = True
        except Exception:  # noqa: BLE001
            buffer.write(traceback.format_exc())
            ok = False
        return json.dumps(
            {
                "version": PROTOCOL_VERSION,
                "magic": PROTOCOL_MAGIC,
                "source": self.node_id,
                "dest": dest,
                "type": TYPE_COMMAND_RESULT,
                "data": {
                    "success": ok,
                    "command": codice,
                    "result": "",
                    # L'editor spezza l'output per riga: riprodurlo qui è ciò
                    # che rende il test capace di cogliere errori di framing.
                    "output": [
                        {"type": "Info", "output": riga + "\n"}
                        for riga in buffer.getvalue().split("\n")
                        if riga
                    ],
                },
            }
        ).encode("utf-8")

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3)
        with contextlib.suppress(OSError):
            self._sock.close()

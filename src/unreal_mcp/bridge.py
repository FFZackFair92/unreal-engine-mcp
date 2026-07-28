"""Client HTTP verso la Remote Control API di Unreal Engine.

Protocollo
----------
Unreal espone (con il plugin "Remote Control API" attivo) un web server locale.
Chiamiamo::

    PUT http://127.0.0.1:30010/remote/object/call
    {
      "objectPath": "/Script/PythonScriptPlugin.Default__PythonScriptLibrary",
      "functionName": "ExecutePythonCommandEx",
      "parameters": {
          "pythonCommand": "<codice>",
          "executionMode": "ExecuteFile",
          "fileExecutionScope": "Private"
      }
    }

La risposta contiene ``LogOutput`` (lista di righe stampate). Il codice inviato
viene incapsulato in un harness che stampa il risultato come JSON fra due
sentinelle, così da avere un valore di ritorno strutturato e non solo testo.
"""

from __future__ import annotations

import json
import os
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

SENTINEL_START = "<<<MCP_JSON_BEGIN>>>"
SENTINEL_END = "<<<MCP_JSON_END>>>"

PYTHON_LIBRARY_PATH = "/Script/PythonScriptPlugin.Default__PythonScriptLibrary"

_HELPERS_PATH = Path(__file__).with_name("ue_side.py")


class UnrealBridgeError(RuntimeError):
    """Errore generico del bridge."""


class UnrealNotConnected(UnrealBridgeError):
    """Unreal non raggiungibile / plugin non attivi."""


class UnrealPythonError(UnrealBridgeError):
    """Il codice Python è stato eseguito ma ha sollevato un'eccezione."""

    def __init__(self, message: str, traceback_text: str = "", log: str = "") -> None:
        super().__init__(message)
        self.traceback_text = traceback_text
        self.log = log


@dataclass(frozen=True)
class BridgeConfig:
    host: str = "127.0.0.1"
    port: int = 30010
    timeout: float = 180.0

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @classmethod
    def from_env(cls) -> "BridgeConfig":
        return cls(
            host=os.environ.get("UE_MCP_HOST", "127.0.0.1"),
            port=int(os.environ.get("UE_MCP_PORT", "30010")),
            timeout=float(os.environ.get("UE_MCP_TIMEOUT", "180")),
        )


def load_helpers() -> str:
    """Sorgente Python eseguito dentro l'editor prima di ogni snippet."""
    return _HELPERS_PATH.read_text(encoding="utf-8")


def build_payload(code: str, helpers: str | None = None) -> str:
    """Incapsula ``code`` nell'harness che stampa il risultato come JSON.

    Nel codice utente si assegna la variabile ``result``; il suo valore viene
    serializzato e restituito al chiamante.
    """
    body = textwrap.indent(textwrap.dedent(code).strip("\n") or "pass", "    ")
    helper_src = load_helpers() if helpers is None else helpers
    harness = f'''
import json as _mcp_json
import traceback as _mcp_tb


def _mcp_main():
    result = None
{body}
    return result


try:
    _mcp_payload = {{"ok": True, "result": _mcp_main()}}
except Exception as _mcp_exc:  # noqa: BLE001
    _mcp_payload = {{
        "ok": False,
        "error": "{{0}}: {{1}}".format(type(_mcp_exc).__name__, _mcp_exc),
        "traceback": _mcp_tb.format_exc(),
    }}

print("{SENTINEL_START}" + _mcp_json.dumps(_mcp_payload, default=str) + "{SENTINEL_END}")
'''
    return helper_src + "\n" + harness


def extract_result(log_text: str) -> tuple[dict[str, Any] | None, str]:
    """Separa il JSON fra sentinelle dal resto del log.

    Si cerca dall'ultima sentinella all'indietro: il log di Unreal può contenere
    le sentinelle delle chiamate precedenti (tool come ue_read_log rileggono il
    file di log), e la nostra risposta è sempre l'ultima stampata.
    """
    start = log_text.rfind(SENTINEL_START)
    if start == -1:
        return None, log_text
    end = log_text.rfind(SENTINEL_END)
    if end == -1 or end < start:
        return None, log_text
    raw = log_text[start + len(SENTINEL_START) : end]
    clean = (log_text[:start] + log_text[end + len(SENTINEL_END) :]).strip()
    try:
        return json.loads(raw), clean
    except json.JSONDecodeError:
        return None, log_text


class UnrealBridge:
    """Connessione asincrona all'editor Unreal."""

    def __init__(self, config: BridgeConfig | None = None) -> None:
        self.config = config or BridgeConfig.from_env()
        self._client: httpx.AsyncClient | None = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            # trust_env=False: la connessione è a localhost, un eventuale proxy
            # di sistema (HTTP_PROXY/ALL_PROXY) va ignorato.
            self._client = httpx.AsyncClient(
                base_url=self.config.base_url,
                timeout=self.config.timeout,
                trust_env=False,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _request(self, method: str, path: str, payload: dict | None = None) -> Any:
        client = await self._http()
        try:
            response = await client.request(method, path, json=payload)
        except httpx.ConnectError as exc:
            raise UnrealNotConnected(
                f"Nessuna risposta da {self.config.base_url}. Controlla che l'editor Unreal sia "
                "aperto, che i plugin 'Remote Control API' e 'Python Editor Script Plugin' siano "
                "attivi e che il web server sia in ascolto (console: WebControl.StartServer)."
            ) from exc
        except httpx.ReadTimeout as exc:
            raise UnrealBridgeError(
                f"Timeout dopo {self.config.timeout}s. L'editor potrebbe essere occupato "
                "(compilazione shader, import pesante, PIE in corso)."
            ) from exc

        if response.status_code == 404:
            raise UnrealNotConnected(
                f"404 su {path}: la Remote Control API risponde ma la rotta non esiste. "
                "Verifica la versione di Unreal (serve UE 5.x)."
            )
        if response.status_code >= 400:
            raise UnrealBridgeError(
                f"HTTP {response.status_code} da {path}: {response.text[:800]}"
            )
        if not response.content:
            return {}
        try:
            return response.json()
        except json.JSONDecodeError:
            return {"raw": response.text}

    async def info(self) -> Any:
        """GET /remote/info — usato come health check."""
        return await self._request("GET", "/remote/info")

    async def call_object(
        self,
        object_path: str,
        function_name: str,
        parameters: dict[str, Any] | None = None,
        generate_transaction: bool = False,
    ) -> Any:
        payload = {
            "objectPath": object_path,
            "functionName": function_name,
            "parameters": parameters or {},
            "generateTransaction": generate_transaction,
        }
        return await self._request("PUT", "/remote/object/call", payload)

    async def exec_python_raw(self, code: str) -> tuple[str, Any]:
        """Esegue codice Python grezzo nell'editor. Ritorna (log, risposta)."""
        response = await self.call_object(
            PYTHON_LIBRARY_PATH,
            "ExecutePythonCommandEx",
            {
                "pythonCommand": code,
                "executionMode": "ExecuteFile",
                "fileExecutionScope": "Private",
            },
        )
        log_entries = (response or {}).get("LogOutput") or []
        log_text = "".join(entry.get("Output", "") for entry in log_entries)
        if not log_text and isinstance(response, dict):
            log_text = str(response.get("CommandResult") or "")
        return log_text, response

    async def run(self, code: str) -> Any:
        """Esegue uno snippet e restituisce il valore assegnato a ``result``.

        Solleva :class:`UnrealPythonError` se il codice fallisce dentro l'editor.
        """
        payload_code = build_payload(code)
        log_text, response = await self.exec_python_raw(payload_code)
        parsed, clean_log = extract_result(log_text)

        if parsed is None:
            raise UnrealBridgeError(
                "Nessun risultato leggibile dall'editor. Log Unreal:\n"
                + (clean_log[-2000:] or json.dumps(response, default=str)[:2000])
            )
        if not parsed.get("ok"):
            raise UnrealPythonError(
                parsed.get("error", "errore Python sconosciuto"),
                traceback_text=parsed.get("traceback", ""),
                log=clean_log,
            )
        return parsed.get("result")

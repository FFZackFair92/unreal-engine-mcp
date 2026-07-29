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

import asyncio
import hashlib
import json
import os
import textwrap
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import httpx

from .pyremote import NoEditorFoundError, PyRemoteClient, PyRemoteConfig, PyRemoteError

SENTINEL_START = "<<<MCP_JSON_BEGIN>>>"
SENTINEL_END = "<<<MCP_JSON_END>>>"

PYTHON_LIBRARY_PATH = "/Script/PythonScriptPlugin.Default__PythonScriptLibrary"

_HELPERS_PATH = Path(__file__).with_name("ue_side.py")

#: Nome del modulo in cui gli helper vengono installati dentro l'editor.
HELPERS_MODULE = "_mcp_helpers"


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


#: Trasporti disponibili verso l'editor.
#:
#: - ``pyremote``: il protocollo di Python remote execution del motore. Chiede
#:   solo il *Python Editor Script Plugin* e la casella *Enable Remote
#:   Execution*: niente file di configurazione.
#: - ``remotecontrol``: la Remote Control API su HTTP. Serve un plugin in più e
#:   un ``DefaultRemoteControl.ini`` scritto a mano, ma funziona anche con
#:   l'editor su un'altra macchina, dove il multicast non arriva.
#: - ``auto``: prova ``pyremote``, e se nessun editor risponde ricade
#:   sull'HTTP. È il default perché copre il caso facile senza chiedere nulla,
#:   senza togliere quello difficile.
TRANSPORTS = ("auto", "pyremote", "remotecontrol")


@dataclass(frozen=True)
class BridgeConfig:
    host: str = "127.0.0.1"
    port: int = 30010
    timeout: float = 180.0
    transport: str = "auto"

    def __post_init__(self) -> None:
        if self.transport not in TRANSPORTS:
            raise ValueError(
                "transport deve essere uno di %s, ricevuto %r"
                % (", ".join(TRANSPORTS), self.transport)
            )

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @classmethod
    def from_env(cls) -> BridgeConfig:
        return cls(
            host=os.environ.get("UE_MCP_HOST", "127.0.0.1"),
            port=int(os.environ.get("UE_MCP_PORT", "30010")),
            timeout=float(os.environ.get("UE_MCP_TIMEOUT", "180")),
            transport=os.environ.get("UE_MCP_TRANSPORT", "auto").strip().lower(),
        )


def load_helpers() -> str:
    """Sorgente Python degli helper installati dentro l'editor."""
    return _HELPERS_PATH.read_text(encoding="utf-8")


def helpers_hash(helpers: str | None = None) -> str:
    """Impronta del sorgente helper, per capire se quelli nell'editor sono aggiornati."""
    source = load_helpers() if helpers is None else helpers
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]


def build_install_payload(helpers: str | None = None) -> str:
    """Snippet che installa gli helper come modulo dentro l'editor.

    Gli helper sono ~750 righe: rispedirli a ogni chiamata significa farli
    riparsare e rieseguire dall'interprete dell'editor ogni volta. Li si
    installa una volta sola in ``sys.modules``, marcati con l'hash del
    sorgente, e le chiamate successive li importano soltanto.
    """
    source = load_helpers() if helpers is None else helpers
    digest = helpers_hash(source)
    return f'''
import sys as _mcp_sys
import types as _mcp_types
import json as _mcp_json

_mcp_module = _mcp_types.ModuleType({HELPERS_MODULE!r})
exec(compile({source!r}, "<unreal-mcp-helpers>", "exec"), _mcp_module.__dict__)
_mcp_module.MCP_HELPERS_HASH = {digest!r}
_mcp_sys.modules[{HELPERS_MODULE!r}] = _mcp_module

print("{SENTINEL_START}" + _mcp_json.dumps({{"ok": True, "result": {{"installed": {digest!r}}}}}) + "{SENTINEL_END}")
'''


def build_payload(code: str, helpers: str | None = None, digest: str | None = None) -> str:
    """Incapsula ``code`` nell'harness che stampa il risultato come JSON.

    Nel codice utente si assegna la variabile ``result``; il suo valore viene
    serializzato e restituito al chiamante. Gli helper non viaggiano con lo
    snippet: si importano dal modulo installato da
    :func:`build_install_payload`. Se manca o è di una versione diversa, la
    risposta lo segnala e il bridge reinstalla e riprova.
    """
    body = textwrap.indent(textwrap.dedent(code).strip("\n") or "pass", "    ")
    expected = helpers_hash(helpers) if digest is None else digest
    return f'''
import json as _mcp_json
import sys as _mcp_sys
import traceback as _mcp_tb

_mcp_helpers = _mcp_sys.modules.get({HELPERS_MODULE!r})

if _mcp_helpers is None or getattr(_mcp_helpers, "MCP_HELPERS_HASH", None) != {expected!r}:
    print("{SENTINEL_START}" + _mcp_json.dumps({{"ok": False, "helpers_missing": True}}) + "{SENTINEL_END}")
else:
    globals().update(
        {{k: v for k, v in _mcp_helpers.__dict__.items() if not k.startswith("__")}}
    )

    def _mcp_main():
        result = None
{textwrap.indent(body, "    ")}
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

    def __init__(
        self, config: BridgeConfig | None = None, pyremote: PyRemoteClient | None = None
    ) -> None:
        self.config = config or BridgeConfig.from_env()
        self._client: httpx.AsyncClient | None = None
        #: Hash degli helper che risultano installati nell'editor corrente.
        #: None finché non se ne ha conferma (o dopo un riavvio dell'editor).
        self._helpers_digest: str | None = None
        #: Client del trasporto nativo. Costruito su richiesta: con
        #: transport="remotecontrol" non serve aprire nessun socket multicast.
        self._pyremote = pyremote
        #: Quale trasporto sta effettivamente servendo le chiamate. Con "auto"
        #: si decide alla prima e non si torna indietro: alternare i due a ogni
        #: chiamata significherebbe reinstallare gli helper ogni volta.
        self._trasporto_scelto: str | None = (
            None if self.config.transport == "auto" else self.config.transport
        )
        #: Perché il canale nativo non è andato, quando si ricade sull'HTTP.
        self._motivo_pyremote: str | None = None

    # --------------------------------------------------------------- trasporto

    @property
    def transport(self) -> str | None:
        """Il trasporto in uso, o None finché non è stato deciso."""
        return self._trasporto_scelto

    def _pyremote_client(self) -> PyRemoteClient:
        if self._pyremote is None:
            self._pyremote = PyRemoteClient(
                replace(PyRemoteConfig.from_env(), command_timeout=self.config.timeout)
            )
        return self._pyremote

    async def _pyremote_exec(self, code: str) -> str:
        """Esegue lo snippet sul canale nativo, da un thread (i socket sono sincroni)."""
        client = self._pyremote_client()
        esito = await asyncio.to_thread(client.run, code, self.config.timeout)
        return esito.log

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

    def forget_helpers(self) -> None:
        """Dimentica gli helper installati: da chiamare quando l'editor riparte.

        Chiude anche il canale nativo: dopo un riavvio dell'editor il socket
        punta a un processo che non c'è più.
        """
        self._helpers_digest = None
        if self._pyremote is not None:
            self._pyremote.close()
        if self.config.transport == "auto":
            self._trasporto_scelto = None

    async def aclose(self) -> None:
        self.forget_helpers()
        self._pyremote = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _request(self, method: str, path: str, payload: dict | None = None) -> Any:
        client = await self._http()
        try:
            response = await client.request(method, path, json=payload)
        # ConnectTimeout non discende da ConnectError ma da TimeoutException:
        # vanno catturate entrambe. Una porta chiusa di solito rifiuta la
        # connessione (ConnectError), ma se un firewall scarta i pacchetti
        # invece di rifiutarli — comune su Windows — si ottiene un timeout, e
        # senza questo l'utente vedrebbe un'eccezione httpx grezza al posto
        # della diagnosi.
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            scaduto = isinstance(exc, httpx.ConnectTimeout)
            raise UnrealNotConnected(
                f"Nessuna risposta da {self.config.base_url}. Controlla che l'editor Unreal sia "
                "aperto, che i plugin 'Remote Control API' e 'Python Editor Script Plugin' siano "
                "attivi e che il web server sia in ascolto (console: WebControl.StartServer)."
                + (
                    " La connessione è scaduta invece di essere rifiutata: di solito è un "
                    "firewall che filtra la porta, o un host/porta sbagliati "
                    "(UE_MCP_HOST / UE_MCP_PORT)."
                    if scaduto
                    else ""
                )
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
        """Health check. Sul canale nativo è la scoperta, sull'HTTP è /remote/info."""
        if self._trasporto_scelto == "pyremote":
            nodo = await asyncio.to_thread(self._pyremote_client().connect)
            return {"transport": "pyremote", "project": nodo.project_name,
                    "engine": nodo.engine_version, "node": nodo.node_id}
        if self._trasporto_scelto == "remotecontrol":
            return await self._request("GET", "/remote/info")

        try:
            nodo = await asyncio.to_thread(self._pyremote_client().connect)
        except PyRemoteError:
            self._pyremote = None
        else:
            self._trasporto_scelto = "pyremote"
            return {"transport": "pyremote", "project": nodo.project_name,
                    "engine": nodo.engine_version, "node": nodo.node_id}
        risposta = await self._request("GET", "/remote/info")
        self._trasporto_scelto = "remotecontrol"
        return risposta

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

    async def exec_python(self, code: str) -> str:
        """Esegue codice Python nell'editor sul trasporto disponibile.

        Con ``transport="auto"`` la prima chiamata decide: si prova il canale
        nativo e, se nessun editor risponde alla scoperta, si passa all'HTTP.
        La scelta poi resta, perché ogni cambio di trasporto costerebbe una
        reinstallazione degli helper.
        """
        if self._trasporto_scelto == "pyremote":
            return await self._pyremote_exec(code)

        if self._trasporto_scelto == "remotecontrol":
            try:
                log, _ = await self.exec_python_raw(code)
            except UnrealBridgeError:
                # Un trasporto scelto una volta non va creduto per sempre. La
                # scelta cade spesso sulla prima chiamata, che con
                # ue_editor_open avviene mentre l'editor sta ancora caricando:
                # il canale nativo non risponde ancora, l'HTTP sì, e la
                # decisione resterebbe congelata anche dopo che il nativo è
                # diventato disponibile — o dopo che l'HTTP ha smesso di
                # funzionare, per esempio perché manca il gate della Remote
                # Control API. Se quello scelto fallisce, si riprova l'altro.
                if self.config.transport != "auto":
                    raise
                self._trasporto_scelto = None
                self._helpers_digest = None
                try:
                    log = await self._pyremote_exec(code)
                except PyRemoteError:
                    self._trasporto_scelto = "remotecontrol"
                    self._pyremote = None
                    raise
                self._trasporto_scelto = "pyremote"
            return log

        try:
            log = await self._pyremote_exec(code)
        except NoEditorFoundError:
            self._pyremote = None
        except PyRemoteError as exc:
            # Il canale nativo c'è ma non funziona (firewall, interfaccia
            # sbagliata): vale la pena provare l'HTTP prima di arrendersi, ma
            # il motivo va conservato — se fallisce anche quello, il primo
            # errore è quasi sempre quello utile.
            self._motivo_pyremote = str(exc)
            self._pyremote = None
        else:
            self._trasporto_scelto = "pyremote"
            return log

        try:
            log, _ = await self.exec_python_raw(code)
        except UnrealNotConnected as exc:
            motivo = self._motivo_pyremote
            raise UnrealNotConnected(
                str(exc)
                + "\n\nAnche il canale nativo (Python remote execution) non ha "
                + (
                    "risposto: " + motivo
                    if motivo
                    else "trovato editor sul gruppo multicast."
                )
                + "\nÈ la via più semplice: basta il 'Python Editor Script Plugin' "
                "e 'Enable Remote Execution' in Project Settings → Python, senza "
                "DefaultRemoteControl.ini."
            ) from exc
        self._trasporto_scelto = "remotecontrol"
        return log

    async def exec_python_raw(self, code: str) -> tuple[str, Any]:
        """Esegue codice Python grezzo via Remote Control API. Ritorna (log, risposta)."""
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

    async def _exec_parsed(self, payload_code: str) -> tuple[dict[str, Any], str]:
        """Esegue un payload già costruito e ne estrae la risposta JSON."""
        log_text = await self.exec_python(payload_code)
        parsed, clean_log = extract_result(log_text)
        if parsed is None:
            raise UnrealBridgeError(
                "Nessun risultato leggibile dall'editor (trasporto: %s). Log Unreal:\n%s"
                % (self._trasporto_scelto or "non deciso", clean_log[-2000:] or "(vuoto)")
            )
        return parsed, clean_log

    async def install_helpers(self) -> str:
        """Installa (o aggiorna) il modulo helper dentro l'editor."""
        digest = helpers_hash()
        parsed, clean_log = await self._exec_parsed(build_install_payload())
        if not parsed.get("ok"):
            raise UnrealPythonError(
                "Installazione degli helper fallita: %s"
                % parsed.get("error", "errore sconosciuto"),
                traceback_text=parsed.get("traceback", ""),
                log=clean_log,
            )
        self._helpers_digest = digest
        return digest

    async def run(self, code: str) -> Any:
        """Esegue uno snippet e restituisce il valore assegnato a ``result``.

        Gli helper vengono installati nell'editor alla prima chiamata e
        riutilizzati dalle successive. Se l'editor è stato riavviato (o gli
        helper sono cambiati) lo snippet lo segnala e si reinstalla al volo:
        il costo del doppio round-trip si paga una volta sola.

        Solleva :class:`UnrealPythonError` se il codice fallisce dentro l'editor.
        """
        digest = helpers_hash()

        if self._helpers_digest != digest:
            await self.install_helpers()

        parsed, clean_log = await self._exec_parsed(build_payload(code, digest=digest))

        if parsed.get("helpers_missing"):
            # L'editor è ripartito da quando abbiamo installato: rifacciamo.
            self._helpers_digest = None
            await self.install_helpers()
            parsed, clean_log = await self._exec_parsed(build_payload(code, digest=digest))
            if parsed.get("helpers_missing"):
                raise UnrealBridgeError(
                    "Gli helper non risultano installati nell'editor nemmeno dopo la "
                    "reinstallazione. Controlla il log di Unreal per errori di import."
                )

        if not parsed.get("ok"):
            raise UnrealPythonError(
                parsed.get("error", "errore Python sconosciuto"),
                traceback_text=parsed.get("traceback", ""),
                log=clean_log,
            )
        return parsed.get("result")

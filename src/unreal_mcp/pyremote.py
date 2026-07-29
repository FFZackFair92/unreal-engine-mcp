"""Trasporto alternativo: il protocollo di *Python remote execution* di Unreal.

Perché esiste
-------------
La Remote Control API funziona, ma per attivarla servono due plugin **e** un
`DefaultRemoteControl.ini` scritto a mano con due gate distinti: è il punto in
cui si perde chi prova il progetto per la prima volta.

Unreal però espone anche un secondo canale, nato per far girare Python da un
IDE esterno, che chiede molto meno: basta il *Python Editor Script Plugin* e la
casella **Enable Remote Execution** in Project Settings → Python. Nessun file
di configurazione, nessuna porta HTTP da aprire.

Il protocollo
-------------
Due canali, entrambi con messaggi JSON UTF-8::

    scoperta   UDP multicast 239.0.0.1:6766
               -> {"type": "ping"}      <- {"type": "pong",  data: versione, progetto, ...}

    comandi    TCP, ma la connessione la apre *l'editor* verso di noi:
               -> {"type": "open_connection", data: {command_ip, command_port}}
               l'editor si collega, poi su quel socket:
               -> {"type": "command", data: {command, exec_mode, unattended}}
               <- {"type": "command_result", data: {success, result, output}}

Ogni messaggio porta `version`, `magic`, `source` (il nostro id) e `dest`.

Il fatto che sia l'editor a connettersi a noi è la parte controintuitiva, ed è
anche il punto in cui un firewall si fa sentire: il socket in ascolto è nostro,
e il pacchetto in ingresso arriva dal processo dell'editor.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import time
import uuid
from dataclasses import dataclass, field

PROTOCOL_VERSION = 1
PROTOCOL_MAGIC = "ue_py"

TYPE_PING = "ping"
TYPE_PONG = "pong"
TYPE_OPEN_CONNECTION = "open_connection"
TYPE_CLOSE_CONNECTION = "close_connection"
TYPE_COMMAND = "command"
TYPE_COMMAND_RESULT = "command_result"

#: `ExecuteFile` esegue lo snippet come un file: è l'unico modo di mandare più
#: istruzioni in una volta e di vedere l'output di `print`, che è come il
#: bridge recupera il risultato.
MODE_EXEC_FILE = "ExecuteFile"

_RECV = 65536


class PyRemoteError(RuntimeError):
    """Errore del trasporto Python remote execution."""


class NoEditorFoundError(PyRemoteError):
    """Nessun editor ha risposto al ping di scoperta."""


@dataclass(frozen=True)
class PyRemoteConfig:
    multicast_group: str = "239.0.0.1"
    multicast_port: int = 6766
    #: Interfaccia da cui inviare il multicast. 0.0.0.0 lascia scegliere al
    #: sistema; su macchine con più schede (VPN, WSL, Hyper-V) può servire
    #: indicare quella giusta, o l'editor non vede mai il ping.
    multicast_bind: str = "0.0.0.0"  # noqa: S104 - interfaccia di uscita, non un bind in ascolto
    #: TTL 0 tiene i pacchetti sulla macchina locale. Alzarlo espone
    #: l'esecuzione di codice arbitrario alla rete: farlo solo consapevolmente.
    multicast_ttl: int = 0
    command_host: str = "127.0.0.1"
    command_port: int = 0  # 0 = porta effimera scelta dal sistema
    discovery_timeout: float = 2.0
    command_timeout: float = 180.0
    #: Con più editor aperti, quale progetto preferire.
    prefer_project: str | None = None

    @classmethod
    def from_env(cls) -> PyRemoteConfig:
        return cls(
            multicast_group=os.environ.get("UE_MCP_MULTICAST_GROUP", "239.0.0.1"),
            multicast_port=int(os.environ.get("UE_MCP_MULTICAST_PORT", "6766")),
            multicast_bind=os.environ.get("UE_MCP_MULTICAST_BIND", "0.0.0.0"),  # noqa: S104
            multicast_ttl=int(os.environ.get("UE_MCP_MULTICAST_TTL", "0")),
            command_host=os.environ.get("UE_MCP_COMMAND_HOST", "127.0.0.1"),
            command_port=int(os.environ.get("UE_MCP_COMMAND_PORT", "0")),
            discovery_timeout=float(os.environ.get("UE_MCP_DISCOVERY_TIMEOUT", "2")),
            command_timeout=float(os.environ.get("UE_MCP_TIMEOUT", "180")),
            prefer_project=os.environ.get("UE_MCP_PROJECT") or None,
        )


@dataclass(frozen=True)
class EditorNode:
    """Un editor che ha risposto alla scoperta."""

    node_id: str
    engine_version: str = ""
    project_name: str = ""
    project_root: str = ""
    machine: str = ""
    user: str = ""

    @classmethod
    def from_pong(cls, message: dict) -> EditorNode:
        data = message.get("data") or {}
        return cls(
            node_id=str(message.get("source", "")),
            engine_version=str(data.get("engine_version", "")),
            project_name=str(data.get("project_name", "")),
            project_root=str(data.get("project_root", "")),
            machine=str(data.get("machine", "")),
            user=str(data.get("user", "")),
        )


@dataclass
class CommandOutcome:
    success: bool
    result: str = ""
    output: list[dict] = field(default_factory=list)

    @property
    def log(self) -> str:
        """Le righe stampate dall'editor, concatenate come le dà la Remote Control."""
        return "".join(str(riga.get("output", "")) for riga in self.output)


def encode(msg_type: str, source: str, dest: str | None = None, data: dict | None = None) -> bytes:
    messaggio = {
        "version": PROTOCOL_VERSION,
        "magic": PROTOCOL_MAGIC,
        "source": source,
        "type": msg_type,
    }
    if dest is not None:
        messaggio["dest"] = dest
    if data is not None:
        messaggio["data"] = data
    return json.dumps(messaggio).encode("utf-8")


def decode(payload: bytes, atteso_source: str | None = None) -> dict | None:
    """Decodifica un messaggio, scartando quelli non nostri.

    Restituisce None invece di sollevare: sul gruppo multicast può arrivare di
    tutto, compresi i nostri stessi pacchetti di ritorno, e un'eccezione qui
    interromperebbe la scoperta al primo pacchetto estraneo.
    """
    try:
        messaggio = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(messaggio, dict):
        return None
    if messaggio.get("version") != PROTOCOL_VERSION or messaggio.get("magic") != PROTOCOL_MAGIC:
        return None
    if "source" not in messaggio or "type" not in messaggio:
        return None
    if atteso_source is not None and messaggio.get("source") == atteso_source:
        return None  # è un nostro pacchetto tornato indietro
    return messaggio


class PyRemoteClient:
    """Client sincrono. Il bridge lo usa da un thread separato."""

    def __init__(self, config: PyRemoteConfig | None = None) -> None:
        self.config = config or PyRemoteConfig.from_env()
        self.node_id = str(uuid.uuid4())
        self._socket: socket.socket | None = None
        self._node: EditorNode | None = None

    # ------------------------------------------------------------- scoperta

    def _multicast_socket(self) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(
            socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, self.config.multicast_ttl
        )
        sock.setsockopt(
            socket.IPPROTO_IP,
            socket.IP_MULTICAST_IF,
            socket.inet_aton(self.config.multicast_bind),
        )
        # LOOP acceso: editor e server girano quasi sempre sulla stessa macchina,
        # e senza questo il pacchetto non torna mai indietro dall'interfaccia di
        # loopback.
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        sock.bind((self.config.multicast_bind, self.config.multicast_port))
        sock.setsockopt(
            socket.IPPROTO_IP,
            socket.IP_ADD_MEMBERSHIP,
            struct.pack(
                "4s4s",
                socket.inet_aton(self.config.multicast_group),
                socket.inet_aton(self.config.multicast_bind),
            ),
        )
        return sock

    def discover(self, timeout: float | None = None) -> list[EditorNode]:
        """Manda un ping e raccoglie i pong finché scade il tempo."""
        attesa = self.config.discovery_timeout if timeout is None else timeout
        try:
            sock = self._multicast_socket()
        except OSError as exc:
            raise PyRemoteError(
                "Impossibile aprire il socket multicast su %s:%d (%s). Un'altra "
                "applicazione potrebbe già occupare la porta, oppure "
                "UE_MCP_MULTICAST_BIND indica un'interfaccia che non esiste."
                % (self.config.multicast_bind, self.config.multicast_port, exc)
            ) from exc

        trovati: dict[str, EditorNode] = {}
        try:
            sock.sendto(
                encode(TYPE_PING, self.node_id),
                (self.config.multicast_group, self.config.multicast_port),
            )
            scadenza = time.monotonic() + attesa
            while True:
                rimasto = scadenza - time.monotonic()
                if rimasto <= 0:
                    break
                sock.settimeout(rimasto)
                try:
                    payload, _ = sock.recvfrom(_RECV)
                except TimeoutError:
                    break
                except OSError:
                    break
                messaggio = decode(payload, atteso_source=self.node_id)
                if messaggio is None or messaggio.get("type") != TYPE_PONG:
                    continue
                nodo = EditorNode.from_pong(messaggio)
                trovati[nodo.node_id] = nodo
        finally:
            sock.close()
        return list(trovati.values())

    # ------------------------------------------------------------ connessione

    def _scegli(self, nodi: list[EditorNode]) -> EditorNode:
        preferito = self.config.prefer_project
        if preferito:
            for nodo in nodi:
                if nodo.project_name.lower() == preferito.lower():
                    return nodo
            raise PyRemoteError(
                "Nessun editor aperto sul progetto %r. Aperti: %s."
                % (preferito, ", ".join(n.project_name or n.node_id for n in nodi))
            )
        return nodi[0]

    def connect(self) -> EditorNode:
        """Scopre un editor e apre il canale comandi."""
        self.close()
        nodi = self.discover()
        if not nodi:
            raise NoEditorFoundError(
                "Nessun editor Unreal ha risposto sul gruppo multicast %s:%d. "
                "Controlla che l'editor sia aperto, che il plugin 'Python Editor "
                "Script Plugin' sia attivo e che 'Enable Remote Execution' sia "
                "spuntato in Project Settings → Plugins → Python."
                % (self.config.multicast_group, self.config.multicast_port)
            )
        nodo = self._scegli(nodi)

        ascolto = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ascolto.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            ascolto.bind((self.config.command_host, self.config.command_port))
            ascolto.listen(1)
            host, porta = ascolto.getsockname()[:2]

            mcast = self._multicast_socket()
            try:
                mcast.sendto(
                    encode(
                        TYPE_OPEN_CONNECTION,
                        self.node_id,
                        dest=nodo.node_id,
                        data={"command_ip": host, "command_port": porta},
                    ),
                    (self.config.multicast_group, self.config.multicast_port),
                )
            finally:
                mcast.close()

            ascolto.settimeout(self.config.discovery_timeout + 5.0)
            try:
                canale, _ = ascolto.accept()
            except TimeoutError as exc:
                raise PyRemoteError(
                    "L'editor (%s) ha risposto alla scoperta ma non si è collegato a "
                    "%s:%d. È l'editor a connettersi verso di noi, quindi di solito è "
                    "il firewall a bloccarlo: consenti il processo UnrealEditor."
                    % (nodo.project_name or nodo.node_id, host, porta)
                ) from exc
        finally:
            ascolto.close()

        canale.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._socket = canale
        self._node = nodo
        return nodo

    @property
    def node(self) -> EditorNode | None:
        return self._node

    def is_connected(self) -> bool:
        return self._socket is not None

    # --------------------------------------------------------------- comandi

    def run(self, code: str, timeout: float | None = None) -> CommandOutcome:
        """Esegue codice Python nell'editor e restituisce output e log."""
        if self._socket is None:
            self.connect()
        assert self._socket is not None and self._node is not None

        attesa = self.config.command_timeout if timeout is None else timeout
        payload = encode(
            TYPE_COMMAND,
            self.node_id,
            dest=self._node.node_id,
            data={"command": code, "unattended": True, "exec_mode": MODE_EXEC_FILE},
        )
        try:
            self._socket.sendall(payload)
        except OSError as exc:
            self.close()
            raise PyRemoteError(
                "Canale comandi caduto durante l'invio (%s). L'editor è stato "
                "chiuso o riavviato: la chiamata successiva riconnette." % exc
            ) from exc
        return self._ricevi(attesa)

    def _ricevi(self, timeout: float) -> CommandOutcome:
        """Accumula byte finché un `command_result` completo si decodifica.

        Il protocollo non ha un prefisso di lunghezza: un risultato grosso —
        una lista di asset, un traceback — arriva spezzato su più segmenti TCP,
        e l'unico modo di sapere che è finito è che il JSON diventi valido.
        """
        assert self._socket is not None
        scadenza = time.monotonic() + timeout
        buffer = b""
        while True:
            rimasto = scadenza - time.monotonic()
            if rimasto <= 0:
                raise PyRemoteError(
                    "Nessuna risposta dall'editor entro %.0fs. Potrebbe essere "
                    "occupato (compilazione shader, import pesante, PIE in corso)."
                    % timeout
                )
            self._socket.settimeout(rimasto)
            try:
                chunk = self._socket.recv(_RECV)
            except TimeoutError as exc:
                raise PyRemoteError(
                    "Nessuna risposta dall'editor entro %.0fs." % timeout
                ) from exc
            except OSError as exc:
                self.close()
                raise PyRemoteError("Canale comandi interrotto: %s" % exc) from exc
            if not chunk:
                self.close()
                raise PyRemoteError(
                    "L'editor ha chiuso il canale comandi: probabilmente è stato chiuso."
                )
            buffer += chunk
            try:
                messaggio = json.loads(buffer.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue  # messaggio ancora incompleto
            buffer = b""
            if not isinstance(messaggio, dict):
                continue
            if messaggio.get("type") != TYPE_COMMAND_RESULT:
                continue  # traffico non nostro sullo stesso socket
            dati = messaggio.get("data") or {}
            return CommandOutcome(
                success=bool(dati.get("success")),
                result=str(dati.get("result", "")),
                output=list(dati.get("output") or []),
            )

    def close(self) -> None:
        if self._socket is not None:
            try:
                self._socket.sendall(
                    encode(
                        TYPE_CLOSE_CONNECTION,
                        self.node_id,
                        dest=self._node.node_id if self._node else None,
                    )
                )
            except OSError:
                pass
            try:
                self._socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._socket.close()
            self._socket = None
        self._node = None

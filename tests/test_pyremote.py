"""Test del trasporto Python remote execution.

Girano contro un editor finto che parla il protocollo vero su socket veri:
multicast per la scoperta, TCP per i comandi. Così il test copre anche il
framing, che è il punto in cui il protocollo si rompe davvero — non c'è un
prefisso di lunghezza, e un risultato grosso arriva spezzato.
"""

from __future__ import annotations

import json
import socket

import pytest
from fake_pyremote_node import FakePyRemoteNode

from unreal_mcp import pyremote
from unreal_mcp.bridge import BridgeConfig, UnrealBridge, UnrealNotConnected


def _porta_libera() -> int:
    """Una porta multicast diversa per ogni test: i test girano in parallelo
    sullo stesso host in CI, e due nodi sulla stessa porta si rubano i ping."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


@pytest.fixture
def nodo():
    n = FakePyRemoteNode(port=_porta_libera())
    yield n
    n.stop()


@pytest.fixture
def config(nodo):
    return pyremote.PyRemoteConfig(
        multicast_port=nodo.port, discovery_timeout=3.0, command_timeout=15.0
    )


# ------------------------------------------------------------------ messaggi


def test_encode_decode_giro_completo():
    grezzo = pyremote.encode("ping", "abc", dest="xyz", data={"k": 1})
    messaggio = pyremote.decode(grezzo)
    assert messaggio["type"] == "ping"
    assert messaggio["source"] == "abc"
    assert messaggio["dest"] == "xyz"
    assert messaggio["magic"] == pyremote.PROTOCOL_MAGIC


@pytest.mark.parametrize(
    "payload",
    [
        b"non json",
        b"[1, 2, 3]",
        json.dumps({"version": 99, "magic": "ue_py", "source": "a", "type": "ping"}).encode(),
        json.dumps({"version": 1, "magic": "altro", "source": "a", "type": "ping"}).encode(),
        json.dumps({"version": 1, "magic": "ue_py", "type": "ping"}).encode(),
    ],
)
def test_decode_scarta_i_messaggi_estranei(payload):
    """Sul gruppo multicast arriva di tutto: scartare, non sollevare."""
    assert pyremote.decode(payload) is None


def test_decode_ignora_i_propri_pacchetti():
    grezzo = pyremote.encode("ping", "io-stesso")
    assert pyremote.decode(grezzo, atteso_source="io-stesso") is None
    assert pyremote.decode(grezzo, atteso_source="qualcun-altro") is not None


# ------------------------------------------------------------------ scoperta


def test_scoperta_trova_leditor(config, nodo):
    client = pyremote.PyRemoteClient(config)
    trovati = client.discover()
    assert [n.project_name for n in trovati] == ["MyGame"]
    assert trovati[0].engine_version.startswith("5.")


def test_scoperta_senza_editor_e_vuota():
    client = pyremote.PyRemoteClient(
        pyremote.PyRemoteConfig(multicast_port=_porta_libera(), discovery_timeout=0.5)
    )
    assert client.discover() == []


def test_connessione_senza_editor_spiega_cosa_fare():
    client = pyremote.PyRemoteClient(
        pyremote.PyRemoteConfig(multicast_port=_porta_libera(), discovery_timeout=0.5)
    )
    with pytest.raises(pyremote.NoEditorFoundError, match="Enable Remote Execution"):
        client.connect()


def test_progetto_preferito_inesistente(config, nodo):
    client = pyremote.PyRemoteClient(
        pyremote.PyRemoteConfig(
            multicast_port=nodo.port, discovery_timeout=3.0, prefer_project="AltroGioco"
        )
    )
    with pytest.raises(pyremote.PyRemoteError, match="AltroGioco"):
        client.connect()


def test_editor_che_non_si_ricollega(nodo):
    """Se l'editor risponde ma non apre il canale, la colpa è del firewall."""
    nodo.esegui = False
    client = pyremote.PyRemoteClient(
        pyremote.PyRemoteConfig(multicast_port=nodo.port, discovery_timeout=1.0)
    )
    with pytest.raises(pyremote.PyRemoteError, match="firewall"):
        client.connect()


# ------------------------------------------------------------------- comandi


def test_esegue_codice_e_raccoglie_loutput(config, nodo):
    client = pyremote.PyRemoteClient(config)
    try:
        esito = client.run("print('ciao dal finto editor')")
        assert esito.success is True
        assert "ciao dal finto editor" in esito.log
        assert nodo.comandi
    finally:
        client.close()


def test_risultato_grosso_arriva_intero(config, nodo):
    """Senza prefisso di lunghezza, un payload grosso si spezza su più segmenti."""
    client = pyremote.PyRemoteClient(config)
    try:
        esito = client.run("print('x' * 300000)")
        assert esito.success is True
        assert len(esito.log) > 290000
    finally:
        client.close()


def test_eccezione_nelleditor_non_e_un_errore_di_trasporto(config, nodo):
    client = pyremote.PyRemoteClient(config)
    try:
        esito = client.run("raise ValueError('rotto')")
        assert esito.success is False
        assert "ValueError" in esito.log
    finally:
        client.close()


# -------------------------------------------------------- integrazione bridge


async def test_il_bridge_usa_il_trasporto_nativo(config, nodo, unreal, monkeypatch):
    """Il giro completo: harness, helper installati una volta, risultato JSON.

    La fixture `unreal` serve solo a mettere il modulo finto in sys.modules:
    il nodo gira in questo stesso processo, e gli helper lo importano.
    """
    monkeypatch.setenv("UE_MCP_MULTICAST_PORT", str(nodo.port))
    monkeypatch.setenv("UE_MCP_DISCOVERY_TIMEOUT", "3")
    bridge = UnrealBridge(BridgeConfig(timeout=15, transport="pyremote"))
    try:
        assert await bridge.run("result = 21 * 2") == 42
        assert bridge.transport == "pyremote"
        # Gli helper si installano una volta sola: la seconda chiamata no.
        prima = len(nodo.comandi)
        await bridge.run("result = 1")
        assert len(nodo.comandi) - prima == 1
    finally:
        await bridge.aclose()


async def test_auto_sceglie_il_nativo_quando_c_e(config, nodo, unreal, monkeypatch):
    monkeypatch.setenv("UE_MCP_MULTICAST_PORT", str(nodo.port))
    monkeypatch.setenv("UE_MCP_DISCOVERY_TIMEOUT", "3")
    # porta HTTP chiusa: se ricadesse sull'HTTP fallirebbe
    bridge = UnrealBridge(BridgeConfig(port=1, timeout=15, transport="auto"))
    try:
        assert await bridge.run("result = 'nativo'") == "nativo"
        assert bridge.transport == "pyremote"
    finally:
        await bridge.aclose()


async def test_auto_ricade_sull_http(unreal, monkeypatch):
    """Senza editor sul multicast si passa alla Remote Control API."""
    monkeypatch.setenv("UE_MCP_MULTICAST_PORT", str(_porta_libera()))
    monkeypatch.setenv("UE_MCP_DISCOVERY_TIMEOUT", "0.5")
    bridge = UnrealBridge(
        BridgeConfig(host="127.0.0.1", port=unreal.port, timeout=30, transport="auto")
    )
    try:
        assert await bridge.run("result = 'http'") == "http"
        assert bridge.transport == "remotecontrol"
    finally:
        await bridge.aclose()


async def test_auto_senza_niente_nomina_entrambi(monkeypatch):
    """Se falliscono tutti e due, l'errore deve dire come sistemare il più facile."""
    monkeypatch.setenv("UE_MCP_MULTICAST_PORT", str(_porta_libera()))
    monkeypatch.setenv("UE_MCP_DISCOVERY_TIMEOUT", "0.5")
    bridge = UnrealBridge(BridgeConfig(port=1, timeout=2, transport="auto"))
    try:
        with pytest.raises(UnrealNotConnected) as errore:
            await bridge.run("result = 1")
        testo = str(errore.value)
        assert "Remote Control" in testo
        assert "Enable Remote Execution" in testo
    finally:
        await bridge.aclose()


def test_transport_non_valido_e_rifiutato_subito():
    with pytest.raises(ValueError, match="transport"):
        BridgeConfig(transport="carrozza")

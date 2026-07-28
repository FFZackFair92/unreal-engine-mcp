import pytest

from unreal_mcp.bridge import (
    HELPERS_MODULE,
    BridgeConfig,
    UnrealBridge,
    UnrealNotConnected,
    UnrealPythonError,
    build_install_payload,
    build_payload,
    extract_result,
    helpers_hash,
)


def test_build_payload_importa_gli_helper_invece_di_rispedirli():
    """Gli helper viaggiano una volta sola: lo snippet li importa e basta."""
    code = build_payload("result = 1 + 1")
    assert "def _mcp_main" in code            # harness
    assert "result = 1 + 1" in code
    assert HELPERS_MODULE in code             # importati dal modulo installato
    assert "def mcp_project_status" not in code   # non più inclusi nel payload
    assert helpers_hash() in code             # con il controllo di versione


def test_payload_molto_piu_piccolo_degli_helper():
    """Il senso della cache: il costo per chiamata crolla."""
    installa = build_install_payload()
    chiamata = build_payload("result = 1")
    assert len(chiamata) < len(installa) / 5


def test_install_payload_registra_il_modulo():
    code = build_install_payload()
    assert "def mcp_project_status" in code    # il sorgente completo, una volta
    assert "sys.modules" in code
    assert "MCP_HELPERS_HASH" in code


def test_extract_result_separa_json_e_log():
    log = 'rumore\n<<<MCP_JSON_BEGIN>>>{"ok": true, "result": 5}<<<MCP_JSON_END>>>\naltro'
    payload, clean = extract_result(log)
    assert payload == {"ok": True, "result": 5}
    assert "MCP_JSON" not in clean
    assert "rumore" in clean


def test_extract_result_ignora_sentinelle_nel_log():
    """Il log di Unreal contiene le sentinelle delle chiamate precedenti:
    conta solo l'ultima, che è la risposta corrente."""
    log = (
        'vecchia: <<<MCP_JSON_BEGIN>>>{"ok": false, "error": "boom"}<<<MCP_JSON_END>>>\n'
        '<<<MCP_JSON_BEGIN>>>{"ok": true, "result": 7}<<<MCP_JSON_END>>>'
    )
    payload, _ = extract_result(log)
    assert payload == {"ok": True, "result": 7}


def test_extract_result_senza_sentinella():
    payload, clean = extract_result("solo log")
    assert payload is None
    assert clean == "solo log"


async def test_info(bridge):
    info = await bridge.info()
    assert info["Name"] == "FakeUnreal"


async def test_run_ritorna_valore(bridge):
    assert await bridge.run("result = 40 + 2") == 42


async def test_run_ha_accesso_agli_helper(bridge):
    version = await bridge.run("result = mcp_project_status()['engine_version']")
    assert "fake" in version


async def test_helper_installati_una_volta_sola(bridge, unreal):
    """Il costo dell'installazione si paga alla prima chiamata, non a ogni chiamata."""
    await bridge.run("result = 1")
    dopo_la_prima = len(unreal.calls)
    assert dopo_la_prima == 2          # installazione + esecuzione

    await bridge.run("result = 2")
    await bridge.run("result = 3")
    assert len(unreal.calls) == dopo_la_prima + 2   # una sola chiamata ciascuna


async def test_reinstalla_se_l_editor_e_ripartito(bridge, unreal):
    """Un riavvio dell'editor svuota sys.modules: il bridge se ne accorge e rimedia."""
    await bridge.run("result = 1")
    assert await bridge.run("result = mcp_project_status()['python_ok']") is True

    # simula il riavvio: gli helper spariscono dall'editor ma il bridge non lo sa
    import sys

    sys.modules.pop(HELPERS_MODULE, None)
    assert bridge._helpers_digest is not None

    assert await bridge.run("result = mcp_project_status()['python_ok']") is True


async def test_helper_ricaricati_se_il_sorgente_cambia(bridge, monkeypatch):
    """Modificare ue_side.py deve avere effetto senza riavviare il server MCP."""
    await bridge.run("result = 1")
    installato = bridge._helpers_digest

    monkeypatch.setattr("unreal_mcp.bridge.helpers_hash", lambda *a, **k: "hash-diverso")
    monkeypatch.setattr(
        "unreal_mcp.bridge.load_helpers",
        lambda: "def mcp_nuovo_helper():\n    return 'aggiornato'\n",
    )

    assert await bridge.run("result = mcp_nuovo_helper()") == "aggiornato"
    assert bridge._helpers_digest != installato


async def test_errore_python_propagato_con_traceback(bridge):
    with pytest.raises(UnrealPythonError) as excinfo:
        await bridge.run("raise ValueError('boom')")
    assert "ValueError: boom" in str(excinfo.value)
    assert "Traceback" in excinfo.value.traceback_text


async def test_editor_non_raggiungibile():
    bridge = UnrealBridge(BridgeConfig(host="127.0.0.1", port=1, timeout=2))
    with pytest.raises(UnrealNotConnected) as excinfo:
        await bridge.info()
    assert "Remote Control API" in str(excinfo.value)
    await bridge.aclose()

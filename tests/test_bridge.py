import pytest

from unreal_mcp.bridge import (
    BridgeConfig,
    UnrealBridge,
    UnrealNotConnected,
    UnrealPythonError,
    build_payload,
    extract_result,
)


def test_build_payload_contiene_helper_e_harness():
    code = build_payload("result = 1 + 1")
    assert "def mcp_project_status" in code   # helper UE anteposti
    assert "def _mcp_main" in code            # harness
    assert "result = 1 + 1" in code


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

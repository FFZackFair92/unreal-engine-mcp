"""Test delle MCP resource e dell'attesa con progress sui job locali."""

import json

import pytest

from unreal_mcp import server as mcp_server


async def _leggi(uri: str) -> dict:
    """Legge una resource attraverso il resource manager di FastMCP."""
    contenuti = await mcp_server.mcp.read_resource(uri)
    testo = next(iter(contenuti)).content
    return json.loads(testo)


async def test_resource_status(tools, unreal):
    dati = await _leggi("unreal://status")
    assert dati["engine_version"].startswith("5.")
    assert "capabilities" in dati


async def test_resource_actors(tools):
    await tools.ue_spawn_actor("StaticMeshActor", label="Cubo")
    dati = await _leggi("unreal://actors")
    assert [a["label"] for a in dati] == ["Cubo"]


async def test_resource_assets(tools, unreal):
    unreal.state["assets"]["/Game/M_Base"] = object()
    unreal.state["asset_classes"]["/Game/M_Base"] = "Material"
    dati = await _leggi("unreal://assets")
    assert {"path": "/Game/M_Base", "class": "Material"} in dati


async def test_resource_log(tools):
    dati = await _leggi("unreal://log")
    assert "lines" in dati


async def test_resource_non_solleva_con_editor_chiuso(monkeypatch):
    """Una resource che solleva sparisce dal client; una che spiega resta utile."""
    async def esplode(_code):
        raise RuntimeError("Nessuna risposta da http://127.0.0.1:30010")

    monkeypatch.setattr(mcp_server, "run", esplode)
    dati = await _leggi("unreal://status")
    assert dati["available"] is False
    assert "30010" in dati["reason"]


# ------------------------------------------------------- attesa con progress


class _CtxFinto:
    def __init__(self):
        self.avanzamenti = []

    async def report_progress(self, progress, total=None, message=None):
        self.avanzamenti.append((progress, total, message))


async def test_attesa_riporta_avanzamento_e_termina(monkeypatch):
    """wait_seconds > 0 polla dentro una chiamata sola invece di farne venti."""
    letture = [
        {"running": True, "elapsed_seconds": 1},
        {"running": True, "elapsed_seconds": 2},
        {"running": False, "elapsed_seconds": 3, "succeeded": True},
    ]

    async def subito(_):
        return None

    monkeypatch.setattr(mcp_server.asyncio, "sleep", subito)
    ctx = _CtxFinto()
    stato = await mcp_server._attendi_job(
        lambda: letture.pop(0) if len(letture) > 1 else letture[0],
        wait_seconds=60,
        ctx=ctx,
        etichetta="compilazione",
    )
    assert stato["running"] is False
    assert stato["succeeded"] is True
    assert ctx.avanzamenti
    assert all(m for _, _, m in ctx.avanzamenti)


async def test_attesa_scaduta_lo_dice(monkeypatch):
    async def subito(_):
        return None

    monkeypatch.setattr(mcp_server.asyncio, "sleep", subito)
    scadenze = iter([0.0, 0.0, 999.0, 999.0, 999.0, 999.0])

    class _Loop:
        def time(self):
            return next(scadenze, 999.0)

    monkeypatch.setattr(mcp_server.asyncio, "get_event_loop", lambda: _Loop())
    stato = await mcp_server._attendi_job(
        lambda: {"running": True, "elapsed_seconds": 5}, 10, None, "packaging"
    )
    assert "Ancora in corso" in stato["note"]


async def test_progress_non_esplode_senza_token():
    """Metà dei client non manda il progressToken: non deve essere un errore."""

    class _CtxRotto:
        async def report_progress(self, *a, **k):
            raise RuntimeError("no progress token")

    await mcp_server._progress(_CtxRotto(), 1, 2, "x")
    await mcp_server._progress(None, 1, 2, "x")


@pytest.mark.parametrize("nome", ["ue_build_status", "ue_package_status"])
async def test_status_senza_attesa_non_polla(tools, nome, monkeypatch):
    chiamato = {"n": 0}

    async def mai(*a, **k):
        chiamato["n"] += 1

    monkeypatch.setattr(mcp_server, "_attendi_job", mai)
    await getattr(tools, nome)()
    assert chiamato["n"] == 0

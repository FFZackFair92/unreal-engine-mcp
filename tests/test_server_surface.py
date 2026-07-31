"""Test della superficie MCP del server.

Le istruzioni e le descrizioni dei tool sono l'unica cosa che il modello legge
prima di decidere cosa chiamare: se si perdono, il server continua a funzionare
e l'agente inizia a usarlo male. Qui si verifica che arrivino davvero.
"""

from unreal_mcp import server as mcp_server


async def test_le_istruzioni_arrivano_al_modello():
    istruzioni = mcp_server.mcp.instructions or ""

    assert "Unreal Engine 5" in istruzioni
    # La via C++, che resta l'unica sui motori precedenti a 5.8.
    assert "ue_cpp_class_create" in istruzioni
    # Fase 11: le istruzioni dicevano "Blueprint node graphs cannot be
    # scripted", ed era diventato falso su 5.8. Un'istruzione obsoleta è
    # peggio di nessuna: l'agente rinuncia a un tool che funziona.
    assert "Blueprint node graphs ARE scriptable" in istruzioni
    assert "capabilities.blueprint_graph_authoring" in istruzioni
    # I limiti veri rimasti, che l'agente deve conoscere prima di provarci.
    assert "UMG widget trees" in istruzioni
    assert "landscape cannot be created from Python" in istruzioni


async def test_il_nome_del_server_e_esplicito():
    """È l'identità con cui il server compare nei client e nei log."""
    assert mcp_server.mcp.name == "unreal-mcp"


async def test_i_tool_sono_registrati_con_le_descrizioni():
    tools = await mcp_server.mcp.list_tools()
    per_nome = {t.name: t for t in tools}

    # Un campione che copre entrambi i livelli, locale ed editor.
    for atteso in (
        "ue_status",
        "ue_project_create",
        "ue_cpp_class_create",
        "ue_reparent_blueprint",
        "ue_create_material",
        "ue_screenshot",
        "ue_spawn_many",
        "ue_set_actor_property",
    ):
        assert atteso in per_nome, "tool mancante: %s" % atteso
        # Senza descrizione il modello non sa quando usarlo.
        assert (per_nome[atteso].description or "").strip()


async def test_ogni_tool_ha_uno_schema_di_input():
    for tool in await mcp_server.mcp.list_tools():
        assert tool.inputSchema is not None, tool.name


async def test_le_resource_sono_registrate():
    """Le resource sono l'unico contesto che il client può aggiornare da sé."""
    resources = await mcp_server.mcp.list_resources()
    uri = {str(r.uri) for r in resources}
    assert {
        "unreal://status",
        "unreal://log",
        "unreal://actors",
        "unreal://assets",
        "unreal://engines",
    } <= uri
    assert all(r.description for r in resources)


async def test_lo_screenshot_non_ha_output_strutturato():
    """Restituisce [Image, dict]: con lo schema attivo FastMCP non lo serializza."""
    tools = await mcp_server.mcp.list_tools()
    screenshot = next(t for t in tools if t.name == "ue_screenshot")
    assert screenshot.outputSchema is None


async def test_attesa_di_apertura_sotto_il_timeout_dei_client():
    """`wait_seconds` alto faceva fallire la chiamata anche a lancio riuscito.

    Molti client MCP interrompono una richiesta dopo 60 secondi: con il vecchio
    default di 240 il client rispondeva "Request timed out" mentre l'editor era
    partito e stava caricando. Il default deve stare sotto quella soglia.
    """
    import inspect

    firma = inspect.signature(mcp_server.ue_editor_open)
    default = firma.parameters["wait_seconds"].default
    assert default < 60, (
        "wait_seconds=%s supera il timeout tipico di un client MCP" % default
    )

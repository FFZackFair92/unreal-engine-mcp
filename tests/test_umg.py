"""Test della creazione di Widget Blueprint (Fase 2 della roadmap di parità con ue-mcp).

Il `WidgetTree` (layout dei widget) non è scriptabile: verificato dal vivo su
un editor 5.8, `get_editor_property("WidgetTree")` sulla CDO risponde
"protected and cannot be read" — stesso muro dei grafi Blueprint. Questo tool
copre solo la creazione dell'asset, che invece funziona davvero (vedi
docs/PARITY_ROADMAP.md).
"""


async def test_crea_widget_blueprint(tools):
    esito = await tools.ue_create_widget_blueprint("/Game/UI", "WBP_MainMenu")

    assert esito["created"] is True
    assert esito["path"] == "/Game/UI/WBP_MainMenu"
    assert esito["parent_class"] == "UserWidget"
    assert esito["editor_utility"] is False


async def test_crea_editor_utility_widget(tools):
    esito = await tools.ue_create_widget_blueprint(
        "/Game/EditorTools", "EUW_LevelHelper", editor_utility=True
    )

    assert esito["created"] is True
    assert esito["editor_utility"] is True


async def test_widget_blueprint_gia_esistente_non_lo_ricrea(tools):
    await tools.ue_create_widget_blueprint("/Game/UI", "WBP_HUD")
    esito = await tools.ue_create_widget_blueprint("/Game/UI", "WBP_HUD")

    assert esito["created"] is False
    assert esito["reason"] == "esiste già"


async def test_parent_class_sconosciuta_fallisce_con_messaggio_utile(tools):
    try:
        await tools.ue_create_widget_blueprint(
            "/Game/UI", "WBP_Bad", parent_class="NomeCheNonEsiste123"
        )
    except RuntimeError as exc:
        assert "NomeCheNonEsiste123" in str(exc)
    else:
        raise AssertionError("doveva fallire")

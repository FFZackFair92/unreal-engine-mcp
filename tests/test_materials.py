"""Test dei materiali e dello screenshot.

Il grafo *materiale* è, a differenza di quello Blueprint, pienamente
scriptabile: questi test verificano che i nodi vengano davvero creati e
collegati ai canali giusti.
"""

import fake_unreal


async def _importa_texture(tools, nomi):
    """Porta delle texture nel Content Browser finto."""
    return await tools.ue_import_assets(
        ["C:/tmp/%s" % nome for nome in nomi], destination="/Game/Textures"
    )


async def test_materiale_collega_le_texture_ai_canali(tools, unreal):
    await _importa_texture(tools, ["Brick_Color.png", "Brick_NormalGL.png"])

    esito = await tools.ue_create_material(
        "/Game/Materials",
        "M_Brick",
        textures={
            "base_color": "/Game/Textures/Brick_Color",
            "normal": "/Game/Textures/Brick_NormalGL",
        },
    )

    assert esito["created"] is True
    assert esito["path"] == "/Game/Materials/M_Brick"
    assert set(esito["connected"]) == {"base_color", "normal"}
    assert "MP_BASE_COLOR" in unreal.state["material_links"]
    assert "MP_NORMAL" in unreal.state["material_links"]


async def test_canale_dedotto_dal_nome_file(tools):
    """Gli asset CC0 di ambientCG/Poly Haven hanno suffissi standard:
    sfruttarli evita di mappare i canali a mano."""
    await _importa_texture(
        tools, ["Wood_Roughness.png", "Wood_BaseColor.png", "Wood_NormalGL.png"]
    )

    esito = await tools.ue_create_material(
        "/Game/Materials",
        "M_Wood",
        textures={
            "auto": "/Game/Textures/Wood_Roughness",
        },
    )
    assert "roughness" in esito["connected"]


async def test_texture_mancante_non_fa_fallire_il_materiale(tools):
    esito = await tools.ue_create_material(
        "/Game/Materials", "M_Parziale", textures={"base_color": "/Game/Textures/Assente"}
    )
    assert esito["created"] is True
    assert esito["skipped"][0]["reason"] == "asset non trovato"


async def test_scalari_sui_canali_senza_texture(tools, unreal):
    esito = await tools.ue_create_material(
        "/Game/Materials", "M_Liscio", scalars={"roughness": 0.15, "metallic": 1.0}
    )
    assert esito["connected"] == {"roughness": 0.15, "metallic": 1.0}
    assert "MP_ROUGHNESS" in unreal.state["material_links"]


async def test_canale_sconosciuto_e_un_errore_esplicito(tools):
    try:
        await tools.ue_create_material(
            "/Game/Materials", "M_Boh", scalars={"lucentezza": 1.0}
        )
    except RuntimeError as exc:
        assert "lucentezza" in str(exc)
        assert "base_color" in str(exc)   # elenca i canali validi
    else:
        raise AssertionError("ci si aspettava un errore sul canale sconosciuto")


async def test_material_instance_con_parametri(tools):
    await tools.ue_create_material("/Game/Materials", "M_Base")

    esito = await tools.ue_create_material_instance(
        "/Game/Materials",
        "MI_Rosso",
        "/Game/Materials/M_Base",
        parameters={"roughness": 0.3, "base_color": {"r": 1, "g": 0, "b": 0}},
    )
    assert esito["created"] is True
    assert esito["parent"] == "/Game/Materials/M_Base"
    assert set(esito["parameters"]) == {"roughness", "base_color"}


async def test_materiale_assegnato_a_un_attore(tools):
    await tools.ue_create_material("/Game/Materials", "M_Base")
    await tools.ue_spawn_actor("StaticMeshActor", label="Muro")

    esito = await tools.ue_assign_material("Muro", "/Game/Materials/M_Base")
    assert esito["actor"] == "Muro"
    assert esito["slot"] == 0
    assert "StaticMeshComponent" in esito["component"]


async def test_screenshot_scrive_il_file(tools, unreal):
    esito = await tools.ue_screenshot("prova.png", width=640, height=360)
    assert esito["captured"] is True
    assert esito["file"].endswith("prova.png")
    assert unreal.state["screenshots"]


async def test_screenshot_aggiunge_estensione(tools):
    esito = await tools.ue_screenshot("senza-estensione")
    assert esito["file"].endswith(".png")


async def test_modifiche_agli_attori_sono_annullabili(tools):
    """Senza transazione, quello che fa l'agente non si annulla con Ctrl+Z."""
    fake_unreal.ScopedEditorTransaction.opened.clear()

    await tools.ue_spawn_actor("StaticMeshActor", label="Cassa")
    await tools.ue_set_actor_transform("Cassa", location=[100, 0, 0])
    await tools.ue_delete_actor("Cassa")

    aperte = fake_unreal.ScopedEditorTransaction.opened
    assert len(aperte) == 3
    assert all(d.startswith("MCP:") for d in aperte)

"""Fase 15: attori e livelli, parità con ChiR24/Unreal_mcp."""

import pytest


async def test_tag_aggiunti_tolti_e_ritrovati(tools):
    await tools.ue_spawn_actor("PointLight", label="Luce")
    await tools.ue_spawn_actor("PointLight", label="Altra")

    esito = await tools.ue_actor_tags("Luce", add=["esterni", "notte"])
    assert esito["tags"] == ["esterni", "notte"]
    esito = await tools.ue_actor_tags("Luce", add=["notte"], remove=["esterni"])
    assert esito["tags"] == ["notte"]          # niente doppioni

    trovati = await tools.ue_list_actors(tag="notte")
    assert [a["label"] for a in trovati] == ["Luce"]
    assert await tools.ue_list_actors(tag="notte", class_contains="Mesh") == []


async def test_duplicate_actor_con_offset_e_label(tools, unreal):
    await tools.ue_spawn_actor("StaticMeshActor", label="Cassa", location=[100, 0, 0])
    copia = await tools.ue_duplicate_actor("Cassa", new_label="Cassa2", offset=[0, 200, 0])
    assert copia["label"] == "Cassa2"
    assert copia["location"] == {"x": 100.0, "y": 200.0, "z": 0.0}
    assert len(unreal.state["actors"]) == 2


async def test_visibilita_cartella_selezione(tools):
    await tools.ue_spawn_actor("PointLight", label="A")
    await tools.ue_spawn_actor("PointLight", label="B")

    v = await tools.ue_set_actor_visibility("A", hidden_in_game=True, hidden_in_editor=True)
    assert v["hidden_in_game"] is True and v["hidden_in_editor"] is True

    f = await tools.ue_set_actor_folder(["A", "B"], "Luci/Esterni")
    assert f["actors"] == ["A", "B"]

    sel = await tools.ue_select_actors(["A"])
    assert [a["label"] for a in sel["selected"]] == ["A"]
    sel = await tools.ue_select_actors(["B"], add=True)
    assert [a["label"] for a in sel["selected"]] == ["A", "B"]
    sel = await tools.ue_select_actors()
    assert len(sel["selected"]) == 2


async def test_raycast_colpisce_il_pavimento(tools):
    colpo = await tools.ue_raycast([10, 20, 500])          # verso il basso di default
    assert colpo["hit"] is True
    assert colpo["location"] == {"x": 10.0, "y": 20.0, "z": 0.0}

    mancato = await tools.ue_raycast([0, 0, 500], end=[0, 0, 100])
    assert mancato["hit"] is False


async def test_snap_to_floor_anche_se_sprofondato(tools, unreal):
    await tools.ue_spawn_actor("StaticMeshActor", label="Sospeso", location=[0, 0, 300])
    await tools.ue_spawn_actor("StaticMeshActor", label="Sotto", location=[0, 0, -40])

    esito = await tools.ue_snap_to_floor(["Sospeso", "Sotto"], z_offset=50)
    assert {e["actor"]: e["to_z"] for e in esito["snapped"]} == {"Sospeso": 50.0, "Sotto": 50.0}
    assert esito["no_floor"] == []


async def test_get_actor_property_isola_gli_errori(tools):
    await tools.ue_spawn_actor("StaticMeshActor", label="Cubo")
    await tools.ue_set_actor_property(
        "Cubo", {"mobility": "Movable"}, component="StaticMeshComponent"
    )

    esito = await tools.ue_get_actor_property(
        "Cubo", ["mobility", "non_esiste"], component="StaticMeshComponent"
    )
    assert esito["values"]["mobility"] == "Movable"


async def test_call_actor_function(tools):
    await tools.ue_spawn_actor("PointLight", label="Generatore")
    esito = await tools.ue_call_actor_function(
        "Generatore", "Rigenera", [3, {"x": 1, "y": 2, "z": 3}]
    )
    assert esito["result"] == "ok:Rigenera"
    with pytest.raises(RuntimeError, match="Failed to find function"):
        await tools.ue_call_actor_function("Generatore", "InesistenteX")


async def test_sublevels_add_list_remove(tools):
    lista = await tools.ue_sublevels()
    assert lista["levels"] == ["L_Test"]

    esito = await tools.ue_sublevels("add", "/Game/Maps/L_Interni")
    assert esito["streaming"] == "LevelStreamingDynamic"
    assert "/Game/Maps/L_Interni" in esito["levels"]

    with pytest.raises(RuntimeError):
        await tools.ue_sublevels("add", "/Game/Maps/L_Interni")    # già presente

    esito = await tools.ue_sublevels("remove", "/Game/Maps/L_Interni")
    assert esito["levels"] == ["L_Test"]
    with pytest.raises(ValueError):
        await tools.ue_sublevels("add")


async def test_build_lighting_cvar_open_asset_undo(tools, unreal):
    esito = await tools.ue_build_lighting("high")
    assert esito["built"] is True
    assert unreal.state["light_builds"] == [("High", True)]

    cv = await tools.ue_cvar("r.ScreenPercentage", 75)
    assert cv["value"]["float"] == 75.0 and cv["value"]["int"] == 75

    await tools.ue_import_assets(["C:/tmp/Cubo.fbx"], destination="/Game/Meshes")
    aperto = await tools.ue_open_asset("/Game/Meshes/Cubo")
    assert aperto["opened"] == "/Game/Meshes/Cubo"

    u = await tools.ue_undo(2)
    assert u["steps"] == 2
    assert unreal.state["console"][-2:] == ["TRANSACTION UNDO", "TRANSACTION UNDO"]

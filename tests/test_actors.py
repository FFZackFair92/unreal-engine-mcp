"""Test dei setter sugli attori piazzati e dello spawn in blocco."""

import pytest


async def test_spawn_many_in_una_sola_chiamata(tools, unreal):
    """Costruire una scena un attore per volta costa un round-trip ciascuno."""
    await tools.ue_status()          # scalda il bridge: gli helper si installano qui
    prima = len(unreal.calls)

    esito = await tools.ue_spawn_many(
        [
            {"class_ref": "PointLight", "location": [0, 0, 300], "label": "Luce1"},
            {"class_ref": "PointLight", "location": [500, 0, 300], "label": "Luce2"},
            {"class_ref": "StaticMeshActor", "label": "Pavimento"},
        ]
    )

    assert esito["spawned"] == 3
    assert esito["failed"] == []
    assert len(unreal.calls) - prima == 1     # una sola richiesta HTTP
    assert len(unreal.state["actors"]) == 3


async def test_spawn_many_isola_i_fallimenti(tools):
    """Un elemento sbagliato non deve far perdere gli altri."""
    esito = await tools.ue_spawn_many(
        [
            {"class_ref": "PointLight", "label": "Buona"},
            {"location": [0, 0, 0]},                       # manca class_ref
            {"class_ref": "ClasseInesistente"},
            {"class_ref": "StaticMeshActor", "label": "Altra"},
        ]
    )

    assert esito["spawned"] == 2
    assert len(esito["failed"]) == 2
    assert esito["failed"][0]["index"] == 1
    assert "class_ref" in esito["failed"][0]["error"]
    assert esito["failed"][1]["index"] == 2


async def test_set_actor_property_su_componente(tools):
    await tools.ue_spawn_actor("StaticMeshActor", label="Cassa")

    esito = await tools.ue_set_actor_property(
        "Cassa", {"mobility": "Movable"}, component="StaticMeshComponent"
    )
    assert esito["applied"] == {"mobility": "Movable"}
    assert esito["failed"] == {}


async def test_i_path_degli_asset_vengono_caricati(tools):
    """Il ponte trasporta solo JSON: un asset arriva come stringa e va risolto."""
    await tools.ue_import_assets(["C:/tmp/Cubo.fbx"], destination="/Game/Meshes")
    await tools.ue_spawn_actor("StaticMeshActor", label="Cubo")

    esito = await tools.ue_set_actor_property(
        "Cubo", {"static_mesh": "/Game/Meshes/Cubo"}, component="StaticMeshComponent"
    )
    assert "static_mesh" in esito["applied"]


async def test_vettori_e_colori_dai_dict(tools):
    await tools.ue_spawn_actor("PointLight", label="Luce")
    esito = await tools.ue_set_actor_property(
        "Luce",
        {"relative_location": {"x": 1, "y": 2, "z": 3}, "light_color": {"r": 1, "g": 0, "b": 0}},
    )
    assert set(esito["applied"]) == {"relative_location", "light_color"}


async def test_attore_inesistente_elenca_quelli_presenti(tools):
    await tools.ue_spawn_actor("StaticMeshActor", label="Esiste")

    with pytest.raises(RuntimeError) as excinfo:
        await tools.ue_set_actor_property("NonEsiste", {"x": 1})

    messaggio = str(excinfo.value)
    assert "NonEsiste" in messaggio
    assert "Esiste" in messaggio      # aiuta a capire cosa scrivere


async def test_elenco_componenti(tools):
    await tools.ue_spawn_actor("StaticMeshActor", label="Cassa")
    esito = await tools.ue_list_actor_components("Cassa")
    assert esito["actor"] == "Cassa"
    assert any(c["class"] == "StaticMeshComponent" for c in esito["components"])


async def test_componente_inesistente_elenca_i_disponibili(tools):
    await tools.ue_spawn_actor("StaticMeshActor", label="Cassa")
    with pytest.raises(RuntimeError) as excinfo:
        await tools.ue_set_actor_property("Cassa", {"x": 1}, component="Inventato")
    assert "StaticMeshComponent" in str(excinfo.value)

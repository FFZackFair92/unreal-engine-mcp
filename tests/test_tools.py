"""Test dei tool MCP contro l'Unreal finto: verificano che ogni snippet generato
sia eseguibile e produca lo stato atteso nell'editor."""

import pytest


# ------------------------------------------------------------------ core


async def test_status(tools):
    status = await tools.ue_status()
    assert status["python_ok"] is True
    assert "fake" in status["engine_version"]
    assert status["actor_count"] == 0
    # Capacità rilevate a runtime: il fake espone l'API completa (UE 5.4+).
    assert status["capabilities"]["blueprint_variables"] is True
    assert status["capabilities"]["blueprint_components"] is True


async def test_read_log(tools):
    log = await tools.ue_read_log(lines=10)
    assert any("avvio" in line for line in log["lines"])

    errors = await tools.ue_read_log(lines=10, only_errors=True)
    assert all("Error" in line or "Warning" in line for line in errors["lines"])


async def test_exec_python(tools):
    assert await tools.ue_exec_python("result = 2 ** 8") == 256


async def test_live_compile(tools, unreal):
    """Live Coding permette di ricompilare a editor aperto: e' la via veloce
    per iterare, senza chiudere e riaprire."""
    esito = await tools.ue_live_compile(max_wait_seconds=3)
    assert "LiveCoding.Compile" in unreal.state["console"]
    assert esito["riuscito"] is True
    assert esito["fallito"] is False
    # Il limite va detto: le modifiche alla reflection non passano da qui.
    assert "UFUNCTION" in esito["nota"]


async def test_save_all(tools, unreal):
    assert (await tools.ue_save_all())["saved"] is True
    assert "/Game" in unreal.state["saved"]


# ---------------------------------------------------------------- asset


async def test_import_assets(tools, unreal):
    report = await tools.ue_import_assets(
        ["C:/tmp/four_corners_export/Arena_Tetto.glb", "C:/tmp/four_corners_export/Characters.glb"],
        destination="/Game/MyGame/Levels",
    )
    assert [r["count"] for r in report] == [1, 1]
    assert "/Game/MyGame/Levels/Arena_Tetto" in unreal.state["imports"]


async def test_list_assets_con_filtro(tools):
    await tools.ue_import_assets(["/tmp/mesh.glb"], destination="/Game/X")
    await tools.ue_import_audio(["/tmp/hit.wav"], destination="/Game/X")

    tutti = await tools.ue_list_assets("/Game/X")
    solo_audio = await tools.ue_list_assets("/Game/X", class_filter="SoundWave")

    assert len(tutti) == 2
    assert [a["path"] for a in solo_audio] == ["/Game/X/hit"]


async def test_livelli(tools, unreal):
    creato = await tools.ue_new_level("/Game/MyGame/Levels/L_Cortile")
    assert creato["created"] is True
    aperto = await tools.ue_open_level("/Game/MyGame/Levels/L_Cortile")
    assert aperto["opened"] is True
    assert unreal.state["current_level"] == "/Game/MyGame/Levels/L_Cortile"


# ---------------------------------------------------------------- attori


async def test_spawn_e_lista_attori(tools):
    info = await tools.ue_spawn_actor(
        "PlayerStart", location=[100, 200, 50], rotation=[0, 90, 0], label="Start_Hunter"
    )
    assert info["label"] == "Start_Hunter"
    assert info["location"] == {"x": 100.0, "y": 200.0, "z": 50.0}
    assert info["rotation"]["yaw"] == 90.0

    attori = await tools.ue_list_actors(name_contains="Hunter")
    assert len(attori) == 1


async def test_transform_e_delete(tools):
    await tools.ue_spawn_actor("StaticMeshActor", label="Muro")
    aggiornato = await tools.ue_set_actor_transform(
        "Muro", location=[10, 20, 30], scale=[2, 2, 2]
    )
    assert aggiornato["location"] == {"x": 10.0, "y": 20.0, "z": 30.0}

    assert (await tools.ue_delete_actor("Muro"))["deleted"] == "Muro"
    assert await tools.ue_list_actors() == []


async def test_errore_attore_inesistente(tools):
    with pytest.raises(RuntimeError) as excinfo:
        await tools.ue_set_actor_transform("NonEsiste", location=[0, 0, 0])
    assert "NonEsiste" in str(excinfo.value)


# ------------------------------------------------------------- blueprint


async def test_crea_blueprint_e_idempotenza(tools):
    primo = await tools.ue_create_blueprint("/Game/MyGame/Blueprints", "BP_CornerSlot")
    secondo = await tools.ue_create_blueprint("/Game/MyGame/Blueprints", "BP_CornerSlot")
    assert primo["created"] is True
    assert secondo["created"] is False


async def test_componente_e_variabili(tools, unreal):
    path = "/Game/MyGame/Blueprints/BP_CornerSlot"
    await tools.ue_create_blueprint("/Game/MyGame/Blueprints", "BP_CornerSlot")

    componente = await tools.ue_add_component(path, "BoxComponent", "TriggerArea")
    assert componente["name"] == "TriggerArea"

    variabile = await tools.ue_add_variable(
        path, "OccupyingPlayer", "object", "Actor", replicated=True, instance_editable=False
    )
    assert variabile["created"] is True and variabile["replicated"] is True

    descrizioni = unreal.state["assets"][path].get_editor_property("_variables")
    assert list(descrizioni) == ["OccupyingPlayer"]
    assert descrizioni["OccupyingPlayer"]["replication"] == "REPLICATED"
    assert descrizioni["OccupyingPlayer"]["editable"] is False

    # seconda aggiunta con lo stesso nome: nessun duplicato
    assert (await tools.ue_add_variable(path, "OccupyingPlayer", "object", "Actor"))["created"] is False


@pytest.mark.parametrize(
    "var_type,sub_type",
    [("bool", None), ("int", None), ("float", None), ("string", None), ("struct", "Vector")],
)
async def test_tipi_variabile_supportati(tools, var_type, sub_type):
    await tools.ue_create_blueprint("/Game/T", "BP_Types")
    esito = await tools.ue_add_variable("/Game/T/BP_Types", "V_" + var_type, var_type, sub_type)
    assert esito["created"] is True


async def test_variabile_su_motore_vecchio(tools):
    """Su UE < 5.4 manca l'API delle variabili: l'errore deve dirlo chiaramente."""
    await tools.ue_create_blueprint("/Game/T", "BP_Old")
    await tools.ue_exec_python(
        "delattr(unreal.BlueprintEditorLibrary, 'add_member_variable')\nresult = True"
    )
    with pytest.raises(RuntimeError) as excinfo:
        await tools.ue_add_variable("/Game/T/BP_Old", "V", "int")
    assert "5.4" in str(excinfo.value)


async def test_tipo_variabile_non_supportato(tools):
    await tools.ue_create_blueprint("/Game/T", "BP_Types")
    with pytest.raises(RuntimeError) as excinfo:
        await tools.ue_add_variable("/Game/T/BP_Types", "V", "quaternione")
    assert "non supportato" in str(excinfo.value)


async def test_compile_e_class_defaults(tools):
    await tools.ue_create_blueprint("/Game/T", "BP_X")
    assert (await tools.ue_compile_blueprint("/Game/T/BP_X"))["compiled"] is True
    esito = await tools.ue_set_class_defaults("/Game/T/BP_X", {"NetUpdateFrequency": 30})
    assert esito["applied"] == {"NetUpdateFrequency": 30}


# ------------------------------------------------------------ networking


async def test_replication(tools):
    await tools.ue_create_blueprint("/Game/T", "BP_Player", "Character")
    esito = await tools.ue_set_replication("/Game/T/BP_Player", True, True, False)
    assert esito["applied"]["replicates"] is True


async def test_pie_multi_client(tools, unreal):
    config = await tools.ue_configure_pie(num_players=5, net_mode="listen_server")
    assert config["num_players"] == 5
    assert config["net_mode"] == "PIE_ListenServer"

    await tools.ue_start_pie()
    await tools.ue_stop_pie()
    assert unreal.state["pie"] == ["start", "stop"]


async def test_pie_net_mode_invalido(tools):
    with pytest.raises(RuntimeError) as excinfo:
        await tools.ue_configure_pie(net_mode="peer_to_peer")
    assert "net_mode" in str(excinfo.value)


async def test_project_setting_scrive_ini(tools, tmp_path):
    esito = await tools.ue_set_project_setting(
        "/Script/EngineSettings.GameMapsSettings",
        "GlobalDefaultGameMode",
        "/Game/MyGame/Blueprints/BP_MyGameMode.BP_MyGameMode_C",
    )
    contenuto = open(esito["file"], encoding="utf-8").read()
    assert "[/Script/EngineSettings.GameMapsSettings]" in contenuto
    assert "GlobalDefaultGameMode=/Game/MyGame" in contenuto

    # riscrittura della stessa chiave: nessun duplicato
    await tools.ue_set_project_setting(
        "/Script/EngineSettings.GameMapsSettings", "GlobalDefaultGameMode", "/Game/Altro"
    )
    contenuto = open(esito["file"], encoding="utf-8").read()
    assert contenuto.count("GlobalDefaultGameMode=") == 1
    assert "/Game/Altro" in contenuto


# ----------------------------------------------------------------- audio


async def test_sound_cue_da_wave(tools):
    await tools.ue_import_audio(["/tmp/corner_captured.wav"])
    esito = await tools.ue_create_sound_cue(
        "/Game/Audio", "SC_CornerCaptured",
        "/Game/Audio/corner_captured",
    )
    assert esito["created"] is True


async def test_sound_cue_wave_mancante(tools):
    with pytest.raises(RuntimeError) as excinfo:
        await tools.ue_create_sound_cue("/Game/A", "SC_X", "/Game/A/inesistente")
    assert "non trovato" in str(excinfo.value)


async def test_metasound_source(tools):
    esito = await tools.ue_create_metasound_source("/Game/MyGame/Audio", "MS_Steps")
    assert esito["created"] is True

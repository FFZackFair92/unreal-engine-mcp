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
    assert esito["succeeded"] is True
    assert esito["failed"] is False
    # Il limite va detto: le modifiche alla reflection non passano da qui.
    assert "UFUNCTION" in esito["note"]


async def test_save_all(tools, unreal):
    assert (await tools.ue_save_all())["saved"] is True
    assert "/Game" in unreal.state["saved"]


# ---------------------------------------------------------------- asset


async def test_import_assets(tools, unreal):
    report = await tools.ue_import_assets(
        ["C:/Assets/Props/crate.glb", "C:/Assets/Props/barrel.glb"],
        destination="/Game/MyGame/Props",
    )
    assert [r["count"] for r in report] == [1, 1]
    assert "/Game/MyGame/Props/crate" in unreal.state["imports"]


async def test_list_assets_con_filtro(tools):
    await tools.ue_import_assets(["/tmp/mesh.glb"], destination="/Game/X")
    await tools.ue_import_audio(["/tmp/hit.wav"], destination="/Game/X")

    tutti = await tools.ue_list_assets("/Game/X")
    solo_audio = await tools.ue_list_assets("/Game/X", class_filter="SoundWave")

    assert len(tutti) == 2
    assert [a["path"] for a in solo_audio] == ["/Game/X/hit"]


async def test_livelli(tools, unreal):
    creato = await tools.ue_new_level("/Game/MyGame/Levels/L_Main")
    assert creato["created"] is True
    aperto = await tools.ue_open_level("/Game/MyGame/Levels/L_Main")
    assert aperto["opened"] is True
    assert unreal.state["current_level"] == "/Game/MyGame/Levels/L_Main"


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
    assert unreal.state["pie"] == ["play", "stop"]


async def test_pie_modo_predefinito_e_play_non_simulate(tools, unreal):
    """Regressione: fino alla 0.x ue_start_pie faceva Simulate, non Play."""
    esito = await tools.ue_start_pie()
    assert esito["mode"] == "play"
    assert esito["api"] == "editor_request_begin_play"
    assert unreal.state["pie"] == ["play"]


async def test_pie_modo_simulate(tools, unreal):
    esito = await tools.ue_start_pie(mode="simulate")
    assert esito["mode"] == "simulate"
    assert esito["api"] == "editor_play_simulate"
    assert unreal.state["pie"] == ["simulate"]


async def test_pie_modo_invalido(tools):
    with pytest.raises(RuntimeError) as excinfo:
        await tools.ue_start_pie(mode="record")
    assert "mode" in str(excinfo.value)


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


# ------------------------------------------------------ comandi di console


async def test_console_command_restituisce_le_righe_nuove(tools, unreal):
    """Un comando di console non torna un valore: scrive nel log.

    Senza rileggere il log, la risposta sarebbe "fatto" e nient'altro — inutile
    per l'agente, che non saprebbe se il comando ha avuto effetto.
    """
    esito = await tools.ue_console_command("stat fps", wait_seconds=0)
    assert esito["command"] == "stat fps"
    assert any("stat fps" in riga for riga in esito["log_lines"])
    assert "stat fps" in unreal.state["console"]


async def test_console_command_senza_output_lo_dice(tools, unreal, monkeypatch):
    """Molti comandi non stampano nulla: va detto, non lasciato ambiguo."""
    def _muto(world, cmd):
        unreal.state.setdefault("console", []).append(cmd)

    monkeypatch.setattr(unreal.unreal.SystemLibrary, "execute_console_command", _muto)
    esito = await tools.ue_console_command("r.ScreenPercentage 50", wait_seconds=0)
    assert esito["log_lines"] == []
    assert "non stampano nulla" in esito["note"]


# ------------------------------- il default della variabile arriva come JSON


@pytest.mark.parametrize(
    "tipo,inviato,atteso",
    [
        ("float", "100", 100.0),      # il client serializza il numero come stringa
        ("float", 100, 100.0),
        ("float", 2.5, 2.5),
        ("int", "7", 7),
        ("int", 7.9, 7),
        ("bool", "true", True),
        ("bool", "false", False),
        ("bool", 1, True),
        ("string", 42, "42"),
    ],
)
async def test_default_convertito_al_tipo_della_variabile(tools, unreal, tipo, inviato, atteso):
    """Su Unreal vero un default arrivato come stringa fa esplodere il set.

        TypeError: Cannot nativize 'str' as 'double'

    Lo stesso 100 può arrivare come numero o come "100" a seconda di come il
    client serializza un parametro dallo schema aperto: la conversione va fatta
    qui, non sperata a monte.
    """
    await tools.ue_create_blueprint("/Game/T", "BP_Var_%s_%s" % (tipo, abs(hash(str(inviato)))))
    nome = list(unreal.state["assets"])[-1]
    esito = await tools.ue_add_variable(nome, "V", var_type=tipo, default_value=inviato)
    assert esito["default"] == atteso
    assert type(esito["default"]) is type(atteso)


async def test_default_non_convertibile_lo_dice(tools, unreal):
    await tools.ue_create_blueprint("/Game/T", "BP_VarRotta")
    nome = list(unreal.state["assets"])[-1]
    with pytest.raises(RuntimeError, match="non è convertibile"):
        await tools.ue_add_variable(nome, "V", var_type="float", default_value="parecchio")

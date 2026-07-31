"""Test della fase 14b (sequencer authoring) della roadmap di parità.

Fino alla 0.9.0 il sequencer c'era solo in uscita: `ue_render_sequence`
renderizza una sequenza già fatta. Questi tool la costruiscono.

Nessun muro, contro le aspettative maturate su UMG e Niagara:
`MovieSceneSequenceExtensions`, `MovieSceneBindingExtensions`,
`MovieSceneTrackExtensions` e `MovieSceneSectionExtensions` sono esposte per
intero. Verificato dal vivo su UE 5.8 il 2026-07-31 costruendo una sequenza a
30 fps con un attore possessato, una track di trasformata con due chiavi su
Rotation.Y e una di visibilità, salvando e ritrovando nel `.uasset` i nomi
`MovieScene3DTransformTrack`, `MovieSceneVisibilityTrack` e il binding.

**Le due trappole della fase**, entrambe riprodotte dal finto perché sono il
genere di cosa che un test con nomi hard-coded non vedrebbe mai:

1. I nomi dei canali hanno un suffisso numerico *instabile*: la stessa sezione
   ha dato `Location.Z_0` alla prima creazione e `Location.Z_3` alla seconda,
   nella stessa sessione di editor.
2. I nomi visualizzati di track e binding sono localizzati (su editor italiano
   la track di trasformata si chiama "Trasforma") — per questo le track si
   indirizzano per tipo o per indice.
"""

import fake_unreal
import pytest


@pytest.fixture(autouse=True)
def attori(unreal):
    unreal.state["actors"].append(
        fake_unreal.Actor(class_name="DirectionalLight", label="Sole")
    )
    unreal.state["actors"].append(
        fake_unreal.Actor(class_name="StaticMeshActor", label="Cubo")
    )


@pytest.fixture
async def sequenza(tools):
    await tools.ue_create_level_sequence("/Game/Cinematics", "LS_Intro", fps=30, length_frames=120)
    return "/Game/Cinematics/LS_Intro"


@pytest.fixture
async def con_track(tools, sequenza):
    await tools.ue_sequence_add_actor(sequenza, "Sole")
    await tools.ue_sequence_add_track(sequenza, "Sole", "transform", 0, 120)
    return sequenza


# ------------------------------------------------------------------- creazione


async def test_create_level_sequence_crea_l_asset(tools):
    esito = await tools.ue_create_level_sequence("/Game/Cinematics", "LS_Intro")

    assert esito["path"] == "/Game/Cinematics/LS_Intro"
    assert esito["created"] is True


async def test_create_level_sequence_imposta_fps_e_durata(tools):
    esito = await tools.ue_create_level_sequence(
        "/Game/Cinematics", "LS_Intro", fps=24, length_frames=240
    )

    assert esito["fps"] == 24.0
    assert esito["playback"] == [0, 240]


async def test_create_level_sequence_non_sovrascrive(tools, sequenza):
    esito = await tools.ue_create_level_sequence("/Game/Cinematics", "LS_Intro")

    assert esito["created"] is False


async def test_sequence_info_rifiuta_un_asset_che_non_e_una_sequenza(tools, unreal):
    unreal.state["assets"]["/Game/Cinematics/NonUnaSeq"] = fake_unreal.StaticMesh()

    with pytest.raises(Exception, match="non una LevelSequence"):
        await tools.ue_sequence_info("/Game/Cinematics/NonUnaSeq")


# --------------------------------------------------------------------- binding


async def test_add_actor_crea_il_binding(tools, sequenza):
    esito = await tools.ue_sequence_add_actor(sequenza, "Sole")

    assert esito["binding"] == "Sole"
    assert esito["class"] == "DirectionalLight"
    assert esito["spawnable"] is False


async def test_add_actor_spawnable(tools, sequenza):
    esito = await tools.ue_sequence_add_actor(sequenza, "Cubo", spawnable=True)

    assert esito["spawnable"] is True


async def test_add_actor_richiede_un_attore_del_livello(tools, sequenza):
    with pytest.raises(Exception, match="Nessun attore con label"):
        await tools.ue_sequence_add_actor(sequenza, "NonEsiste")


async def test_binding_indirizzabile_anche_per_indice(tools, sequenza):
    await tools.ue_sequence_add_actor(sequenza, "Sole")
    await tools.ue_sequence_add_actor(sequenza, "Cubo")

    esito = await tools.ue_sequence_add_track(sequenza, "1", "transform")

    assert esito["binding"] == "Cubo"


async def test_binding_inesistente_elenca_quelli_che_ci_sono(tools, sequenza):
    await tools.ue_sequence_add_actor(sequenza, "Sole")

    with pytest.raises(Exception, match="Sole"):
        await tools.ue_sequence_add_track(sequenza, "Luna", "transform")


# ----------------------------------------------------------------------- track


async def test_add_track_crea_anche_la_sezione(tools, sequenza):
    await tools.ue_sequence_add_actor(sequenza, "Sole")

    esito = await tools.ue_sequence_add_track(sequenza, "Sole", "transform", 0, 90)

    assert esito["track"] == "MovieScene3DTransformTrack"
    assert esito["range"] == [0, 90]


async def test_add_track_senza_range_usa_il_playback(tools, sequenza):
    await tools.ue_sequence_add_actor(sequenza, "Sole")

    esito = await tools.ue_sequence_add_track(sequenza, "Sole", "transform")

    assert esito["range"] == [0, 120]


async def test_add_track_elenca_i_canali_senza_suffisso(tools, con_track):
    """Il punto della fase: i canali si presentano con il nome stabile, non con
    `Location.Z_3`."""
    info = await tools.ue_sequence_info(con_track)

    canali = [c["name"] for c in info["bindings"][0]["tracks"][0]["sections"][0]["channels"]]
    assert canali[:3] == ["Location.X", "Location.Y", "Location.Z"]


async def test_alias_di_track_riconosciuti(tools, sequenza):
    await tools.ue_sequence_add_actor(sequenza, "Sole")

    visibilita = await tools.ue_sequence_add_track(sequenza, "Sole", "visibility")

    assert visibilita["track"] == "MovieSceneVisibilityTrack"


async def test_track_accetta_anche_il_nome_esatto_della_classe(tools, sequenza):
    await tools.ue_sequence_add_actor(sequenza, "Sole")

    esito = await tools.ue_sequence_add_track(sequenza, "Sole", "MovieSceneFloatTrack")

    assert esito["track"] == "MovieSceneFloatTrack"


async def test_tipo_di_track_sconosciuto_elenca_gli_alias(tools, sequenza):
    await tools.ue_sequence_add_actor(sequenza, "Sole")

    with pytest.raises(Exception, match="transform"):
        await tools.ue_sequence_add_track(sequenza, "Sole", "cosa_e_questo")


async def test_il_nome_visualizzato_e_localizzato_ma_la_classe_no(tools, con_track):
    """La ragione per cui non si indirizza mai una track per nome."""
    info = await tools.ue_sequence_info(con_track)

    track = info["bindings"][0]["tracks"][0]
    assert track["display_name"] == "Trasforma"
    assert track["class"] == "MovieScene3DTransformTrack"


# ---------------------------------------------------------------------- chiavi


async def test_add_key_mette_la_chiave_e_rilegge(tools, con_track):
    esito = await tools.ue_sequence_add_key(
        con_track, "Sole", "Rotation.Y", 0, -20.0, track_type="transform"
    )

    assert esito["channel"] == "Rotation.Y"
    assert esito["keys"] == [[0, -20.0]]


async def test_add_key_accumula_e_ordina(tools, con_track):
    await tools.ue_sequence_add_key(con_track, "Sole", "Rotation.Y", 120, -70.0, track=0)
    esito = await tools.ue_sequence_add_key(con_track, "Sole", "Rotation.Y", 0, -20.0, track=0)

    assert esito["keys"] == [[0, -20.0], [120, -70.0]]


async def test_add_key_indirizza_il_canale_senza_suffisso(tools, con_track):
    """Il suffisso vero cambia da una creazione all'altra: il tool non deve
    dipenderci, e il finto lo fa variare apposta."""
    esito = await tools.ue_sequence_add_key(con_track, "Sole", "Location.Z", 30, 500.0, track=0)

    assert esito["channel"] == "Location.Z"


async def test_add_key_su_un_canale_che_non_esiste_elenca_quelli_veri(tools, con_track):
    with pytest.raises(Exception, match=r"Location\.X"):
        await tools.ue_sequence_add_key(con_track, "Sole", "Colore", 0, 1.0, track=0)


async def test_add_key_accetta_l_interpolazione(tools, con_track):
    esito = await tools.ue_sequence_add_key(
        con_track, "Sole", "Location.Z", 90, 500.0, track=0, interpolation="LINEAR"
    )

    assert esito["keys"] == [[90, 500.0]]


async def test_add_key_rifiuta_un_interpolazione_inventata(tools, con_track):
    with pytest.raises(Exception, match="LINEAR"):
        await tools.ue_sequence_add_key(
            con_track, "Sole", "Location.Z", 0, 1.0, track=0, interpolation="MORBIDA"
        )


async def test_add_key_su_canale_booleano(tools, sequenza):
    await tools.ue_sequence_add_actor(sequenza, "Sole")
    await tools.ue_sequence_add_track(sequenza, "Sole", "visibility")

    esito = await tools.ue_sequence_add_key(
        sequenza, "Sole", "MovieSceneScriptingBoolChannel", 60, False, track_type="visibility"
    )

    assert esito["keys"] == [[60, False]]


async def test_track_ambigua_chiede_di_indicarla(tools, sequenza):
    """Due track sullo stesso binding e nessuna indicazione: meglio un errore
    che dice cosa fare, che scrivere sulla prima a caso."""
    await tools.ue_sequence_add_actor(sequenza, "Sole")
    await tools.ue_sequence_add_track(sequenza, "Sole", "transform")
    await tools.ue_sequence_add_track(sequenza, "Sole", "visibility")

    with pytest.raises(Exception, match="indica quale"):
        await tools.ue_sequence_add_key(sequenza, "Sole", "Location.Z", 0, 1.0)


async def test_sezione_fuori_range(tools, con_track):
    with pytest.raises(Exception, match="fuori range"):
        await tools.ue_sequence_add_key(con_track, "Sole", "Location.Z", 0, 1.0, track=0, section=5)


# ------------------------------------------------------------- range e rimozione


async def test_set_range_cambia_playback_e_fps(tools, sequenza):
    esito = await tools.ue_sequence_set_range(sequenza, 10, 240, fps=24)

    assert esito["playback"] == [10, 240]
    assert esito["fps"] == 24.0


async def test_remove_toglie_la_track(tools, sequenza):
    await tools.ue_sequence_add_actor(sequenza, "Sole")
    await tools.ue_sequence_add_track(sequenza, "Sole", "transform")
    await tools.ue_sequence_add_track(sequenza, "Sole", "visibility")

    esito = await tools.ue_sequence_remove(sequenza, "Sole", track_type="visibility")

    assert esito["removed_track"] == "MovieSceneVisibilityTrack"
    assert esito["tracks_left"] == 1


async def test_remove_senza_track_toglie_il_binding(tools, con_track):
    esito = await tools.ue_sequence_remove(con_track, "Sole")

    assert esito["removed_binding"] == "Sole"
    assert (await tools.ue_sequence_info(con_track))["bindings"] == []


# ------------------------------------------------------------------- finestra


async def test_open_apre_la_sequenza(tools, sequenza, unreal):
    esito = await tools.ue_sequence_open(sequenza)

    assert esito["open"] is True
    assert unreal.state["sequencer_open"][-1].startswith("/Game/Cinematics/LS_Intro")


async def test_open_con_close_chiude(tools, sequenza, unreal):
    esito = await tools.ue_sequence_open(sequenza, close=True)

    assert esito["open"] is False
    assert unreal.state["sequencer_open"][-1] is None

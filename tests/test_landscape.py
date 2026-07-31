"""Test della Fase 9 (landscape) della roadmap di parità con ue-mcp.

È la fase più ridimensionata di tutte: **creare** un landscape da Python non
si può — verificato dal vivo su UE 5.8, spawnare `Landscape` restituisce un
`LandscapePlaceholder` vuoto, e `LandscapeSubsystem`/`LandscapeEditorObject`/
`ActorFactoryLandscape` non sono esposte al Python del motore. Su un
landscape già creato con Landscape Mode, invece, heightmap, weightmap,
materiale e grass si guidano tutti da qui.

La catena file immagine → texture → render target è verificata dal vivo
(PNG a gradiente riletto pixel per pixel dal render target); l'ultimo anello,
la chiamata `landscape_import_heightmap_from_render_target`, non lo è, perché
in quattroCantoni non esiste nessun landscape su cui provarla.
"""

import fake_unreal
import pytest


@pytest.fixture
def heightmap(tmp_path):
    """Un file immagine che esiste davvero: i tool rifiutano i path inesistenti
    prima ancora di chiamare Unreal."""
    percorso = tmp_path / "altimetria.png"
    percorso.write_bytes(b"\x89PNG\r\n\x1a\n")
    return str(percorso)


def piazza_landscape(unreal, label="Terreno", target_layers=("Erba", "Roccia"), edit_layers=("Layer")):
    landscape = fake_unreal.Landscape(
        label=label, components=4, target_layers=target_layers, edit_layers=edit_layers
    )
    unreal.state["actors"].append(landscape)
    return landscape


# --------------------------------------------------------------- introspezione


async def test_landscape_list_vuota_su_un_livello_senza_terreno(tools):
    esito = await tools.ue_landscape_list()

    assert esito == {"landscapes": [], "count": 0}


async def test_landscape_list_riporta_i_terreni_presenti(tools, unreal):
    piazza_landscape(unreal)

    esito = await tools.ue_landscape_list()

    assert esito["count"] == 1
    assert esito["landscapes"][0]["label"] == "Terreno"
    assert esito["landscapes"][0]["components"] == 4


async def test_landscape_info_elenca_target_layer_ed_edit_layer(tools, unreal):
    piazza_landscape(unreal)

    info = await tools.ue_landscape_info()

    assert info["target_layers"] == ["Erba", "Roccia"]
    assert info["components"] == 4
    assert info["grass_enabled"] is True


async def test_landscape_info_su_un_proxy_omette_cio_che_il_proxy_non_ha(tools, unreal):
    """Un `LandscapeStreamingProxy` non ha target layer né grass: i tool devono
    degradare invece di sollevare."""
    unreal.state["actors"].append(fake_unreal.LandscapeProxy(label="Pezzo"))

    info = await tools.ue_landscape_info()

    assert info["class"] == "LandscapeStreamingProxy"
    assert "target_layers" not in info
    assert "grass_enabled" not in info


async def test_senza_landscape_l_errore_spiega_che_va_creato_a_mano(tools):
    with pytest.raises(RuntimeError, match="Landscape Mode"):
        await tools.ue_landscape_info()


async def test_con_piu_landscape_serve_la_label(tools, unreal):
    piazza_landscape(unreal, label="Nord")
    piazza_landscape(unreal, label="Sud")

    with pytest.raises(RuntimeError, match="più landscape"):
        await tools.ue_landscape_info()

    info = await tools.ue_landscape_info("Sud")
    assert info["label"] == "Sud"


async def test_spawnare_un_landscape_da_script_da_solo_un_placeholder(tools):
    """Documenta la trappola: `ue_spawn_actor("Landscape")` sembra funzionare
    ma non crea un terreno, e infatti non compare in `ue_landscape_list`."""
    await tools.ue_spawn_actor("Landscape", label="Finto")

    assert (await tools.ue_landscape_list())["count"] == 0


# --------------------------------------------------------------- heightmap


async def test_import_heightmap_passa_dal_render_target(tools, unreal, heightmap):
    landscape = piazza_landscape(unreal)

    esito = await tools.ue_landscape_import_heightmap(heightmap)

    assert esito["imported"] is True
    assert esito["source"]["width"] == 64
    assert heightmap in unreal.state["textures_importate"]
    assert len(landscape.heightmap_importati) == 1


async def test_import_heightmap_su_file_inesistente_fallisce_prima_di_unreal(tools, unreal):
    piazza_landscape(unreal)

    with pytest.raises(RuntimeError, match="inesistente"):
        await tools.ue_landscape_import_heightmap("/percorso/che/non/esiste.png")

    assert unreal.state["textures_importate"] == []


async def test_import_heightmap_accetta_il_canale_rg(tools, unreal, heightmap):
    """I 16 bit di altezza stanno su R+G: è il modo di non perdere precisione."""
    landscape = piazza_landscape(unreal)

    esito = await tools.ue_landscape_import_heightmap(
        heightmap, rt_format="RGBA16f", from_rg_channel=True
    )

    assert esito["from_rg_channel"] is True
    assert landscape.heightmap_importati[0][1] is True
    assert unreal.state["render_targets"][-1].format == "RTF_RGBA16f"


async def test_formato_render_target_sconosciuto_elenca_i_validi(tools, unreal, heightmap):
    piazza_landscape(unreal)

    with pytest.raises(RuntimeError, match="rgba16f"):
        await tools.ue_landscape_import_heightmap(heightmap, rt_format="rgba99")


async def test_export_heightmap_scrive_il_file_richiesto(tools, unreal, tmp_path):
    piazza_landscape(unreal)

    esito = await tools.ue_landscape_export_heightmap(str(tmp_path), "fuori.png", resolution=512)

    assert esito["resolution"] == 512
    assert (str(tmp_path), "fuori.png") in unreal.state["render_target_esportati"]


# --------------------------------------------------------------- weightmap


async def test_import_weightmap_dipinge_il_layer(tools, unreal, heightmap):
    landscape = piazza_landscape(unreal)

    esito = await tools.ue_landscape_import_weightmap("Roccia", heightmap)

    assert esito["layer"] == "Roccia"
    assert landscape.weightmap_importati[0][1] == "Roccia"


async def test_import_weightmap_su_layer_inesistente_elenca_quelli_veri(tools, unreal, heightmap):
    """I target layer nascono dal materiale del landscape: non si creano da qui."""
    piazza_landscape(unreal)

    with pytest.raises(RuntimeError, match="Erba, Roccia"):
        await tools.ue_landscape_import_weightmap("Neve", heightmap)


# --------------------------------------------------------------- materiale/grass


async def test_set_material_assegna_e_rilegge(tools, unreal):
    piazza_landscape(unreal)
    await tools.ue_create_material("/Game/Terreno", "M_Terreno")

    info = await tools.ue_landscape_set_material("/Game/Terreno/M_Terreno")

    assert info["material"] is not None


async def test_set_material_inesistente_fallisce(tools, unreal):
    piazza_landscape(unreal)

    with pytest.raises(RuntimeError, match="non trovato"):
        await tools.ue_landscape_set_material("/Game/Terreno/M_Assente")


async def test_set_grass_spegne_l_erba(tools, unreal):
    piazza_landscape(unreal)

    esito = await tools.ue_landscape_set_grass(False)

    assert esito["grass_enabled"] is False


async def test_set_grass_su_un_proxy_spiega_dove_farlo(tools, unreal):
    unreal.state["actors"].append(fake_unreal.LandscapeProxy(label="Pezzo"))

    with pytest.raises(RuntimeError, match="attore Landscape principale"):
        await tools.ue_landscape_set_grass(True)

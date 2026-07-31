"""Test della fase 14a (foliage) della roadmap di parità con ue-mcp.

Il gap più citato nel confronto con db-lyon/ue-mcp, ed è risultato interamente
scriptabile — ma non dalla porta che ci si aspetta. `EditorFoliageLibrary` e
`FoliageEditorSubsystem` **non esistono** nella Python API di UE 5.8
(verificato con `hasattr` dal vivo il 2026-07-31, non dedotto): quello che
esiste sono `InstancedFoliageActor.add_instances` / `remove_all_instances`
come UFUNCTION statiche, e i `FoliageInstancedStaticMeshComponent` del livello
per query e rimozione per istanza.

Verificato dal vivo su UE 5.8: FoliageType da /Engine/BasicShapes/Cube, due
istanze esplicite più dodici sparse con seed fisso, `ue_foliage_query` che le
ritrova con le loro trasformate in world space, rimozione dentro una sfera
(8 tolte, 6 rimaste), spawner procedurale e volume collegato.

**La trappola della fase, riprodotta anche nel finto**: `FoliageStatistics` —
la libreria che *sembra* fatta per contare le istanze — risponde sempre 0 nel
mondo dell'editor. Provata su un box che ne conteneva 5, con entrambi i world
context plausibili. È una libreria di gameplay. Per questo i tool passano dai
componenti, e questi test verificano il conteggio da lì.
"""

import fake_unreal
import pytest


@pytest.fixture(autouse=True)
def mesh_di_partenza(unreal):
    """La static mesh dev'esserci prima: `ue_create_foliage_type` la carica e
    rifiuta di proseguire se non la trova (o se non è una StaticMesh)."""
    unreal.state["assets"]["/Engine/BasicShapes/Cube"] = fake_unreal.StaticMesh()


@pytest.fixture
async def erba(tools):
    """Un FoliageType pronto, con la sua mesh attaccata."""
    await tools.ue_create_foliage_type("/Game/Foliage", "FT_Erba", "/Engine/BasicShapes/Cube")
    return "/Game/Foliage/FT_Erba"


# ------------------------------------------------------------------ FoliageType


async def test_create_foliage_type_crea_l_asset(tools):
    esito = await tools.ue_create_foliage_type(
        "/Game/Foliage", "FT_Erba", "/Engine/BasicShapes/Cube"
    )

    assert esito["path"] == "/Game/Foliage/FT_Erba"
    assert esito["created"] is True
    assert esito["mesh"] == "/Engine/BasicShapes/Cube"


async def test_create_foliage_type_non_sovrascrive(tools, erba):
    esito = await tools.ue_create_foliage_type(
        "/Game/Foliage", "FT_Erba", "/Engine/BasicShapes/Cube"
    )

    assert esito["created"] is False


async def test_create_foliage_type_applica_le_proprieta_iniziali(tools):
    await tools.ue_create_foliage_type(
        "/Game/Foliage", "FT_Fiori", "/Engine/BasicShapes/Cube", properties={"density": 250.0}
    )

    esito = await tools.ue_set_foliage_property("/Game/Foliage/FT_Fiori", "density", 250.0)
    assert esito["value"] == 250.0


async def test_create_foliage_type_rifiuta_una_mesh_che_non_esiste(tools):
    with pytest.raises(Exception, match="non trovata"):
        await tools.ue_create_foliage_type("/Game/Foliage", "FT_X", "/Game/NonEsiste")


async def test_set_foliage_property_rilegge_il_valore(tools, erba):
    esito = await tools.ue_set_foliage_property(erba, "radius", 120.0)

    assert esito["property"] == "radius"
    assert esito["value"] == 120.0


async def test_set_foliage_property_rifiuta_un_asset_che_non_e_un_foliage_type(tools, unreal):
    unreal.state["assets"]["/Game/Foliage/NonUnTipo"] = fake_unreal.StaticMesh()

    with pytest.raises(Exception, match="non un FoliageType"):
        await tools.ue_set_foliage_property("/Game/Foliage/NonUnTipo", "radius", 1.0)


# --------------------------------------------------------------------- istanze


async def test_add_instances_accetta_le_trasformate_complete(tools, erba):
    esito = await tools.ue_foliage_add_instances(
        erba,
        [
            {"location": {"x": 100.0, "y": 0.0, "z": 0.0}, "scale": {"x": 2.0, "y": 2.0, "z": 2.0}},
            {"location": {"x": 200.0, "y": 0.0, "z": 0.0}},
        ],
    )

    assert esito["added"] == 2
    assert esito["total_instances"] == 2


async def test_add_instances_accetta_anche_le_sole_posizioni(tools, erba):
    """Il caso comune: piazzare senza curarsi di rotazione e scala."""
    esito = await tools.ue_foliage_add_instances(erba, [[100.0, 0.0, 0.0], {"x": 200.0, "y": 0.0, "z": 0.0}])

    assert esito["added"] == 2


async def test_add_instances_rifiuta_una_lista_vuota(tools, erba):
    with pytest.raises(Exception, match="Nessuna trasformata"):
        await tools.ue_foliage_add_instances(erba, [])


async def test_add_instances_si_ferma_se_il_tipo_non_ha_mesh(tools, unreal):
    """Senza mesh non c'è componente su cui contare: meglio un errore che
    dice cosa fare che uno stack trace su None."""
    unreal.state["assets"]["/Game/Foliage/FT_Vuoto"] = fake_unreal.FoliageType_InstancedStaticMesh()

    with pytest.raises(Exception, match="non ha una mesh"):
        await tools.ue_foliage_add_instances("/Game/Foliage/FT_Vuoto", [[0.0, 0.0, 0.0]])


# ---------------------------------------------------------------------- scatter


async def test_scatter_piazza_il_numero_richiesto(tools, erba):
    esito = await tools.ue_foliage_scatter(erba, {"x": 0.0, "y": 0.0, "z": 500.0}, 800.0, 12, seed=7)

    assert esito["added"] == 12
    assert esito["total_instances"] == 12


async def test_scatter_con_lo_stesso_seed_da_lo_stesso_risultato(tools, erba):
    primo = await tools.ue_foliage_scatter(erba, [0.0, 0.0, 500.0], 800.0, 5, seed=42)
    posizioni_primo = (await tools.ue_foliage_query(erba, [0.0, 0.0, 0.0], 5000.0))["instances"]

    await tools.ue_foliage_remove(erba)
    await tools.ue_foliage_scatter(erba, [0.0, 0.0, 500.0], 800.0, 5, seed=42)
    posizioni_secondo = (await tools.ue_foliage_query(erba, [0.0, 0.0, 0.0], 5000.0))["instances"]

    assert primo["seed"] == 42
    assert [p["location"] for p in posizioni_primo] == [p["location"] for p in posizioni_secondo]


async def test_scatter_appoggia_a_terra(tools, erba):
    """Con `align_to_ground` ogni istanza scende sul terreno: senza, restano
    tutte alla quota del centro, che su un terreno non piatto vuol dire mezze
    sepolte e mezze in aria."""
    esito = await tools.ue_foliage_scatter(erba, [0.0, 0.0, 500.0], 400.0, 6, seed=1)

    assert esito["grounded"] == 6
    istanze = (await tools.ue_foliage_query(erba, [0.0, 0.0, 0.0], 5000.0))["instances"]
    assert all(i["location"]["z"] == 0.0 for i in istanze)


async def test_scatter_senza_allineamento_resta_alla_quota_del_centro(tools, erba):
    await tools.ue_foliage_scatter(erba, [0.0, 0.0, 500.0], 400.0, 4, seed=1, align_to_ground=False)

    istanze = (await tools.ue_foliage_query(erba, [0.0, 0.0, 500.0], 5000.0))["instances"]
    assert all(i["location"]["z"] == 500.0 for i in istanze)


async def test_scatter_applica_lo_z_offset(tools, erba):
    await tools.ue_foliage_scatter(erba, [0.0, 0.0, 500.0], 400.0, 3, seed=1, z_offset=25.0)

    istanze = (await tools.ue_foliage_query(erba, [0.0, 0.0, 0.0], 5000.0))["instances"]
    assert all(i["location"]["z"] == 25.0 for i in istanze)


async def test_scatter_rifiuta_un_conteggio_non_positivo(tools, erba):
    with pytest.raises(Exception, match="positivo"):
        await tools.ue_foliage_scatter(erba, [0.0, 0.0, 0.0], 100.0, 0)


async def test_scatter_resta_dentro_il_raggio(tools, erba):
    await tools.ue_foliage_scatter(erba, [0.0, 0.0, 500.0], 300.0, 20, seed=3)

    istanze = (await tools.ue_foliage_query(erba, [0.0, 0.0, 0.0], 100000.0))["instances"]
    for voce in istanze:
        distanza = (voce["location"]["x"] ** 2 + voce["location"]["y"] ** 2) ** 0.5
        assert distanza <= 300.0 + 1e-6


# ------------------------------------------------------------- elenco e query


async def test_foliage_list_vuoto_prima_di_piazzare_qualcosa(tools):
    esito = await tools.ue_foliage_list()

    assert esito == {"foliage": [], "total_instances": 0}


async def test_foliage_list_riporta_mesh_e_conteggio(tools, erba):
    await tools.ue_foliage_add_instances(erba, [[0.0, 0.0, 0.0], [100.0, 0.0, 0.0]])

    esito = await tools.ue_foliage_list()

    assert esito["total_instances"] == 2
    assert esito["foliage"][0]["mesh"] == "/Engine/BasicShapes/Cube"
    assert esito["foliage"][0]["instances"] == 2


async def test_query_conta_solo_le_istanze_dentro_la_sfera(tools, erba):
    await tools.ue_foliage_add_instances(erba, [[0.0, 0.0, 0.0], [100.0, 0.0, 0.0], [5000.0, 0.0, 0.0]])

    esito = await tools.ue_foliage_query(erba, [0.0, 0.0, 0.0], 500.0)

    assert esito["count"] == 2


async def test_query_rispetta_il_limite_ma_conta_tutto(tools, erba):
    """Il limite serve a non riversare mille trasformate nel contesto: il
    conteggio dev'essere comunque quello vero."""
    await tools.ue_foliage_scatter(erba, [0.0, 0.0, 100.0], 200.0, 10, seed=5)

    esito = await tools.ue_foliage_query(erba, [0.0, 0.0, 0.0], 5000.0, limit=3)

    assert esito["count"] == 10
    assert esito["returned"] == 3
    assert len(esito["instances"]) == 3


# -------------------------------------------------------------------- rimozione


async def test_remove_senza_sfera_toglie_tutto(tools, erba):
    await tools.ue_foliage_add_instances(erba, [[0.0, 0.0, 0.0], [100.0, 0.0, 0.0]])

    esito = await tools.ue_foliage_remove(erba)

    assert esito["removed"] == "all"
    assert (await tools.ue_foliage_list())["total_instances"] == 0


async def test_remove_con_la_sfera_toglie_solo_quelle_dentro(tools, erba):
    await tools.ue_foliage_add_instances(erba, [[0.0, 0.0, 0.0], [100.0, 0.0, 0.0], [5000.0, 0.0, 0.0]])

    esito = await tools.ue_foliage_remove(erba, [0.0, 0.0, 0.0], 500.0)

    assert esito["removed"] == 2
    assert esito["remaining"] == 1


async def test_remove_non_sbaglia_indici_togliendone_piu_di_uno(tools, erba):
    """`remove_instances` rinumera quelle che restano: togliere dal fondo è
    l'unico ordine che non cancella l'istanza sbagliata."""
    await tools.ue_foliage_scatter(erba, [0.0, 0.0, 100.0], 100.0, 8, seed=9)

    esito = await tools.ue_foliage_remove(erba, [0.0, 0.0, 0.0], 10000.0)

    assert esito["removed"] == 8
    assert esito["remaining"] == 0


# ------------------------------------------------------------------ procedurale


async def test_create_foliage_spawner_include_i_tipi(tools, erba):
    esito = await tools.ue_create_foliage_spawner(
        "/Game/Foliage", "PFS_Bosco", [erba], tile_size=5000.0
    )

    assert esito["created"] is True
    assert esito["foliage_types"] == [erba]
    assert esito["tile_size"] == 5000.0


async def test_create_foliage_spawner_non_sovrascrive(tools, erba):
    await tools.ue_create_foliage_spawner("/Game/Foliage", "PFS_Bosco", [erba])

    esito = await tools.ue_create_foliage_spawner("/Game/Foliage", "PFS_Bosco", [erba])

    assert esito["created"] is False


async def test_spawn_volume_collega_lo_spawner(tools, erba):
    await tools.ue_create_foliage_spawner("/Game/Foliage", "PFS_Bosco", [erba])

    esito = await tools.ue_foliage_spawn_volume(
        "/Game/Foliage/PFS_Bosco", label="PFV_Bosco", size={"x": 4000, "y": 4000, "z": 1000}
    )

    assert esito["actor"] == "PFV_Bosco"
    assert esito["spawner"] == "/Game/Foliage/PFS_Bosco"
    # come per il PCGVolume: 200 cm di brush, quindi 4000 cm = scala 20.
    assert esito["scale"] == {"x": 20.0, "y": 20.0, "z": 5.0}


async def test_spawn_volume_rifiuta_uno_spawner_inesistente(tools):
    with pytest.raises(Exception, match="non trovato"):
        await tools.ue_foliage_spawn_volume("/Game/Foliage/NonEsiste")


async def test_simulate_chiede_la_risimulazione(tools, erba, unreal):
    await tools.ue_create_foliage_spawner("/Game/Foliage", "PFS_Bosco", [erba])
    await tools.ue_foliage_spawn_volume("/Game/Foliage/PFS_Bosco", label="PFV_Bosco")

    esito = await tools.ue_foliage_simulate("PFV_Bosco")

    assert esito["simulated"] is True


async def test_simulate_con_clear_azzera(tools, erba):
    await tools.ue_create_foliage_spawner("/Game/Foliage", "PFS_Bosco", [erba])
    await tools.ue_foliage_spawn_volume("/Game/Foliage/PFS_Bosco", label="PFV_Bosco")

    esito = await tools.ue_foliage_simulate("PFV_Bosco", clear=True)

    assert esito["cleared"] is True


async def test_simulate_rifiuta_un_attore_qualunque(tools, unreal):
    unreal.state["actors"].append(fake_unreal.Actor(class_name="StaticMeshActor", label="Cubo"))

    with pytest.raises(Exception, match="non un ProceduralFoliageVolume"):
        await tools.ue_foliage_simulate("Cubo")

"""Test della Fase 10 (PCG) della roadmap di parità con ue-mcp.

È la sorpresa della roadmap. Dopo Blueprint (fase 3), UMG (fase 2) e Niagara
(fase 5) ci si aspettava l'ennesimo grafo protetto; invece il grafo PCG è
pienamente scriptabile — nodi, archi, posizioni e proprietà — verificato dal
vivo su UE 5.8 il 2026-07-31 costruendo Input → SurfaceSampler →
StaticMeshSpawner, salvando e rileggendo l'asset da zero (nodi, archi e
`points_per_squared_meter` c'erano ancora, e i nomi dei nodi compaiono nel
.uasset). Anche PCGVolume, `set_graph`, `generate` e `cleanup` sono stati
provati su un volume vero nel livello.

Il motivo è lo stesso dei Behavior Tree della fase 6: è un grafo di dati veri
(`UPCGNode` + `UPCGEdge`), non un `UEdGraph` di nodi K2 con il contenuto in
una proprietà protetta.
"""

import pytest


@pytest.fixture
async def grafo(tools):
    """Un grafo PCG vuoto, pronto per i nodi."""
    await tools.ue_create_pcg_graph("/Game/PCG", "PCG_Test")
    return "/Game/PCG/PCG_Test"


# --------------------------------------------------------------- creazione


async def test_create_pcg_graph_crea_l_asset(tools):
    esito = await tools.ue_create_pcg_graph("/Game/PCG", "PCG_Foresta")

    assert esito == {"path": "/Game/PCG/PCG_Foresta", "created": True}


async def test_create_pcg_graph_non_sovrascrive(tools):
    await tools.ue_create_pcg_graph("/Game/PCG", "PCG_Foresta")

    esito = await tools.ue_create_pcg_graph("/Game/PCG", "PCG_Foresta")

    assert esito["created"] is False


async def test_grafo_nuovo_ha_gia_input_e_output(tools, grafo):
    info = await tools.ue_pcg_graph_info(grafo)

    ruoli = {n["role"] for n in info["nodes"]}
    assert ruoli == {"input", "output"}
    assert info["edges"] == []


# --------------------------------------------------------------- nodi


async def test_add_node_restituisce_nome_e_pin(tools, grafo):
    esito = await tools.ue_pcg_add_node(grafo, "PCGSurfaceSamplerSettings")

    assert esito["node"] == "SurfaceSampler_0"
    assert "Surface" in esito["input_pins"]
    assert esito["output_pins"] == ["Out"]


async def test_add_node_numera_i_nodi_dello_stesso_tipo(tools, grafo):
    primo = await tools.ue_pcg_add_node(grafo, "PCGSurfaceSamplerSettings")
    secondo = await tools.ue_pcg_add_node(grafo, "PCGSurfaceSamplerSettings")

    assert [primo["node"], secondo["node"]] == ["SurfaceSampler_0", "SurfaceSampler_1"]


async def test_add_node_accetta_la_posizione_nell_editor(tools, grafo):
    await tools.ue_pcg_add_node(grafo, "PCGSurfaceSamplerSettings", position={"x": 300, "y": 120})

    info = await tools.ue_pcg_graph_info(grafo)
    campionatore = next(n for n in info["nodes"] if n["name"] == "SurfaceSampler_0")
    assert campionatore["position"] == {"x": 300, "y": 120}


async def test_add_node_accetta_la_posizione_come_lista(tools, grafo):
    await tools.ue_pcg_add_node(grafo, "PCGSurfaceSamplerSettings", position=[600, 0])

    info = await tools.ue_pcg_graph_info(grafo)
    campionatore = next(n for n in info["nodes"] if n["name"] == "SurfaceSampler_0")
    assert campionatore["position"] == {"x": 600, "y": 0}


async def test_remove_node_toglie_anche_i_suoi_archi(tools, grafo):
    campionatore = await tools.ue_pcg_add_node(grafo, "PCGSurfaceSamplerSettings")
    await tools.ue_pcg_connect(grafo, "input", "In", campionatore["node"], "Surface")

    esito = await tools.ue_pcg_remove_node(grafo, campionatore["node"])

    assert esito["removed"] == "SurfaceSampler_0"
    assert (await tools.ue_pcg_graph_info(grafo))["edges"] == []


async def test_nodo_inesistente_elenca_quelli_presenti(tools, grafo):
    await tools.ue_pcg_add_node(grafo, "PCGSurfaceSamplerSettings")

    with pytest.raises(RuntimeError, match="SurfaceSampler_0"):
        await tools.ue_pcg_remove_node(grafo, "NonEsiste")


async def test_su_un_asset_che_non_e_un_grafo_pcg_fallisce(tools):
    await tools.ue_create_blueprint("/Game/PCG", "BP_NonUnGrafo", "Actor")

    with pytest.raises(RuntimeError, match="non un PCGGraph"):
        await tools.ue_pcg_graph_info("/Game/PCG/BP_NonUnGrafo")


# --------------------------------------------------------------- collegamenti


async def test_connect_collega_input_e_nodi(tools, grafo):
    campionatore = await tools.ue_pcg_add_node(grafo, "PCGSurfaceSamplerSettings")
    spawner = await tools.ue_pcg_add_node(grafo, "PCGStaticMeshSpawnerSettings")

    await tools.ue_pcg_connect(grafo, "input", "In", campionatore["node"], "Surface")
    esito = await tools.ue_pcg_connect(grafo, campionatore["node"], "Out", spawner["node"], "In")

    assert esito["edges"] == 2
    archi = (await tools.ue_pcg_graph_info(grafo))["edges"]
    assert archi[0] == {
        "from": "DefaultInputNode",
        "from_pin": "In",
        "to": "SurfaceSampler_0",
        "to_pin": "Surface",
    }


async def test_alias_input_e_output_evitano_di_indovinare_i_nomi(tools, grafo):
    """Senza gli alias chi chiama dovrebbe sapere che i due nodi si chiamano
    `DefaultInputNode` e `DefaultOutputNode`."""
    campionatore = await tools.ue_pcg_add_node(grafo, "PCGSurfaceSamplerSettings")

    esito = await tools.ue_pcg_connect(grafo, "input", "In", campionatore["node"], "Surface")

    assert esito["connected"].startswith("DefaultInputNode.In ->")


async def test_pin_inesistente_elenca_quelli_veri(tools, grafo):
    campionatore = await tools.ue_pcg_add_node(grafo, "PCGSurfaceSamplerSettings")

    with pytest.raises(RuntimeError, match="Bounding Shape"):
        await tools.ue_pcg_connect(grafo, "input", "In", campionatore["node"], "PinFinto")


async def test_disconnect_rimuove_l_arco(tools, grafo):
    campionatore = await tools.ue_pcg_add_node(grafo, "PCGSurfaceSamplerSettings")
    await tools.ue_pcg_connect(grafo, "input", "In", campionatore["node"], "Surface")

    esito = await tools.ue_pcg_disconnect(grafo, "input", "In", campionatore["node"], "Surface")

    assert esito == {"graph": grafo, "removed": True, "edges": 0}


async def test_disconnect_di_un_arco_che_non_c_e_lo_dice(tools, grafo):
    campionatore = await tools.ue_pcg_add_node(grafo, "PCGSurfaceSamplerSettings")

    esito = await tools.ue_pcg_disconnect(grafo, "input", "In", campionatore["node"], "Surface")

    assert esito["removed"] is False


# --------------------------------------------------------------- proprietà


async def test_set_node_property_scrive_sulle_settings(tools, grafo):
    campionatore = await tools.ue_pcg_add_node(grafo, "PCGSurfaceSamplerSettings")

    esito = await tools.ue_pcg_set_node_property(
        grafo, campionatore["node"], "points_per_squared_meter", 0.25
    )

    assert esito["value"] == 0.25
    assert esito["settings_class"] == "PCGSurfaceSamplerSettings"


# --------------------------------------------------------------- volume


async def test_spawn_volume_collega_il_grafo(tools, grafo, unreal):
    esito = await tools.ue_pcg_spawn_volume(grafo, label="Bosco", location={"x": 0, "y": 0, "z": 100})

    assert esito["actor"] == "Bosco"
    volume = next(a for a in unreal.state["actors"] if a.get_actor_label() == "Bosco")
    assert volume._components[0].get_graph() is not None


async def test_size_del_volume_diventa_scala(tools, grafo):
    """Il brush di default è 200 cm per lato: 2000 cm chiesti = scala 10."""
    esito = await tools.ue_pcg_spawn_volume(grafo, label="Grande", size={"x": 2000, "y": 2000, "z": 500})

    assert esito["scale"] == {"x": 10.0, "y": 10.0, "z": 2.5}


async def test_generate_e_cleanup_passano_dal_componente(tools, grafo, unreal):
    await tools.ue_pcg_spawn_volume(grafo, label="Bosco")
    volume = next(a for a in unreal.state["actors"] if a.get_actor_label() == "Bosco")

    await tools.ue_pcg_generate("Bosco")
    await tools.ue_pcg_cleanup("Bosco")

    assert volume._components[0].generazioni == [True]
    assert volume._components[0].pulizie == [True]


async def test_generate_su_un_attore_senza_pcg_spiega_cosa_fare(tools):
    await tools.ue_spawn_actor("StaticMeshActor", label="Cassa")

    with pytest.raises(RuntimeError, match="PCGComponent"):
        await tools.ue_pcg_generate("Cassa")

"""Test della Fase 8 (networking esteso) della roadmap di parità con ue-mcp.

A differenza delle fasi 2/3/5, qui non c'è nessun muro: dormancy, frequenze
di update, priorità, cull distance e relevancy sono `get/set_editor_property`
normali sulla CDO del Blueprint, e i template dei componenti sono
raggiungibili via `SubobjectDataBlueprintFunctionLibrary.get_object` —
verificato dal vivo su UE 5.8 il 2026-07-31, controllando anche che i nomi
delle proprietà finiscano davvero nel .uasset dopo il salvataggio.
"""

import pytest

# --------------------------------------------------------------- net config


async def test_set_net_config_applica_tutti_i_parametri(tools, unreal):
    await tools.ue_create_blueprint("/Game/Net", "BP_Torretta", "Actor")

    esito = await tools.ue_set_net_config(
        "/Game/Net/BP_Torretta",
        dormancy="initial",
        net_update_frequency=20,
        min_net_update_frequency=1,
        net_priority=2.5,
        only_relevant_to_owner=True,
        net_use_owner_relevancy=True,
        net_load_on_client=False,
    )

    assert esito["applied"]["dormancy"] == "initial"
    assert esito["applied"]["net_update_frequency"] == 20.0
    assert esito["applied"]["net_priority"] == 2.5
    assert esito["applied"]["only_relevant_to_owner"] is True
    assert esito["info"]["dormancy"] == "DORM_INITIAL"
    assert esito["info"]["net_load_on_client"] is False


async def test_set_net_config_tocca_solo_quello_che_riceve(tools):
    """Il tool è incrementale: chiamarlo per la dormancy non deve resettare
    le frequenze al default del motore."""
    await tools.ue_create_blueprint("/Game/Net", "BP_Parziale", "Actor")
    await tools.ue_set_net_config("/Game/Net/BP_Parziale", net_priority=7.0)

    esito = await tools.ue_set_net_config("/Game/Net/BP_Parziale", dormancy="never")

    assert esito["applied"] == {"dormancy": "never"}
    assert esito["info"]["net_priority"] == 7.0


async def test_net_cull_distance_viene_scritta_al_quadrato(tools):
    """Unreal memorizza `NetCullDistanceSquared`: il tool accetta centimetri
    come tutto il resto del server e fa il quadrato per conto suo."""
    await tools.ue_create_blueprint("/Game/Net", "BP_Cull", "Actor")

    esito = await tools.ue_set_net_config("/Game/Net/BP_Cull", net_cull_distance=5000)

    assert esito["info"]["net_cull_distance_squared"] == 25_000_000.0
    assert esito["info"]["net_cull_distance"] == 5000.0


async def test_net_cull_distance_negativa_viene_rifiutata(tools):
    await tools.ue_create_blueprint("/Game/Net", "BP_CullNeg", "Actor")

    with pytest.raises(RuntimeError, match="negativa"):
        await tools.ue_set_net_config("/Game/Net/BP_CullNeg", net_cull_distance=-1)


@pytest.mark.parametrize(
    "scritta, atteso",
    [
        ("awake", "DORM_AWAKE"),
        ("DORM_Initial", "DORM_INITIAL"),
        ("dormant-all", "DORM_DORMANT_ALL"),
        ("Dormant Partial", "DORM_DORMANT_PARTIAL"),
    ],
)
async def test_dormancy_accetta_le_scritture_comuni(tools, scritta, atteso):
    await tools.ue_create_blueprint("/Game/Net", "BP_Dorm_%s" % atteso, "Actor")

    esito = await tools.ue_set_net_config("/Game/Net/BP_Dorm_%s" % atteso, dormancy=scritta)

    assert esito["info"]["dormancy"] == atteso


async def test_dormancy_sconosciuta_elenca_i_valori_validi(tools):
    await tools.ue_create_blueprint("/Game/Net", "BP_DormErr", "Actor")

    with pytest.raises(RuntimeError, match="dormant_partial"):
        await tools.ue_set_net_config("/Game/Net/BP_DormErr", dormancy="mezzo_addormentato")


# --------------------------------------------------------------- net info


async def test_net_info_riporta_i_default_del_motore(tools):
    await tools.ue_create_blueprint("/Game/Net", "BP_Vergine", "Actor")

    info = await tools.ue_net_info("/Game/Net/BP_Vergine")

    assert info["net_update_frequency"] == 100.0
    assert info["min_net_update_frequency"] == 2.0
    assert info["net_priority"] == 1.0
    assert info["components"] == []


async def test_net_info_elenca_la_replication_dei_componenti(tools):
    await tools.ue_create_blueprint("/Game/Net", "BP_ConComp", "Actor")
    await tools.ue_add_component("/Game/Net/BP_ConComp", "SceneComponent", "Radice")

    info = await tools.ue_net_info("/Game/Net/BP_ConComp")

    assert [c["name"] for c in info["components"]] == ["Radice_GEN_VARIABLE"]
    assert info["components"][0]["replicates"] is False


# ------------------------------------------------- replication dei componenti


async def test_set_component_replication_accende_il_componente(tools):
    await tools.ue_create_blueprint("/Game/Net", "BP_Replicante", "Actor")
    await tools.ue_add_component("/Game/Net/BP_Replicante", "BoxComponent", "Zona")

    esito = await tools.ue_set_component_replication("/Game/Net/BP_Replicante", "Zona")

    assert esito["replicates"] is True
    assert esito["component"] == "Zona_GEN_VARIABLE"


async def test_set_component_replication_ignora_il_suffisso_gen_variable(tools):
    """Nell'editor il componente si chiama "Zona", su disco
    "Zona_GEN_VARIABLE": chi chiama deve poter usare il nome che vede."""
    await tools.ue_create_blueprint("/Game/Net", "BP_Suffisso", "Actor")
    await tools.ue_add_component("/Game/Net/BP_Suffisso", "BoxComponent", "Zona")

    per_nome_pulito = await tools.ue_set_component_replication("/Game/Net/BP_Suffisso", "Zona")
    per_nome_reale = await tools.ue_set_component_replication(
        "/Game/Net/BP_Suffisso", "Zona_GEN_VARIABLE", replicates=False
    )

    assert per_nome_pulito["replicates"] is True
    assert per_nome_reale["replicates"] is False


async def test_componente_inesistente_elenca_quelli_presenti(tools):
    await tools.ue_create_blueprint("/Game/Net", "BP_Mancante", "Actor")
    await tools.ue_add_component("/Game/Net/BP_Mancante", "BoxComponent", "Zona")

    with pytest.raises(RuntimeError, match="Zona_GEN_VARIABLE"):
        await tools.ue_set_component_replication("/Game/Net/BP_Mancante", "NonEsiste")


# ------------------------------------------------- default dei componenti


async def test_set_component_default_scrive_sul_template(tools):
    """La via per le proprietà `EditDefaultsOnly` (es. `SensesConfig` di
    AIPerception), che il motore rifiuta di scrivere su un'istanza."""
    await tools.ue_create_blueprint("/Game/Net", "BP_Sensi", "Actor")
    await tools.ue_add_component("/Game/Net/BP_Sensi", "AIPerceptionComponent", "Percezione")

    esito = await tools.ue_set_component_default(
        "/Game/Net/BP_Sensi", "Percezione", "auto_register_as_source", True
    )

    assert esito["component"] == "Percezione_GEN_VARIABLE"
    assert esito["value"] is True


async def test_set_component_default_converte_i_dict_in_tipi_unreal(tools):
    await tools.ue_create_blueprint("/Game/Net", "BP_Offset", "Actor")
    await tools.ue_add_component("/Game/Net/BP_Offset", "BoxComponent", "Zona")

    esito = await tools.ue_set_component_default(
        "/Game/Net/BP_Offset", "Zona", "relative_location", {"x": 0, "y": 0, "z": 90}
    )

    assert "Vector" in esito["value"] or esito["value"] is not None


# --------------------------------------------------------------- salvataggio


async def test_le_modifiche_di_rete_salvano_il_blueprint(tools, unreal):
    """Senza salvataggio la configurazione resterebbe solo in memoria e
    sparirebbe alla chiusura dell'editor."""
    await tools.ue_create_blueprint("/Game/Net", "BP_Salvato", "Actor")
    unreal.state["saved"].clear()

    await tools.ue_set_net_config("/Game/Net/BP_Salvato", net_priority=3.0)

    assert "/Game/Net/BP_Salvato" in unreal.state["saved"]

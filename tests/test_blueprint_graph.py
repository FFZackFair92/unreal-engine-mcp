"""Test dell'editing parziale del grafo Blueprint (Fase 3 della roadmap di parità con ue-mcp).

Copertura verificata dal vivo su un editor 5.8: si possono aggiungere nodi
evento per eventi ereditati overridabili e grafi funzione vuoti, non nodi
arbitrari (`Nodes` di `EdGraph` è protetta). Un nodo evento ha solo pin di
output — per questo non esiste un tool di connessione pin, vedi il commento
in cima a `ue_side.py::mcp_bp_add_event_override`.
"""

import pytest


async def _crea_blueprint(tools, nome="BP_Test"):
    return await tools.ue_create_blueprint("/Game/Blueprints", nome, parent_class="Actor")


async def test_list_graphs_include_event_graph_e_construction_script(tools):
    await _crea_blueprint(tools)

    esito = await tools.ue_bp_list_graphs("/Game/Blueprints/BP_Test")

    assert "EventGraph" in esito["graphs"]
    assert "UserConstructionScript" in esito["graphs"]


async def test_list_events_segnala_non_implementati_di_default(tools):
    await _crea_blueprint(tools)

    esito = await tools.ue_bp_list_events("/Game/Blueprints/BP_Test")

    per_nome = {e["name"]: e for e in esito["events"]}
    assert "ReceiveBeginPlay" in per_nome
    assert per_nome["ReceiveBeginPlay"]["is_implemented"] is False


async def test_add_event_override_lo_segna_implementato_e_ne_restituisce_i_pin(tools):
    await _crea_blueprint(tools)

    esito = await tools.ue_bp_add_event_override("/Game/Blueprints/BP_Test", "ReceiveBeginPlay")

    assert esito["node_path"].endswith("K2Node_Event_ReceiveBeginPlay")
    nomi_pin = {p["name"] for p in esito["pins"]}
    assert "then" in nomi_pin
    # Un nodo evento non ha mai pin di input: è il motivo per cui non esiste
    # un tool di connessione pin in questa fase.
    assert all(p["direction"] == "EGPD_OUTPUT" for p in esito["pins"])

    dopo = await tools.ue_bp_list_events("/Game/Blueprints/BP_Test")
    per_nome = {e["name"]: e for e in dopo["events"]}
    assert per_nome["ReceiveBeginPlay"]["is_implemented"] is True


async def test_add_event_override_su_evento_sconosciuto_fallisce_con_messaggio_utile(tools):
    await _crea_blueprint(tools)

    with pytest.raises(RuntimeError, match="EventoCheNonEsiste"):
        await tools.ue_bp_add_event_override("/Game/Blueprints/BP_Test", "EventoCheNonEsiste")


async def test_add_function_graph_compare_in_list_graphs(tools):
    await _crea_blueprint(tools)

    esito = await tools.ue_bp_add_function_graph("/Game/Blueprints/BP_Test", "ApriPorta")
    assert esito["graph_name"] == "ApriPorta"

    dopo = await tools.ue_bp_list_graphs("/Game/Blueprints/BP_Test")
    assert "ApriPorta" in dopo["graphs"]

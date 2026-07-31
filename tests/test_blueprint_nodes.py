"""Test della Fase 11 (authoring del grafo Blueprint) della roadmap.

È la rettifica della fase 3, che aveva concluso "i grafi Blueprint non sono
scriptabili". La conclusione era giusta sul metodo — `EdGraph.Nodes` è
protetta, e lo è tutt'ora — e sbagliata sul risultato: UE 5.8 espone
`BlueprintGraphEditor`, che manipola il grafo dall'esterno senza mai toccare
quella proprietà.

Verificato dal vivo su UE 5.8 il 2026-07-31: costruito BeginPlay →
PrintString → Branch con una variabile booleana che alimenta `Condition`,
Blueprint compilato `BS_UP_TO_DATE` senza errori né warning, salvato e
riletto da zero con le connessioni al loro posto (`PrintString` e `InString`
presenti anche nel .uasset).
"""

import pytest

STAMPA = "/Script/Engine.KismetSystemLibrary:PrintString"


@pytest.fixture
async def bp(tools):
    """Un Blueprint Actor nuovo, con il suo EventGraph."""
    await tools.ue_create_blueprint("/Game/Logica", "BP_Attore", "Actor")
    return "/Game/Logica/BP_Attore"


# --------------------------------------------------------------- introspezione


async def test_graph_info_vede_gli_eventi_gia_nel_grafo(tools, bp):
    info = await tools.ue_bp_graph_info(bp)

    titoli = {n["title"] for n in info["nodes"]}
    assert "Evento BeginPlay" in titoli
    assert info["connections"] == []
    assert info["errors"] == []


async def test_graph_info_su_un_grafo_inesistente_elenca_quelli_veri(tools, bp):
    with pytest.raises(RuntimeError, match="UserConstructionScript"):
        await tools.ue_bp_graph_info(bp, "GrafoCheNonEsiste")


# --------------------------------------------------------------- creazione nodi


async def test_add_call_function_restituisce_nome_oggetto_e_pin(tools, bp):
    esito = await tools.ue_bp_add_call_function(bp, STAMPA, position={"x": 400, "y": 0})

    assert esito["node"] == "K2Node_CallFunction_0"
    assert esito["position"] == {"x": 400, "y": 0}
    nomi = [p["name"] for p in esito["pins"]]
    assert "InString" in nomi and "then" in nomi


async def test_add_call_function_su_funzione_inesistente_spiega_il_formato(tools, bp):
    with pytest.raises(RuntimeError, match="/Script/<Modulo>"):
        await tools.ue_bp_add_call_function(bp, "/Script/Engine.Nope:Nope")


async def test_add_branch_ha_condition_then_else(tools, bp):
    esito = await tools.ue_bp_add_branch(bp, position=[800, 200])

    direzioni = {p["name"]: p["direction"] for p in esito["pins"]}
    assert direzioni["Condition"] == "input"
    assert direzioni["then"] == "output" and direzioni["else"] == "output"


async def test_add_custom_event_solo_nei_grafi_evento(tools, bp):
    """Una funzione non può contenere un Custom Event: il messaggio lo dice."""
    await tools.ue_bp_add_function_graph(bp, "ApriPorta")

    with pytest.raises(RuntimeError, match="grafo evento"):
        await tools.ue_bp_add_custom_event(bp, "Attiva", graph_name="ApriPorta")


async def test_add_variable_node_richiede_che_la_variabile_esista(tools, bp):
    with pytest.raises(RuntimeError, match="ue_add_variable"):
        await tools.ue_bp_add_variable_node(bp, "Inesistente", "get")


async def test_add_variable_node_get_e_set(tools, bp):
    await tools.ue_add_variable(bp, "Attivo", "bool")

    prendi = await tools.ue_bp_add_variable_node(bp, "Attivo", "get")
    metti = await tools.ue_bp_add_variable_node(bp, "Attivo", "set")

    assert prendi["class"] == "K2Node_VariableGet"
    assert prendi["pins"][0]["direction"] == "output"
    assert metti["class"] == "K2Node_VariableSet"


async def test_mode_diverso_da_get_o_set_viene_rifiutato(tools, bp):
    await tools.ue_add_variable(bp, "Attivo", "bool")

    with pytest.raises(RuntimeError, match="'get' o 'set'"):
        await tools.ue_bp_add_variable_node(bp, "Attivo", "incrementa")


async def test_remove_node_toglie_il_nodo_e_i_suoi_fili(tools, bp):
    stampa = await tools.ue_bp_add_call_function(bp, STAMPA)
    await tools.ue_bp_connect(bp, "event:ReceiveBeginPlay", "then", stampa["node"], "execute")

    esito = await tools.ue_bp_remove_node(bp, stampa["node"])

    assert esito["removed"] == "K2Node_CallFunction_0"
    assert (await tools.ue_bp_graph_info(bp))["connections"] == []


async def test_nodo_inesistente_elenca_quelli_presenti(tools, bp):
    with pytest.raises(RuntimeError, match="K2Node_Event_0"):
        await tools.ue_bp_remove_node(bp, "K2Node_Fantasma_9")


# --------------------------------------------------------------- palette


async def test_list_palette_filtra_per_sottostringa(tools, bp):
    esito = await tools.ue_bp_list_palette(bp, contains="ramo")

    assert esito["matches"] == ["Utilità|ControlloDiFlusso|Ramo"]


async def test_add_node_by_name_vuole_il_nome_localizzato(tools, bp):
    """La trappola della fase 11: la palette segue la lingua dell'editor, e
    il nome inglese non risolve niente."""
    with pytest.raises(RuntimeError, match="localizzati"):
        await tools.ue_bp_add_node_by_name(bp, "Utilities|FlowControl|Branch")

    esito = await tools.ue_bp_add_node_by_name(bp, "Utilità|ControlloDiFlusso|Ramo")
    assert esito["class"] == "K2Node_IfThenElse"


# --------------------------------------------------------------- connessioni


async def test_connect_collega_evento_e_nodo(tools, bp):
    stampa = await tools.ue_bp_add_call_function(bp, STAMPA)

    esito = await tools.ue_bp_connect(bp, "event:ReceiveBeginPlay", "then", stampa["node"], "execute")

    assert esito["connected"] == "K2Node_Event_0.then -> K2Node_CallFunction_0.execute"
    connessioni = (await tools.ue_bp_graph_info(bp))["connections"]
    assert connessioni == [
        {
            "from": "K2Node_Event_0",
            "from_pin": "then",
            "to": "K2Node_CallFunction_0",
            "to_pin": "execute",
        }
    ]


async def test_alias_event_evita_di_indovinare_il_nome_oggetto(tools, bp):
    """Senza l'alias chi chiama dovrebbe sapere che BeginPlay è
    `K2Node_Event_0` — un dettaglio interno e non garantito."""
    stampa = await tools.ue_bp_add_call_function(bp, STAMPA)

    esito = await tools.ue_bp_connect(bp, "event:ReceiveBeginPlay", "then", stampa["node"], "execute")

    assert esito["connected"].startswith("K2Node_Event_0.")


async def test_evento_inesistente_ricorda_di_usare_il_nome_membro(tools, bp):
    stampa = await tools.ue_bp_add_call_function(bp, STAMPA)

    with pytest.raises(RuntimeError, match="nome membro"):
        await tools.ue_bp_connect(bp, "event:Begin Play", "then", stampa["node"], "execute")


async def test_pin_inesistente_elenca_quelli_veri(tools, bp):
    stampa = await tools.ue_bp_add_call_function(bp, STAMPA)

    with pytest.raises(RuntimeError, match="InString"):
        await tools.ue_bp_connect(bp, "event:ReceiveBeginPlay", "then", stampa["node"], "PinFinto")


async def test_tipi_incompatibili_riportano_i_due_tipi(tools, bp):
    """Meglio dire *perché* Unreal rifiuta che limitarsi a fallire."""
    await tools.ue_add_variable(bp, "Attivo", "bool")
    prendi = await tools.ue_bp_add_variable_node(bp, "Attivo", "get")
    stampa = await tools.ue_bp_add_call_function(bp, STAMPA)

    with pytest.raises(RuntimeError, match="Booleano contro Exec"):
        await tools.ue_bp_connect(bp, prendi["node"], "Attivo", stampa["node"], "execute")


async def test_break_pin_riporta_quanti_fili_ha_staccato(tools, bp):
    stampa = await tools.ue_bp_add_call_function(bp, STAMPA)
    await tools.ue_bp_connect(bp, "event:ReceiveBeginPlay", "then", stampa["node"], "execute")

    esito = await tools.ue_bp_break_pin(bp, stampa["node"], "execute")

    assert esito["broken"] == 1
    assert (await tools.ue_bp_graph_info(bp))["connections"] == []


# --------------------------------------------------------------- valori dei pin


async def test_set_pin_value_scrive_il_letterale(tools, bp):
    stampa = await tools.ue_bp_add_call_function(bp, STAMPA)

    esito = await tools.ue_bp_set_pin_value(bp, stampa["node"], "InString", "Ciao")

    assert esito["value"] == "Ciao"


async def test_i_booleani_python_diventano_true_e_false_minuscoli(tools, bp):
    """Unreal serializza i default dei pin come stringhe: `True` di Python
    non è la stessa cosa di `true`."""
    ramo = await tools.ue_bp_add_branch(bp)

    esito = await tools.ue_bp_set_pin_value(bp, ramo["node"], "Condition", False)

    assert esito["value"] == "false"


async def test_set_pin_value_rilegge_perche_unreal_non_valida(tools, bp):
    """Verificato dal vivo: scrivere "non_un_bool" su un pin booleano viene
    accettato e memorizzato così com'è. Il tool rilegge sempre il pin, così
    chi chiama vede il valore vero invece di fidarsi del successo."""
    ramo = await tools.ue_bp_add_branch(bp)

    esito = await tools.ue_bp_set_pin_value(bp, ramo["node"], "Condition", "non_un_bool")

    assert esito["value"] == "non_un_bool"


async def test_set_pin_value_su_un_pin_di_uscita_fallisce(tools, bp):
    stampa = await tools.ue_bp_add_call_function(bp, STAMPA)

    with pytest.raises(RuntimeError, match="pin di input"):
        await tools.ue_bp_set_pin_value(bp, stampa["node"], "then", "x")


# --------------------------------------------------------------- salvataggio


async def test_su_un_motore_vecchio_dice_cosa_fare(tools, bp, unreal):
    """Il repo supporta UE 5.0+, ma questa API è recente: chi ha un motore
    senza `BlueprintGraphEditor` deve leggere una spiegazione, non un
    AttributeError."""
    del unreal.unreal.BlueprintGraphEditor

    with pytest.raises(RuntimeError, match="ue_cpp_class_create"):
        await tools.ue_bp_graph_info(bp)


async def test_ogni_modifica_compila_e_salva(tools, bp, unreal):
    """Un grafo modificato ma non compilato non è ancora il comportamento del
    gioco: senza compile+save la modifica resterebbe solo nell'editor."""
    unreal.state["saved"].clear()

    await tools.ue_bp_add_call_function(bp, STAMPA)

    assert "compiled:%s" % bp in unreal.state["saved"]
    assert bp in unreal.state["saved"]

"""Test della fase 14c (motore di flow) della roadmap di parità.

A differenza delle altre fasi, questa non tocca l'editor: un flow vive
interamente lato server MCP e si limita a chiamare i tool che già esistono.
Per questo i test verificano soprattutto le due cose che un flow può
sbagliare — la forma del file e i riferimenti fra passi — e la garanzia che
conta di più: che `dry_run` non esegua *niente*.

PyYAML è una dipendenza opzionale, quindi i test che riguardano la sintassi
YAML si saltano quando non c'è; quelli in JSON girano sempre, perché JSON è
un sottoinsieme di YAML e un flow in JSON dev'essere valido comunque.
"""

import json

import pytest

from unreal_mcp import flow as flow_engine

try:
    import yaml
except ImportError:  # pragma: no cover - dipende dall'ambiente
    yaml = None

serve_yaml = pytest.mark.skipif(yaml is None, reason="PyYAML non installato")


# ------------------------------------------------------------------- il parser


def test_carica_flow_da_json(tools):
    definizione = flow_engine.carica_flow(json.dumps({"steps": [{"tool": "ue_status"}]}))

    assert definizione["steps"][0]["tool"] == "ue_status"


def test_una_lista_nuda_e_gia_un_flow():
    """Il caso più corto possibile: nessuna variabile, solo i passi."""
    definizione = flow_engine.carica_flow(json.dumps([{"tool": "ue_status"}]))

    assert definizione["steps"] == [{"tool": "ue_status"}]


@serve_yaml
def test_carica_flow_da_yaml():
    definizione = flow_engine.carica_flow("steps:\n  - tool: ue_status\n")

    assert definizione["steps"][0]["tool"] == "ue_status"


def test_carica_flow_da_file(tmp_path):
    percorso = tmp_path / "flusso.json"
    percorso.write_text(json.dumps({"steps": [{"tool": "ue_status"}]}), encoding="utf-8")

    definizione = flow_engine.carica_flow(str(percorso))

    assert definizione["steps"][0]["tool"] == "ue_status"


def test_una_riga_che_non_e_un_file_da_un_errore_leggibile():
    with pytest.raises(flow_engine.FlowError, match="non è un file"):
        flow_engine.carica_flow("questo-non-esiste.yaml")


def test_flow_senza_passi():
    with pytest.raises(flow_engine.FlowError, match="nessun passo"):
        flow_engine.normalizza_passi(flow_engine.carica_flow(json.dumps({"steps": []})))


def test_passo_senza_tool():
    with pytest.raises(flow_engine.FlowError, match="manca il nome del tool"):
        flow_engine.normalizza_passi({"steps": [{"args": {}}]})


def test_args_deve_essere_un_oggetto():
    with pytest.raises(flow_engine.FlowError, match="'args' dev'essere un oggetto"):
        flow_engine.normalizza_passi({"steps": [{"tool": "ue_status", "args": [1, 2]}]})


# -------------------------------------------------------------- i riferimenti


def test_riferimento_intero_conserva_il_tipo():
    """`"${cubo.location}"` dev'essere il dict, non la sua rappresentazione:
    altrimenti il tool a valle riceve la stringa "{'x': 0.0, ...}"."""
    contesto = {"cubo": {"location": {"x": 1.0, "y": 2.0, "z": 3.0}}}

    assert flow_engine.espandi("${cubo.location}", contesto) == {"x": 1.0, "y": 2.0, "z": 3.0}


def test_riferimento_dentro_una_frase_viene_interpolato():
    contesto = {"cubo": {"label": "Cubo_0"}}

    assert flow_engine.espandi("copia di ${cubo.label}", contesto) == "copia di Cubo_0"


def test_riferimento_dentro_liste_e_dict():
    contesto = {"n": 5}

    espanso = flow_engine.espandi({"a": ["${n}", {"b": "${n}"}]}, contesto)

    assert espanso == {"a": [5, {"b": 5}]}


def test_riferimento_a_un_indice_di_lista():
    contesto = {"attori": {"labels": ["Uno", "Due"]}}

    assert flow_engine.espandi("${attori.labels.1}", contesto) == "Due"


def test_riferimento_mancante_dice_cosa_c_e():
    contesto = {"cubo": {"label": "Cubo_0"}}

    with pytest.raises(flow_engine.FlowError, match="label"):
        flow_engine.espandi("${cubo.posizione}", contesto)


def test_indice_non_numerico_su_una_lista():
    with pytest.raises(flow_engine.FlowError, match="indice numerico"):
        flow_engine.espandi("${l.primo}", {"l": [1, 2]})


def test_indice_fuori_range():
    with pytest.raises(flow_engine.FlowError, match="fuori range"):
        flow_engine.espandi("${l.9}", {"l": [1, 2]})


def test_non_si_scende_dentro_uno_scalare():
    with pytest.raises(flow_engine.FlowError, match="non si può scendere"):
        flow_engine.espandi("${n.x}", {"n": 5})


# ---------------------------------------------------------------- le condizioni


def test_when_assente_e_sempre_vero():
    assert flow_engine.condizione_vera(None, {}) is True


def test_when_su_un_riferimento():
    assert flow_engine.condizione_vera("${ok}", {"ok": True}) is True
    assert flow_engine.condizione_vera("${ok}", {"ok": False}) is False


def test_when_equals():
    contesto = {"a": {"created": True}}

    assert flow_engine.condizione_vera({"equals": ["${a.created}", True]}, contesto) is True
    assert flow_engine.condizione_vera({"equals": ["${a.created}", False]}, contesto) is False


def test_when_not_equals():
    assert flow_engine.condizione_vera({"not_equals": ["${n}", 1]}, {"n": 2}) is True


def test_when_exists_non_esplode_se_manca():
    """`exists` serve proprio a chiedere di un valore che potrebbe non esserci:
    deve rispondere False, non sollevare."""
    assert flow_engine.condizione_vera({"exists": "cubo.label"}, {}) is False
    assert flow_engine.condizione_vera({"exists": "cubo.label"}, {"cubo": {"label": "x"}}) is True


def test_when_equals_vuole_due_valori():
    with pytest.raises(flow_engine.FlowError, match="due valori"):
        flow_engine.condizione_vera({"equals": [1]}, {})


def test_when_di_forma_sconosciuta():
    with pytest.raises(flow_engine.FlowError, match="booleano"):
        flow_engine.condizione_vera(12.5, {})


# ------------------------------------------------------------------ esecuzione


async def test_flow_esegue_i_passi_in_ordine(tools, unreal):
    flusso = json.dumps(
        {
            "steps": [
                {"tool": "ue_spawn_actor", "args": {"class_ref": "StaticMeshActor", "label": "Uno"}},
                {"tool": "ue_spawn_actor", "args": {"class_ref": "PointLight", "label": "Due"}},
            ]
        }
    )

    esito = await tools.ue_flow_run(flusso)

    assert esito["failed"] == 0
    assert esito["executed"] == 2
    assert [a.get_actor_label() for a in unreal.state["actors"]] == ["Uno", "Due"]


async def test_save_rende_il_risultato_disponibile_al_passo_dopo(tools, unreal):
    flusso = json.dumps(
        {
            "steps": [
                {
                    "tool": "ue_spawn_actor",
                    "args": {"class_ref": "StaticMeshActor", "label": "Cubo"},
                    "save": "cubo",
                },
                {
                    "tool": "ue_set_actor_transform",
                    "args": {"label": "${cubo.label}", "scale": [2, 2, 2]},
                },
            ]
        }
    )

    esito = await tools.ue_flow_run(flusso)

    assert esito["failed"] == 0
    assert "cubo" in esito["variables"]


async def test_variables_iniziali_si_possono_passare_dall_esterno(tools, unreal):
    flusso = json.dumps(
        {"steps": [{"tool": "ue_spawn_actor", "args": {"class_ref": "${classe}", "label": "X"}}]}
    )

    esito = await tools.ue_flow_run(flusso, variables={"classe": "PointLight"})

    assert esito["failed"] == 0
    assert unreal.state["actors"][0].get_class().get_name() == "PointLight"


async def test_variables_del_flow_e_dell_esterno_si_sommano(tools):
    flusso = json.dumps(
        {
            "variables": {"classe": "StaticMeshActor", "nome": "DalFlow"},
            "steps": [{"tool": "ue_spawn_actor", "args": {"class_ref": "${classe}", "label": "${nome}"}}],
        }
    )

    esito = await tools.ue_flow_run(flusso, variables={"nome": "DallEsterno"})

    assert esito["results"][0]["status"] == "ok"


async def test_when_falso_salta_il_passo(tools, unreal):
    flusso = json.dumps(
        {
            "steps": [
                {
                    "tool": "ue_spawn_actor",
                    "args": {"class_ref": "StaticMeshActor", "label": "Saltato"},
                    "when": False,
                }
            ]
        }
    )

    esito = await tools.ue_flow_run(flusso)

    assert esito["results"][0]["status"] == "skipped"
    assert unreal.state["actors"] == []


async def test_tool_inesistente_suggerisce_qualcosa(tools):
    flusso = json.dumps({"steps": [{"tool": "ue_spawn_attore"}]})

    esito = await tools.ue_flow_run(flusso)

    assert esito["failed"] == 1
    assert "non esiste" in esito["results"][0]["error"]


async def test_un_flow_non_puo_chiamare_se_stesso(tools):
    flusso = json.dumps({"steps": [{"tool": "ue_flow_run", "args": {"flow": "[]"}}]})

    esito = await tools.ue_flow_run(flusso)

    assert "se stesso" in esito["results"][0]["error"]


async def test_un_flow_non_puo_chiamare_gli_interni_del_server(tools):
    """`run` e `lit` sono funzioni del modulo come i tool: senza il filtro un
    flow potrebbe eseguire Python arbitrario nell'editor passando da `run`."""
    flusso = json.dumps({"steps": [{"tool": "run", "args": {"code": "result = 1"}}]})

    esito = await tools.ue_flow_run(flusso)

    assert esito["failed"] == 1


async def test_si_ferma_al_primo_errore(tools, unreal):
    flusso = json.dumps(
        {
            "steps": [
                {"tool": "ue_set_actor_transform", "args": {"label": "NonEsiste"}},
                {"tool": "ue_spawn_actor", "args": {"class_ref": "StaticMeshActor", "label": "MaiCreato"}},
            ]
        }
    )

    esito = await tools.ue_flow_run(flusso)

    assert esito["stopped_at"] == 0
    assert esito["executed"] == 1
    assert unreal.state["actors"] == []


async def test_stop_on_error_false_prosegue(tools, unreal):
    flusso = json.dumps(
        {
            "steps": [
                {"tool": "ue_set_actor_transform", "args": {"label": "NonEsiste"}},
                {"tool": "ue_spawn_actor", "args": {"class_ref": "StaticMeshActor", "label": "Creato"}},
            ]
        }
    )

    esito = await tools.ue_flow_run(flusso, stop_on_error=False)

    assert esito["failed"] == 1
    assert esito["executed"] == 2
    assert unreal.state["actors"][0].get_actor_label() == "Creato"


async def test_continue_on_error_sul_singolo_passo(tools, unreal):
    flusso = json.dumps(
        {
            "steps": [
                {
                    "tool": "ue_set_actor_transform",
                    "args": {"label": "NonEsiste"},
                    "continue_on_error": True,
                },
                {"tool": "ue_spawn_actor", "args": {"class_ref": "StaticMeshActor", "label": "Creato"}},
            ]
        }
    )

    esito = await tools.ue_flow_run(flusso)

    assert esito["failed"] == 1
    assert unreal.state["actors"][0].get_actor_label() == "Creato"


# --------------------------------------------------------------------- dry run


async def test_dry_run_non_esegue_niente(tools, unreal):
    """La garanzia che rende il dry run utile."""
    flusso = json.dumps(
        {"steps": [{"tool": "ue_spawn_actor", "args": {"class_ref": "StaticMeshActor", "label": "Uno"}}]}
    )

    esito = await tools.ue_flow_run(flusso, dry_run=True)

    assert esito["dry_run"] is True
    assert esito["results"][0]["status"] == "ok (dry run)"
    assert unreal.state["actors"] == []


async def test_dry_run_trova_i_tool_sbagliati(tools):
    flusso = json.dumps({"steps": [{"tool": "ue_non_esiste"}]})

    esito = await tools.ue_flow_run(flusso, dry_run=True)

    assert esito["failed"] == 1


async def test_dry_run_trova_i_riferimenti_rotti(tools):
    flusso = json.dumps(
        {"steps": [{"tool": "ue_spawn_actor", "args": {"class_ref": "${mai_definito}"}}]}
    )

    esito = await tools.ue_flow_run(flusso, dry_run=True)

    assert esito["failed"] == 1
    assert "mai_definito" in esito["results"][0]["error"]


async def test_dry_run_non_si_lamenta_dei_riferimenti_a_passi_precedenti(tools):
    """Il segnaposto per i `save`: senza, ogni flow che usa un risultato
    sembrerebbe rotto in dry run pur essendo corretto."""
    flusso = json.dumps(
        {
            "steps": [
                {
                    "tool": "ue_spawn_actor",
                    "args": {"class_ref": "StaticMeshActor", "label": "Cubo"},
                    "save": "cubo",
                },
                {"tool": "ue_set_actor_transform", "args": {"label": "${cubo.label}"}},
            ]
        }
    )

    esito = await tools.ue_flow_run(flusso, dry_run=True)

    assert esito["failed"] == 0


# ------------------------------------------------------------------ riepilogo


def test_riepiloga_lascia_stare_i_risultati_corti():
    assert flow_engine.riepiloga({"a": 1}) == {"a": 1}


def test_riepiloga_accorcia_quelli_lunghi():
    esito = flow_engine.riepiloga({"attori": ["x" * 50 for _ in range(50)]})

    assert esito["_truncated"] is True
    assert esito["size"] > 400


async def test_i_risultati_lunghi_non_finiscono_interi_nel_riassunto(tools, unreal):
    """Il motivo per cui il motore esiste: un flow che restituisse venti
    risposte intere non farebbe risparmiare niente."""
    for indice in range(60):
        unreal.state["actors"].append(
            __import__("fake_unreal").Actor(class_name="StaticMeshActor", label="A%d" % indice)
        )
    flusso = json.dumps({"steps": [{"tool": "ue_list_actors"}]})

    esito = await tools.ue_flow_run(flusso)

    assert esito["results"][0]["result"]["_truncated"] is True

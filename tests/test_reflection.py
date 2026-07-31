"""Test dei tool di reflection (Fase 1 della roadmap di parità con ue-mcp).

`ClassIterator`/`StructIterator` nella Python API di UE elencano le
sottoclassi/sotto-struct della classe passata, non i suoi campi: verificato
dal vivo su un editor 5.8 (vedi docs/PARITY_ROADMAP.md). Questi test
verificano solo che gli helper attraversino la gerarchia finta nel modo
giusto e restituiscano la forma dati attesa — non riproducono il motore.
"""


async def test_find_classes_include_la_base_e_le_derivate(tools):
    esito = await tools.ue_find_classes("/Script/Engine.Actor")

    nomi = {c["name"] for c in esito["classes"]}
    assert "Actor" in nomi
    assert "Character" in nomi
    assert "BP_Nemico_C" in nomi
    assert esito["truncated"] is False


async def test_find_classes_filtro_per_nome(tools):
    esito = await tools.ue_find_classes("/Script/Engine.Actor", name_contains="Nemico")

    assert [c["name"] for c in esito["classes"]] == ["BP_Nemico_C"]
    assert esito["classes"][0]["path"] == "/Game/Blueprints/BP_Nemico.BP_Nemico_C"


async def test_find_classes_rispetta_il_limite(tools):
    esito = await tools.ue_find_classes("/Script/Engine.Actor", limit=1)

    assert len(esito["classes"]) == 1
    assert esito["truncated"] is True


async def test_find_classes_classe_sconosciuta_fallisce_con_messaggio_utile(tools):
    try:
        await tools.ue_find_classes("NomeCheNonEsiste123")
    except RuntimeError as exc:
        assert "NomeCheNonEsiste123" in str(exc)
    else:
        raise AssertionError("doveva fallire")


async def test_find_structs_include_la_base_e_le_derivate(tools):
    esito = await tools.ue_find_structs("/Script/CoreUObject.Vector")

    nomi = {s["name"] for s in esito["structs"]}
    assert "Vector" in nomi
    assert "Vector_NetQuantize" in nomi
    assert "Transform" not in nomi  # non deriva da Vector


async def test_reflect_enum_elenca_nome_valore_e_display_name(tools):
    esito = await tools.ue_reflect_enum("CollisionChannel")

    assert esito["count"] == 3
    per_nome = {v["name"]: v for v in esito["values"]}
    assert per_nome["ECC_Pawn"]["value"] == 2
    assert "display_name" in per_nome["ECC_Pawn"]


async def test_reflect_enum_sconosciuto_fallisce_con_messaggio_utile(tools):
    try:
        await tools.ue_reflect_enum("EnumCheNonEsiste")
    except RuntimeError as exc:
        assert "EnumCheNonEsiste" in str(exc)
    else:
        raise AssertionError("doveva fallire")

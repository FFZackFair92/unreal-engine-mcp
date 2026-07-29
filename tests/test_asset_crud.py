"""Test dei tool di gestione asset e della gerarchia fra attori.

Sono i buchi che restavano nel set: un agente sapeva creare e importare, ma non
cancellare quello che aveva sbagliato né comporre una scena agganciando gli
oggetti fra loro.
"""

import pytest


async def _crea_asset(tools, unreal, path, classe="StaticMesh"):
    unreal.state["assets"][path] = object()
    unreal.state["asset_classes"][path] = classe


# ------------------------------------------------------------------- asset


async def test_delete_asset(tools, unreal):
    await _crea_asset(tools, unreal, "/Game/Imported/SM_rock")
    esito = await tools.ue_delete_asset("/Game/Imported/SM_rock")
    assert esito["deleted"] is True
    assert "/Game/Imported/SM_rock" not in unreal.state["assets"]


async def test_delete_asset_referenziato_richiede_force(tools, unreal):
    """Cancellare un asset referenziato lascia riferimenti rotti: va detto."""
    await _crea_asset(tools, unreal, "/Game/Imported/SM_rock")
    unreal.state["referencers"]["/Game/Imported/SM_rock"] = ["/Game/Levels/L_Main"]

    with pytest.raises(RuntimeError, match="referenziato"):
        await tools.ue_delete_asset("/Game/Imported/SM_rock")

    esito = await tools.ue_delete_asset("/Game/Imported/SM_rock", force=True)
    assert esito["deleted"] is True
    assert esito["referencers"] == ["/Game/Levels/L_Main"]


async def test_delete_asset_inesistente(tools):
    with pytest.raises(RuntimeError, match="Nessun asset"):
        await tools.ue_delete_asset("/Game/NonEsiste")


async def test_delete_cartella(tools, unreal):
    unreal.state["directories"].add("/Game/Imported")
    await _crea_asset(tools, unreal, "/Game/Imported/SM_a")
    await _crea_asset(tools, unreal, "/Game/Imported/SM_b")

    esito = await tools.ue_delete_asset("/Game/Imported")
    assert esito["was_directory"] is True
    assert not [k for k in unreal.state["assets"] if k.startswith("/Game/Imported/")]


async def test_path_con_suffisso_oggetto(tools, unreal):
    """Unreal scrive i path anche come /Game/X.X: il suffisso va tolto."""
    await _crea_asset(tools, unreal, "/Game/Imported/SM_rock")
    esito = await tools.ue_delete_asset("/Game/Imported/SM_rock.SM_rock")
    assert esito["path"] == "/Game/Imported/SM_rock"
    assert esito["deleted"] is True


async def test_path_relativo_rifiutato(tools):
    with pytest.raises(RuntimeError, match="iniziano con /Game"):
        await tools.ue_delete_asset("Imported/SM_rock")


async def test_rename_asset(tools, unreal):
    await _crea_asset(tools, unreal, "/Game/Imported/SM_rock")
    esito = await tools.ue_rename_asset("/Game/Imported/SM_rock", "/Game/MyGame/SM_Roccia")
    assert esito["renamed"] is True
    assert "/Game/MyGame/SM_Roccia" in unreal.state["assets"]
    assert "/Game/Imported/SM_rock" not in unreal.state["assets"]


async def test_rename_su_destinazione_occupata(tools, unreal):
    await _crea_asset(tools, unreal, "/Game/A")
    await _crea_asset(tools, unreal, "/Game/B")
    with pytest.raises(RuntimeError, match="Esiste già"):
        await tools.ue_rename_asset("/Game/A", "/Game/B")


async def test_duplicate_asset(tools, unreal):
    await _crea_asset(tools, unreal, "/Game/M_Base")
    esito = await tools.ue_duplicate_asset("/Game/M_Base", "/Game/M_Variante")
    assert esito["duplicated"] is True
    assert "/Game/M_Base" in unreal.state["assets"]
    assert "/Game/M_Variante" in unreal.state["assets"]


async def test_make_folder_idempotente(tools):
    primo = await tools.ue_make_folder("/Game/MyGame/Meshes")
    assert primo["created"] is True and primo["existed"] is False
    secondo = await tools.ue_make_folder("/Game/MyGame/Meshes")
    assert secondo["created"] is False and secondo["existed"] is True


# --------------------------------------------------------------- gerarchia


async def _due_attori(tools):
    await tools.ue_spawn_actor("StaticMeshActor", label="Lampione")
    await tools.ue_spawn_actor("PointLight", label="Luce")


async def test_attach_e_gerarchia(tools):
    await _due_attori(tools)
    esito = await tools.ue_attach_actor("Luce", "Lampione")
    assert esito["attached"] is True
    assert esito["parent"] == "Lampione"

    albero = await tools.ue_actor_hierarchy()
    lampione = next(r for r in albero if r["label"] == "Lampione")
    assert [f["label"] for f in lampione["children"]] == ["Luce"]
    # la luce non compare più fra le radici
    assert "Luce" not in [r["label"] for r in albero]


async def test_detach(tools):
    await _due_attori(tools)
    await tools.ue_attach_actor("Luce", "Lampione")
    esito = await tools.ue_detach_actor("Luce")
    assert esito["detached"] is True
    assert esito["was_attached_to"] == "Lampione"

    albero = await tools.ue_actor_hierarchy()
    assert "Luce" in [r["label"] for r in albero]


async def test_detach_di_attore_libero(tools):
    await _due_attori(tools)
    esito = await tools.ue_detach_actor("Luce")
    assert esito["detached"] is False


async def test_attach_a_se_stesso(tools):
    await _due_attori(tools)
    with pytest.raises(RuntimeError, match="se stesso"):
        await tools.ue_attach_actor("Luce", "Luce")


async def test_attach_rule_non_valida(tools):
    await _due_attori(tools)
    with pytest.raises(RuntimeError, match="attach_rule"):
        await tools.ue_attach_actor("Luce", "Lampione", attach_rule="SNAP")


async def test_attach_attore_inesistente(tools):
    await _due_attori(tools)
    with pytest.raises(RuntimeError, match="Nessun attore"):
        await tools.ue_attach_actor("Fantasma", "Lampione")


async def test_delete_protegge_dagli_attori_nel_livello_aperto(tools, unreal):
    """Il caso che la protezione mancava, ed è quello che capita davvero.

    `find_package_referencers_for_asset` guarda solo il disco. Mentre un agente
    costruisce, il livello è aperto e non salvato: gli attori appena spawnati da
    un Blueprint non risultano da nessuna parte, il tool dà via libera, e con
    l'asset spariscono anche loro.
    """
    await tools.ue_create_blueprint("/Game/T", "BP_Usato")
    await tools.ue_spawn_actor("/Game/T/BP_Usato", label="Istanza")

    with pytest.raises(RuntimeError, match="nel livello"):
        await tools.ue_delete_asset("/Game/T/BP_Usato")

    esito = await tools.ue_delete_asset("/Game/T/BP_Usato", force=True)
    assert esito["deleted"] is True
    assert any("Istanza" in r for r in esito["referencers"])


async def test_delete_di_asset_non_istanziato_resta_libero(tools, unreal):
    await tools.ue_create_blueprint("/Game/T", "BP_MaiUsato")
    esito = await tools.ue_delete_asset("/Game/T/BP_MaiUsato")
    assert esito["deleted"] is True

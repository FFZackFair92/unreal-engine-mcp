"""Test della Fase 4 (Animazione) della roadmap di parità con ue-mcp.

A differenza di UMG e del grafo Blueprint, qui la scrittura funziona
davvero: `BlendParameters`/`SampleData` di un BlendSpace sono array di
struct ordinari, non protetti — verificato dal vivo salvando e ricaricando
l'asset da zero su un editor 5.8 reale (Remy_Skeleton, BS_Remy_Locomozione).
L'AnimGraph di un Anim Blueprint resta invece un EdGraph protetto come gli
altri: `ue_create_anim_blueprint` crea solo l'asset.
"""

import fake_unreal
import pytest


def _metti_skeleton(unreal, path="/Game/Char/Hero_Skeleton", bones=None, sockets=None):
    unreal.state["assets"][path] = fake_unreal.Skeleton(bones=bones, sockets=sockets)
    return path


def _metti_animazione(unreal, path, skeleton_path, **campi):
    seq = fake_unreal.AnimSequence(skeleton=skeleton_path)
    for chiave, valore in campi.items():
        setattr(seq, chiave, valore)
    unreal.state["assets"][path] = seq
    return path


async def test_skeleton_info_elenca_ossa_e_socket(tools, unreal):
    path = _metti_skeleton(
        unreal, bones=["root", "spine", "head"], sockets=["WeaponSocket"]
    )

    esito = await tools.ue_skeleton_info(path)

    assert esito["bones"] == ["root", "spine", "head"]
    assert esito["sockets"] == ["WeaponSocket"]


async def test_skeleton_info_su_path_sconosciuto_fallisce(tools):
    with pytest.raises(RuntimeError, match="non trovato"):
        await tools.ue_skeleton_info("/Game/Char/NonEsiste_Skeleton")


async def test_anim_sequence_info_legge_notify_e_curve(tools, unreal):
    sk = _metti_skeleton(unreal)
    path = _metti_animazione(
        unreal, "/Game/Char/Idle", sk,
        length=2.5, num_frames=75,
        notify_track_names=["1"], notify_event_names=["Footstep"],
        sync_marker_names=["Left", "Right"], curve_names=["MoveSpeed"],
    )

    esito = await tools.ue_anim_sequence_info(path)

    assert esito["length_seconds"] == 2.5
    assert esito["num_frames"] == 75
    assert esito["notify_event_names"] == ["Footstep"]
    assert esito["sync_marker_names"] == ["Left", "Right"]
    assert esito["curve_names"] == ["MoveSpeed"]


async def test_crea_blend_space_1d_con_sample(tools, unreal):
    sk = _metti_skeleton(unreal)
    idle = _metti_animazione(unreal, "/Game/Char/Idle", sk)
    run = _metti_animazione(unreal, "/Game/Char/Run", sk)

    esito = await tools.ue_create_blend_space_1d(
        "/Game/Char/Anim", "BS_Locomotion", sk,
        axis_name="Speed", axis_min=0, axis_max=600, grid_num=2,
        samples=[{"value": 0, "animation": idle}, {"value": 600, "animation": run}],
    )

    assert esito["created"] is True
    assert esito["axis"] == {"name": "Speed", "min": 0.0, "max": 600.0, "grid_num": 2}
    assert esito["samples"] == 2

    asset = unreal.state["assets"]["/Game/Char/Anim/BS_Locomotion"]
    assert len(asset.get_editor_property("SampleData")) == 2
    assert asset.get_editor_property("BlendParameters")[0].get_editor_property("Max") == 600.0


async def test_blend_space_gia_esistente_non_lo_ricrea(tools, unreal):
    sk = _metti_skeleton(unreal)
    await tools.ue_create_blend_space_1d("/Game/Char/Anim", "BS_Dup", sk)

    esito = await tools.ue_create_blend_space_1d("/Game/Char/Anim", "BS_Dup", sk)

    assert esito["created"] is False
    assert esito["reason"] == "esiste già"


async def test_blend_space_skeleton_sconosciuto_fallisce(tools):
    with pytest.raises(RuntimeError, match="non trovato"):
        await tools.ue_create_blend_space_1d(
            "/Game/Char/Anim", "BS_Bad", "/Game/Char/NonEsiste_Skeleton"
        )


async def test_crea_anim_montage_da_sequenza_esistente(tools, unreal):
    sk = _metti_skeleton(unreal)
    idle = _metti_animazione(unreal, "/Game/Char/Idle", sk)

    esito = await tools.ue_create_anim_montage("/Game/Char/Anim", "AM_Idle", idle)

    assert esito["created"] is True
    assert esito["slot_names"] == ["DefaultSlot"]


async def test_anim_montage_sequenza_sconosciuta_fallisce(tools):
    with pytest.raises(RuntimeError, match="non trovata"):
        await tools.ue_create_anim_montage(
            "/Game/Char/Anim", "AM_Bad", "/Game/Char/NonEsiste"
        )


async def test_crea_anim_blueprint_con_parent_default(tools, unreal):
    sk = _metti_skeleton(unreal)

    esito = await tools.ue_create_anim_blueprint("/Game/Char", "ABP_Hero", sk)

    assert esito["created"] is True
    assert esito["parent_class"] == "AnimInstance"

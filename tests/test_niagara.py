"""Test della Fase 5 (Niagara/VFX) della roadmap di parità con ue-mcp.

`EmitterHandles` di NiagaraSystem è protetta come `Nodes`/`WidgetTree`:
verificato dal vivo anche su template popolati della libreria di sistema
(/Niagara/DefaultAssets/Templates/Systems/...). Niente aggiunta di emitter
via Python — solo creazione dell'asset vuoto e introspezione di un sistema
esistente (che invece funziona davvero, a livello di asset).
"""

import fake_unreal
import pytest


async def test_crea_niagara_system(tools):
    esito = await tools.ue_create_niagara_system("/Game/VFX", "NS_Explosion")

    assert esito["created"] is True
    assert esito["path"] == "/Game/VFX/NS_Explosion"


async def test_niagara_system_gia_esistente_non_lo_ricrea(tools):
    await tools.ue_create_niagara_system("/Game/VFX", "NS_Dup")

    esito = await tools.ue_create_niagara_system("/Game/VFX", "NS_Dup")

    assert esito["created"] is False
    assert esito["reason"] == "esiste già"


async def test_niagara_system_info_legge_emitter_e_parametri(tools, unreal):
    sistema = fake_unreal.NiagaraSystem()
    sistema.emitters = [
        fake_unreal.NiagaraEmitterInfoFake("DirectionalBurst", is_enabled=True, is_lightweight=False),
        fake_unreal.NiagaraEmitterInfoFake("LocationBasedRibbon", is_enabled=False, is_lightweight=True),
    ]
    sistema.user_parameters = [
        fake_unreal.NiagaraUserParameterInfoFake("Color", "LinearColor"),
    ]
    unreal.state["assets"]["/Game/VFX/NS_Populated"] = sistema

    esito = await tools.ue_niagara_system_info("/Game/VFX/NS_Populated")

    assert esito["emitters"] == [
        {"name": "DirectionalBurst", "enabled": True, "lightweight": False},
        {"name": "LocationBasedRibbon", "enabled": False, "lightweight": True},
    ]
    assert esito["user_parameters"] == [{"name": "Color", "type": "LinearColor"}]


async def test_niagara_system_info_su_path_sconosciuto_fallisce(tools):
    with pytest.raises(RuntimeError, match="non trovato"):
        await tools.ue_niagara_system_info("/Game/VFX/NonEsiste")

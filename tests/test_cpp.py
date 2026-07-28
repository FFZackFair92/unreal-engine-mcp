"""Test della generazione di classi C++ e del reparent dei Blueprint.

È la via per dare logica eseguibile a un progetto quando i grafi Blueprint non
sono scrivibili da Python: la logica sta nella classe C++ padre.
"""

import json
from pathlib import Path

import pytest
from test_local import _make_batch_files, _make_engine

from unreal_mcp import local


@pytest.fixture
def progetto(tmp_path, monkeypatch):
    root = tmp_path / "engines"
    root.mkdir()
    _make_batch_files(_make_engine(root, "5.8"))
    monkeypatch.setenv("UE_MCP_ENGINE_DIRS", str(root))
    monkeypatch.setattr(local, "WINDOWS_LAUNCHER_DAT", tmp_path / "assente.dat")
    return local.create_project("MyGame", str(tmp_path / "Projects"))


def test_crea_il_modulo_su_un_progetto_blueprint_only(progetto):
    """Un progetto senza Source non compila nulla: il modulo va creato per intero."""
    esito = local.create_cpp_class(progetto["uproject"], "DoorBase")

    assert esito["module_created"] is True
    radice = Path(progetto["root"])
    assert (radice / "Source/MyGame/MyGame.Build.cs").exists()
    assert (radice / "Source/MyGame.Target.cs").exists()
    assert (radice / "Source/MyGame/MyGame.cpp").exists()

    # Senza la voce Modules il .uproject resta Blueprint-only.
    dati = json.loads(Path(progetto["uproject"]).read_text(encoding="utf-8-sig"))
    assert dati["Modules"][0]["Name"] == "MyGame"


def test_prefisso_a_per_gli_attori(progetto):
    esito = local.create_cpp_class(progetto["uproject"], "DoorBase", parent_class="Actor")
    assert esito["class"] == "ADoorBase"
    assert esito["parent"] == "AActor"


def test_prefisso_u_per_i_componenti(progetto):
    """`ActorComponent` inizia per A ma la classe è `UActorComponent`."""
    esito = local.create_cpp_class(
        progetto["uproject"], "HealthComp", parent_class="ActorComponent"
    )
    assert esito["class"] == "UHealthComp"
    assert esito["parent"] == "UActorComponent"
    header = Path(esito["header"]).read_text(encoding="utf-8")
    assert '#include "Components/ActorComponent.h"' in header


def test_header_del_parent_corretto(progetto):
    """AIController non sta sotto GameFramework/: sbagliarlo rompe la build."""
    esito = local.create_cpp_class(
        progetto["uproject"], "EnemyBrain", parent_class="AIController"
    )
    header = Path(esito["header"]).read_text(encoding="utf-8")
    assert '#include "AIController.h"' in header
    assert "GameFramework/AIController.h" not in header


def test_proprieta_e_macro_generate(progetto):
    esito = local.create_cpp_class(
        progetto["uproject"],
        "CornerSlot",
        properties=[
            {"name": "SlotIndex", "type": "int32", "default": "0"},
            {"name": "IsOccupied", "type": "bool", "category": "Gioco"},
        ],
    )
    header = Path(esito["header"]).read_text(encoding="utf-8")
    assert "UCLASS()" in header
    assert "GENERATED_BODY()" in header
    assert "MYGAME_API ACornerSlot : public AActor" in header
    assert "int32 SlotIndex = 0;" in header
    assert 'Category = "Gioco"' in header


def test_replication_genera_getlifetimereplicatedprops(progetto):
    """Dichiarare Replicated senza DOREPLIFETIME non replica nulla,
    e Unreal non lo segnala come errore."""
    esito = local.create_cpp_class(
        progetto["uproject"],
        "GameStateBase2",
        parent_class="GameStateBase",
        properties=[{"name": "Score", "type": "int32", "replicated": True}],
    )
    assert esito["replicated_properties"] == ["Score"]

    header = Path(esito["header"]).read_text(encoding="utf-8")
    source = Path(esito["source"]).read_text(encoding="utf-8")
    assert "GetLifetimeReplicatedProps" in header
    assert '#include "Net/UnrealNetwork.h"' in source
    assert "DOREPLIFETIME(AGameStateBase2, Score);" in source
    assert "bReplicates = true;" in source


def test_rep_notify_genera_la_callback(progetto):
    esito = local.create_cpp_class(
        progetto["uproject"],
        "Flag",
        properties=[{"name": "Team", "type": "int32", "replicated": True, "rep_notify": True}],
    )
    header = Path(esito["header"]).read_text(encoding="utf-8")
    source = Path(esito["source"]).read_text(encoding="utf-8")
    assert "ReplicatedUsing = OnRep_Team" in header
    assert "void OnRep_Team();" in header
    assert "void AFlag::OnRep_Team()" in source


def test_funzioni_blueprint_callable(progetto):
    """È così che la logica C++ diventa richiamabile dai grafi Blueprint."""
    esito = local.create_cpp_class(
        progetto["uproject"],
        "Scorer",
        functions=[
            {"name": "AddPoints", "params": "int32 Amount", "body": "Total += Amount;"}
        ],
    )
    header = Path(esito["header"]).read_text(encoding="utf-8")
    source = Path(esito["source"]).read_text(encoding="utf-8")
    assert "UFUNCTION(BlueprintCallable" in header
    assert "void AddPoints(int32 Amount);" in header
    assert "void AScorer::AddPoints(int32 Amount)" in source
    assert "Total += Amount;" in source


def test_forward_declaration_per_i_puntatori(progetto):
    """`TObjectPtr<APlayerState>` non compila se la classe non è dichiarata,
    e CoreMinimal.h non la porta dentro."""
    esito = local.create_cpp_class(
        progetto["uproject"],
        "CornerSlot",
        properties=[{"name": "OccupiedBy", "type": "TObjectPtr<APlayerState>"}],
        functions=[{"name": "TryCapture", "return_type": "bool", "params": "APlayerState* Who"}],
    )
    header = Path(esito["header"]).read_text(encoding="utf-8")
    assert "class APlayerState;" in header
    assert header.count("class APlayerState;") == 1     # una sola, non una per uso

    # Nel .cpp serve l'header vero: il corpo può volerne i membri.
    source = Path(esito["source"]).read_text(encoding="utf-8")
    assert '#include "GameFramework/PlayerState.h"' in source


def test_niente_forward_declaration_per_se_stessa(progetto):
    esito = local.create_cpp_class(
        progetto["uproject"],
        "Door",
        properties=[{"name": "Next", "type": "TObjectPtr<ADoor>"}],
    )
    header = Path(esito["header"]).read_text(encoding="utf-8")
    assert "class ADoor;" not in header


def test_tick_spento_di_default(progetto):
    """Il Tick acceso su ogni attore è uno spreco: va chiesto."""
    spento = local.create_cpp_class(progetto["uproject"], "Statica")
    assert "PrimaryActorTick.bCanEverTick = false;" in Path(spento["source"]).read_text(
        encoding="utf-8"
    )

    acceso = local.create_cpp_class(progetto["uproject"], "Mobile", with_tick=True)
    assert "PrimaryActorTick.bCanEverTick = true;" in Path(acceso["source"]).read_text(
        encoding="utf-8"
    )
    assert "virtual void Tick(float DeltaTime) override;" in Path(
        acceso["header"]
    ).read_text(encoding="utf-8")


def test_non_sovrascrive_senza_force(progetto):
    local.create_cpp_class(progetto["uproject"], "Unica")
    with pytest.raises(local.LocalError) as excinfo:
        local.create_cpp_class(progetto["uproject"], "Unica")
    assert "force=True" in str(excinfo.value)

    local.create_cpp_class(progetto["uproject"], "Unica", force=True)


def test_nome_classe_invalido(progetto):
    with pytest.raises(local.LocalError) as excinfo:
        local.create_cpp_class(progetto["uproject"], "2Sbagliato")
    assert "non valido" in str(excinfo.value)


def test_suggerisce_i_passi_successivi(progetto):
    """Generare il file non basta: senza build e reparent non serve a niente."""
    esito = local.create_cpp_class(progetto["uproject"], "DoorBase")
    passi = " ".join(esito["next_steps"])
    assert "ue_build_start" in passi
    assert "ue_reparent_blueprint" in passi


# ------------------------------------------------------------- lato editor


async def test_reparent_assorbe_le_variabili_del_padre(tools, unreal):
    await tools.ue_create_blueprint("/Game/BP", "BP_Door")
    await tools.ue_add_variable("/Game/BP/BP_Door", "ParentSpeed", "float")
    await tools.ue_add_variable("/Game/BP/BP_Door", "SoloMia", "float")

    esito = await tools.ue_reparent_blueprint("/Game/BP/BP_Door", "Actor")

    assert "ParentSpeed" in esito["absorbed_by_parent"]
    assert "SoloMia" in esito["variables_after"]
    assert unreal.state["reparented"]


async def test_reparent_pulisce_le_variabili_orfane(tools, unreal):
    await tools.ue_create_blueprint("/Game/BP", "BP_Pulito")
    esito = await tools.ue_reparent_blueprint(
        "/Game/BP/BP_Pulito", "Actor", remove_unused_variables=True
    )
    assert esito["unused_removed"] is True
    assert "/Game/BP/BP_Pulito" in unreal.state["cleaned"]

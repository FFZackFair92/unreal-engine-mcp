"""Test della compilazione C++: avvio in background e lettura dello stato."""

import json
from pathlib import Path

import pytest
from test_local import _make_batch_files, _make_engine  # riusa il finto albero del motore

from unreal_mcp import local


@pytest.fixture
def progetto(tmp_path, monkeypatch):
    root = tmp_path / "engines"
    root.mkdir()
    engine_root = _make_engine(root, "5.8")
    _make_batch_files(engine_root)

    monkeypatch.setenv("UE_MCP_ENGINE_DIRS", str(root))
    monkeypatch.setattr(local, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(local, "STATE_FILE", tmp_path / "state/state.json")
    monkeypatch.setattr(local, "BUILD_STATE_FILE", tmp_path / "state/build.json")
    monkeypatch.setattr(local, "WINDOWS_LAUNCHER_DAT", tmp_path / "assente.dat")

    creato = local.create_project("MyGame", str(tmp_path / "Projects"))
    (Path(creato["root"]) / "Source").mkdir()
    return creato


class _FakePopen:
    def __init__(self, args, **kwargs):
        self.args = args
        self.pid = 777


def test_build_richiede_una_cartella_source(progetto, tmp_path, monkeypatch):
    senza = local.create_project("SenzaCpp", str(tmp_path / "Altro"))
    with pytest.raises(local.LocalError) as excinfo:
        local.start_build(senza["uproject"])
    assert "Source" in str(excinfo.value)


def test_build_rifiuta_editor_aperto(progetto, monkeypatch):
    monkeypatch.setattr(local, "editor_status", lambda: {"running": True, "pid": 123})
    with pytest.raises(local.LocalError) as excinfo:
        local.start_build(progetto["uproject"])
    assert "Live Coding" in str(excinfo.value)
    assert "ue_editor_close" in str(excinfo.value)


def test_build_rileva_editor_aperto_a_mano(progetto, monkeypatch):
    """Un editor non lanciato dall'MCP non ha un pid nel nostro stato:
    va rilevato dal nome del processo, altrimenti il build parte e fallisce."""
    monkeypatch.setattr(
        local, "editor_status",
        lambda: {"running": False, "editor_process_detected": True},
    )
    with pytest.raises(local.LocalError) as excinfo:
        local.start_build(progetto["uproject"])
    assert "ancora aperto" in str(excinfo.value)


def test_build_chiude_live_coding(progetto, monkeypatch):
    """Il processo di console di Live Coding sopravvive all'editor e tiene
    il lock sulle DLL: start_build deve terminarlo."""
    monkeypatch.setattr(local, "editor_status", lambda: {"running": False})
    monkeypatch.setattr(local.subprocess, "Popen", _FakePopen)

    terminati = []
    monkeypatch.setattr(local, "terminate_process_by_name",
                        lambda nome: terminati.append(nome) or True)

    local.start_build(progetto["uproject"])
    assert terminati == ["LiveCodingConsole.exe"]


def test_build_avvio_e_stato(progetto, monkeypatch):
    monkeypatch.setattr(local, "editor_status", lambda: {"running": False})
    monkeypatch.setattr(local.subprocess, "Popen", _FakePopen)

    avvio = local.start_build(progetto["uproject"])
    assert avvio["pid"] == 777
    assert avvio["target"] == "MyGameEditor"
    salvato = json.loads(local.BUILD_STATE_FILE.read_text())
    assert salvato["jobs"][progetto["uproject"]]["pid"] == 777
    assert salvato["last"] == progetto["uproject"]

    # Il comando passa da uno script su file: la citazione annidata via `cmd /c`
    # perdeva la redirezione e lasciava il log vuoto.
    script = Path(progetto["root"]) / "Saved"
    generati = list(script.glob("mcp_build_run.*"))
    assert len(generati) == 1
    contenuto = generati[0].read_text(encoding="utf-8")
    assert "MyGameEditor" in contenuto
    assert "mcp_build.log" in contenuto
    assert "EXITCODE" in contenuto

    # compilazione ancora in corso
    monkeypatch.setattr(local, "_process_alive", lambda pid: True)
    assert local.build_status()["running"] is True

    # ...poi finita con successo
    Path(avvio["log"]).write_text(
        "Building MyGameEditor...\nTotal execution time: 42.0 seconds\nEXITCODE=0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(local, "_process_alive", lambda pid: False)
    stato = local.build_status()
    assert stato["running"] is False
    assert stato["succeeded"] is True
    assert stato["exit_code"] == "0"
    assert stato["errors"] == []


def test_build_riporta_errori_di_compilazione(progetto, monkeypatch):
    monkeypatch.setattr(local, "editor_status", lambda: {"running": False})
    monkeypatch.setattr(local.subprocess, "Popen", _FakePopen)
    avvio = local.start_build(progetto["uproject"])

    Path(avvio["log"]).write_text(
        "CornerSlot.cpp(42): error C2065: 'Pippo': undeclared identifier\n"
        "MyGameMode.cpp(10): warning C4100: parametro inutilizzato\n"
        "EXITCODE=6\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(local, "_process_alive", lambda pid: False)

    stato = local.build_status()
    assert stato["succeeded"] is False
    assert stato["exit_code"] == "6"
    assert any("C2065" in e for e in stato["errors"])
    assert any("C4100" in w for w in stato["warnings"])


def test_build_status_senza_compilazioni(tmp_path, monkeypatch):
    monkeypatch.setattr(local, "BUILD_STATE_FILE", tmp_path / "mai-avviata.json")
    assert local.build_status()["running"] is False


# ---------------------------------------------------- validazione argomenti shell


@pytest.mark.parametrize(
    "kwargs",
    [
        {"configuration": "Development & whoami"},
        {"configuration": "Development; id"},
        {"configuration": "Sviluppo"},
        {"target": "MyGameEditor && calc.exe"},
        {"target": "My Game Editor"},
        {"platform": "Win64 | id"},
        {"platform": "PlayStation5"},
    ],
)
def test_build_rifiuta_argomenti_iniettabili(progetto, monkeypatch, kwargs):
    """Gli argomenti finiscono in uno script di shell: vanno su allowlist.

    Senza questo, `configuration="Development & whoami"` non è una build con un
    nome strano, è un comando in più eseguito dalla shell.
    """
    monkeypatch.setattr(local.subprocess, "Popen", _FakePopen)
    with pytest.raises(local.LocalError):
        local.start_build(progetto["uproject"], **kwargs)


def test_build_stato_separato_per_progetto(progetto, tmp_path, monkeypatch):
    """Due progetti in parallelo non devono sovrascriversi lo stato."""
    monkeypatch.setattr(local.subprocess, "Popen", _FakePopen)
    primo = progetto["uproject"]
    secondo_creato = local.create_project("AltroGioco", str(tmp_path / "Projects2"))
    secondo = secondo_creato["uproject"]
    (Path(secondo).parent / "Source").mkdir(parents=True, exist_ok=True)

    local.start_build(primo)
    local.start_build(secondo)

    assert local.build_status(uproject=primo)["target"] == "MyGameEditor"
    assert local.build_status(uproject=secondo)["target"] == "AltroGiocoEditor"
    # senza argomento: l'ultimo avviato
    assert local.build_status()["target"] == "AltroGiocoEditor"

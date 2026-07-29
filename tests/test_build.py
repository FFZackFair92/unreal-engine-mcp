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


# ------------------------------------------------ build bloccata su mutex


def _log_build(progetto, righe):
    log = Path(progetto["uproject"]).parent / "Saved/Logs/mcp_build.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("\n".join(righe) + "\n", encoding="utf-8")


def test_build_ferma_su_mutex_non_e_una_build_lenta(progetto, monkeypatch):
    """Il consiglio "aspetta di più" qui è esattamente quello sbagliato.

    Build.bat con -WaitMutex aspetta all'infinito che un UnrealBuildTool
    precedente lo rilasci. Se quel processo è orfano non lo rilascia mai: il
    log resta di una riga e lo stato direbbe "in corso" per sempre.
    """
    monkeypatch.setattr(local.subprocess, "Popen", _FakePopen)
    local.start_build(progetto["uproject"])
    _log_build(progetto, ["Build.bat is already running, waiting for existing script to terminate..."])
    monkeypatch.setattr(local, "_process_alive", lambda pid: True)

    stato = local.build_status()
    assert stato["running"] is True
    assert stato["blocked"] is True
    assert "lock" in stato["reason"]
    assert "UnrealBuildTool.exe" in stato["reason"]


def test_build_che_sta_davvero_compilando_non_e_bloccata(progetto, monkeypatch):
    monkeypatch.setattr(local.subprocess, "Popen", _FakePopen)
    local.start_build(progetto["uproject"])
    _log_build(progetto, [
        "Build.bat is already running, waiting for existing script to terminate...",
        "Building IndovinaChi3DEditor...",
        "Compiling 12 actions",
    ])
    monkeypatch.setattr(local, "_process_alive", lambda pid: True)

    stato = local.build_status()
    assert stato["blocked"] is False
    assert "reason" not in stato


def test_seconda_build_rifiutata_mentre_la_prima_e_viva(progetto, monkeypatch):
    """Accodarne una seconda sullo stesso mutex peggiora e basta."""
    monkeypatch.setattr(local.subprocess, "Popen", _FakePopen)
    local.start_build(progetto["uproject"])
    _log_build(progetto, ["Build.bat is already running, waiting for existing script to terminate..."])
    monkeypatch.setattr(local, "_process_alive", lambda pid: True)

    with pytest.raises(local.LocalError) as errore:
        local.start_build(progetto["uproject"])
    testo = str(errore.value)
    assert "già una compilazione" in testo
    assert "bloccata su un mutex" in testo
    assert "force=True" in testo

    # force resta la via d'uscita dopo aver ripulito i processi
    local.start_build(progetto["uproject"], force=True)


def test_chiusura_forzata_ripulisce_i_processi_che_tengono_il_mutex(progetto, monkeypatch):
    """È la nostra taskkill a lasciare orfani: la pulizia va fatta qui."""
    monkeypatch.setattr(local.subprocess, "Popen", _FakePopen)
    local.launch_editor(progetto["uproject"], skip_module_check=True)

    # Vivo al controllo iniziale (altrimenti kill_editor non ha niente da fare),
    # morto subito dopo la taskkill.
    chiamate = {"n": 0}

    def _vivo(pid):
        chiamate["n"] += 1
        return chiamate["n"] == 1

    monkeypatch.setattr(local, "_process_alive", _vivo)
    monkeypatch.setattr(local.subprocess, "run", lambda *a, **k: None)
    # Su Linux kill_editor usa os.kill: il pid finto non esiste.
    monkeypatch.setattr(local.os, "kill", lambda *a: None)
    monkeypatch.setattr(local.platform, "system", lambda: "Windows")

    uccisi = []

    def _finto_kill(nome):
        uccisi.append(nome)
        return True

    monkeypatch.setattr(local, "terminate_process_by_name", _finto_kill)
    esito = local.kill_editor(timeout=0.1)

    assert esito["killed"] is True
    assert "UnrealBuildTool.exe" in esito["orphans_cleaned"]
    assert uccisi == list(local.BUILD_LOCK_PROCESSES)

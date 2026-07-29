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
    assert "mcp_build_" in contenuto and ".log" in contenuto
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


def _log_build(avvio, righe):
    log = Path(avvio["log"])
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("\n".join(righe) + "\n", encoding="utf-8")


def test_build_ferma_su_mutex_non_e_una_build_lenta(progetto, monkeypatch):
    """Il consiglio "aspetta di più" qui è esattamente quello sbagliato.

    Build.bat con -WaitMutex aspetta all'infinito che un UnrealBuildTool
    precedente lo rilasci. Se quel processo è orfano non lo rilascia mai: il
    log resta di una riga e lo stato direbbe "in corso" per sempre.
    """
    monkeypatch.setattr(local.subprocess, "Popen", _FakePopen)
    avvio = local.start_build(progetto["uproject"])
    _log_build(avvio, ["Build.bat is already running, waiting for existing script to terminate..."])
    monkeypatch.setattr(local, "_process_alive", lambda pid: True)

    stato = local.build_status()
    assert stato["running"] is True
    assert stato["blocked"] is True
    assert "lock" in stato["reason"]
    assert "ue_build_unblock" in stato["reason"]


def test_build_che_sta_davvero_compilando_non_e_bloccata(progetto, monkeypatch):
    monkeypatch.setattr(local.subprocess, "Popen", _FakePopen)
    avvio = local.start_build(progetto["uproject"])
    _log_build(avvio, [
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
    avvio = local.start_build(progetto["uproject"])
    _log_build(avvio, ["Build.bat is already running, waiting for existing script to terminate..."])
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


# ------------------------------------------ trovare chi tiene il lock di build


def test_riconosce_ubt_dentro_dotnet():
    """È il caso che rendeva il lock inestirpabile.

    Su UE 5 UnrealBuildTool è un assembly .NET: il processo si chiama
    `dotnet.exe`, quindi `taskkill /IM UnrealBuildTool.exe` non trova niente.
    Solo la riga di comando lo rivela.
    """
    assert local._matches_build_lock(
        "dotnet.exe",
        r'"C:\Program Files\dotnet\dotnet.exe" '
        r'"C:\UE_5.8\Engine\Binaries\DotNET\UnrealBuildTool\UnrealBuildTool.dll" -Mode=Build',
    )


def test_riconosce_gli_script_dentro_cmd():
    assert local._matches_build_lock("cmd.exe", 'cmd.exe /c ""C:\\UE\\Engine\\Build\\BatchFiles\\Build.bat" MyEditor Win64"')
    assert local._matches_build_lock("cmd.exe", 'cmd.exe /c "C:\\UE\\Engine\\Build\\BatchFiles\\RunUAT.bat" Turnkey')


def test_non_tocca_leditor():
    """L'editor usa la build, non la blocca: terminarlo sarebbe un danno."""
    assert not local._matches_build_lock(
        "UnrealEditor.exe", r'"C:\UE\UnrealEditor.exe" "C:\P\G.uproject"'
    )
    assert not local._matches_build_lock("UnrealEditor-Cmd.exe", "UnrealEditor-Cmd.exe render")


def test_non_tocca_dotnet_estranei():
    assert not local._matches_build_lock("dotnet.exe", "dotnet.exe run --project MioServizio")
    assert not local._matches_build_lock("chrome.exe", "chrome.exe --type=renderer")


def test_parsing_elenco_windows():
    uscita = "\n".join([
        "1234,dotnet.exe,C:\\dotnet.exe UnrealBuildTool.dll -Mode=Build -Project=C:\\a,b\\G.uproject",
        "5678,chrome.exe,chrome.exe --flag",
        "intestazione da ignorare",
        "",
    ])
    processi = local._parse_windows_process_list(uscita)
    assert [p["pid"] for p in processi] == [1234, 5678]
    # La riga di comando contiene virgole: non deve essere troncata.
    assert processi[0]["cmdline"].endswith("G.uproject")


def test_parsing_elenco_posix():
    uscita = "  PID COMMAND\n  42 /usr/bin/dotnet UnrealBuildTool.dll -Mode=Build\n  43 sleep 1\n"
    processi = local._parse_posix_process_list(uscita)
    assert processi[0]["pid"] == 42
    assert processi[0]["name"] == "dotnet"


def test_dry_run_non_termina_niente(monkeypatch):
    monkeypatch.setattr(
        local, "list_processes_with_cmdline",
        lambda: [{"pid": 1, "name": "dotnet.exe", "cmdline": "dotnet UnrealBuildTool.dll"}],
    )
    uccisi = []
    monkeypatch.setattr(local.subprocess, "run", lambda *a, **k: uccisi.append(a))

    esito = local.clear_build_locks()
    assert esito["dry_run"] is True
    assert len(esito["found"]) == 1
    assert esito["terminated"] == []
    assert uccisi == []
    assert "dry_run=False" in esito["note"]


def test_terminazione_effettiva(monkeypatch):
    monkeypatch.setattr(
        local, "list_processes_with_cmdline",
        lambda: [
            {"pid": 1, "name": "dotnet.exe", "cmdline": "dotnet UnrealBuildTool.dll"},
            {"pid": 2, "name": "chrome.exe", "cmdline": "chrome --type=renderer"},
        ],
    )
    monkeypatch.setattr(local.platform, "system", lambda: "Windows")
    comandi = []
    monkeypatch.setattr(local.subprocess, "run", lambda args, **k: comandi.append(args))

    esito = local.clear_build_locks(dry_run=False)
    assert [p["pid"] for p in esito["terminated"]] == [1]
    assert comandi == [["taskkill", "/PID", "1", "/T", "/F"]]
    assert "Terminati 1" in esito["note"]


def test_nessun_lock_lo_dice(monkeypatch):
    monkeypatch.setattr(local, "list_processes_with_cmdline", list)
    esito = local.clear_build_locks()
    assert esito["found"] == []
    assert "Nessun processo" in esito["note"]


def test_percorso_del_file_di_lock_di_build_bat():
    """Le righe 18-20 di Build.bat: percorso completo, \\ -> -, via i :, sotto %TMP%.

    Il nome va costruito come lo costruisce Windows anche quando il server gira
    altrove, o i separatori restano misti e si cerca un file che non esiste.
    """
    motore = local.EngineInstall(
        version="5.8", root=r"C:\Program Files\Epic Games\UE_5.8", editor="x", source="t"
    )
    assert (
        local.build_lock_file(motore).name
        == "C-Program Files-Epic Games-UE_5.8-Engine-Build-BatchFiles-Build.bat.lock"
    )


def test_file_di_lock_assente_non_e_un_problema(tmp_path, monkeypatch):
    monkeypatch.setenv("TMP", str(tmp_path))
    motore = local.EngineInstall(version="5.8", root=r"C:\UE", editor="x", source="t")
    stato = local.inspect_build_lock_file(motore)
    assert stato["exists"] is False
    assert stato["writable"] is True


def test_file_di_lock_non_scrivibile_e_la_causa_senza_processi(tmp_path, monkeypatch):
    """È il caso che i processi non spiegano: nessuno lo tiene, eppure blocca."""
    monkeypatch.setenv("TMP", str(tmp_path))
    motore = local.EngineInstall(version="5.8", root=r"C:\UE", editor="x", source="t")
    percorso = local.build_lock_file(motore)
    percorso.write_text("")

    stato = local.inspect_build_lock_file(motore)
    assert stato["exists"] is True
    assert stato["writable"] is True  # scrivibile: allora è un processo vivo

    import os as _os
    _os.chmod(percorso, 0o444)
    monkeypatch.setattr(local.os, "access", lambda p, m: False)

    def _nega(*a, **k):
        raise PermissionError("accesso negato")

    monkeypatch.setattr("builtins.open", _nega)
    stato = local.inspect_build_lock_file(motore)
    assert stato["writable"] is False
    assert "senza che nessun processo lo tenga" in stato["reason"]


# ------------------------------------------- l'ambiente dei processi figli


def test_tmp_viene_sempre_fornito_ai_processi_figli(monkeypatch, tmp_path):
    """La causa vera del lock che non si scioglieva.

    Un client MCP avvia il server con un ambiente ridotto: su Windows la lista
    predefinita ha TEMP ma non TMP. Build.bat costruisce il file di lock con
    %tmp%, che senza quella variabile diventa la radice del disco — non
    scrivibile — e lo script stampa "is already running" per sempre, senza che
    nessun processo tenga niente.
    """
    monkeypatch.delenv("TMP", raising=False)
    monkeypatch.setenv("TEMP", str(tmp_path))

    env = local.child_environment()
    assert env["TMP"] == str(tmp_path)
    assert env["TEMP"] == str(tmp_path)


def test_tmp_inesistente_viene_sostituito(monkeypatch):
    """Una TMP che punta a una cartella che non c'è è peggio che assente."""
    monkeypatch.setenv("TMP", r"C:\cartella\che\non\esiste")
    monkeypatch.delenv("TEMP", raising=False)
    assert Path(local.child_environment()["TMP"]).is_dir()


def test_la_build_passa_lambiente_al_processo(progetto, monkeypatch, tmp_path):
    """Non basta avere la funzione: Popen deve riceverla davvero."""
    monkeypatch.delenv("TMP", raising=False)
    monkeypatch.setenv("TEMP", str(tmp_path))
    catturato = {}

    class _Cattura(_FakePopen):
        def __init__(self, args, **kwargs):
            catturato.update(kwargs)
            super().__init__(args, **kwargs)

    monkeypatch.setattr(local.subprocess, "Popen", _Cattura)
    local.start_build(progetto["uproject"])

    assert "env" in catturato, "senza env il figlio eredita l'ambiente ridotto del server"
    assert catturato["env"]["TMP"] == str(tmp_path)

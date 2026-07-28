"""Test del livello locale: scoperta engine, creazione progetti, apertura/chiusura editor."""

import json
import platform
from pathlib import Path

import pytest

from unreal_mcp import local

# --------------------------------------------------------------- fixtures


def _make_engine(root: Path, version: str = "5.4") -> Path:
    """Crea un finto albero di installazione Unreal."""
    engine_root = root / f"UE_{version}"
    for relative in (
        "Engine/Binaries/Win64/UnrealEditor.exe",
        "Engine/Binaries/Linux/UnrealEditor",
        "Engine/Binaries/Mac/UnrealEditor.app/Contents/MacOS/UnrealEditor",
    ):
        path = engine_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fake editor", encoding="utf-8")
    build = engine_root / "Engine/Build/Build.version"
    build.parent.mkdir(parents=True, exist_ok=True)
    major, minor = version.split(".")
    build.write_text(json.dumps({"MajorVersion": int(major), "MinorVersion": int(minor)}), encoding="utf-8")

    # un template minimale
    template = engine_root / "Templates/TP_ThirdPerson"
    (template / "Content/ThirdPerson").mkdir(parents=True, exist_ok=True)
    (template / "Content/ThirdPerson/BP_Char.uasset").write_text("bp", encoding="utf-8")
    (template / "Source/TP_ThirdPerson").mkdir(parents=True, exist_ok=True)
    (template / "Source/TP_ThirdPerson/Module.cpp").write_text("// c++", encoding="utf-8")
    (template / "TP_ThirdPerson.uproject").write_text('{"FileVersion": 3}', encoding="utf-8")
    return engine_root


def _make_batch_files(engine_root: Path) -> None:
    """Crea gli script di build per tutte le piattaforme.

    Epic li distribuisce come `.bat` su Windows e `.sh` sotto `Linux/`o `Mac/`:
    creandoli tutti, i test girano identici ovunque e la CI può usare Linux.
    """
    batch = engine_root / "Engine/Build/BatchFiles"
    for stem in ("Build", "RunUAT"):
        windows = batch / f"{stem}.bat"
        windows.parent.mkdir(parents=True, exist_ok=True)
        windows.write_text("@echo off", encoding="utf-8")
        for sistema in ("Linux", "Mac"):
            unix = batch / sistema / f"{stem}.sh"
            unix.parent.mkdir(parents=True, exist_ok=True)
            unix.write_text("#!/bin/sh\n", encoding="utf-8")
            unix.chmod(0o755)


@pytest.fixture
def engines(tmp_path, monkeypatch):
    root = tmp_path / "engines"
    root.mkdir()
    _make_engine(root, "5.4")
    _make_engine(root, "5.8")
    monkeypatch.setenv("UE_MCP_ENGINE_DIRS", str(root))
    monkeypatch.setenv("UE_MCP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(local, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(local, "STATE_FILE", tmp_path / "state/state.json")
    monkeypatch.setattr(local, "WINDOWS_LAUNCHER_DAT", tmp_path / "inesistente.dat")
    return root


# ------------------------------------------------------------- scoperta engine


def test_find_engines(engines):
    found = local.find_engines()
    assert [e.version for e in found] == ["5.8", "5.4"]   # ordinati dal più recente
    assert all(Path(e.editor).exists() for e in found)


def test_resolve_engine_default_e_specifico(engines):
    assert local.resolve_engine().version == "5.8"
    assert local.resolve_engine("5.4").version == "5.4"


def test_resolve_engine_per_guid(engines, monkeypatch):
    """Un progetto copiato e riaperto punta al motore con un GUID, non con
    il numero di versione: va risolto lo stesso."""
    reali = local.find_engines()
    guid = "{129D8DB7-43F1-E40E-C86F-ACA44FD5F1F6}"
    con_guid = [
        local.EngineInstall(e.version, e.root, e.editor, "RegistryBuilds",
                            guid if e.version == "5.8" else "")
        for e in reali
    ]
    monkeypatch.setattr(local, "find_engines", lambda: con_guid)

    assert local.resolve_engine(guid).version == "5.8"


def test_resolve_engine_guid_sconosciuto_con_un_solo_motore(engines, monkeypatch):
    """GUID che non corrisponde a nulla: con un motore solo si usa quello,
    invece di bloccare tutto per un identificativo stantio."""
    uno = local.find_engines()[:1]
    monkeypatch.setattr(local, "find_engines", lambda: uno)
    assert local.resolve_engine("{GUID-INESISTENTE}").root == uno[0].root


def test_resolve_engine_mancante(engines):
    with pytest.raises(local.LocalError) as excinfo:
        local.resolve_engine("4.27")
    assert "Disponibili: 5.8, 5.4" in str(excinfo.value)


def test_nessun_engine(tmp_path, monkeypatch):
    monkeypatch.setenv("UE_MCP_ENGINE_DIRS", str(tmp_path / "vuoto"))
    monkeypatch.setattr(local, "WINDOWS_LAUNCHER_DAT", tmp_path / "inesistente.dat")
    monkeypatch.setattr(local, "_engines_from_registry", lambda: [])
    with pytest.raises(local.LocalError) as excinfo:
        local.resolve_engine()
    assert "UE_MCP_ENGINE_DIRS" in str(excinfo.value)


def test_list_templates(engines):
    templates = local.list_templates(local.resolve_engine("5.4"))
    assert [t["name"] for t in templates] == ["TP_ThirdPerson"]
    assert templates[0]["has_source"] is True


# ------------------------------------------------------------ creazione progetto


def test_create_project_blank(engines, tmp_path):
    result = local.create_project("MyGame", str(tmp_path / "Projects"))

    root = Path(result["root"])
    uproject = json.loads((root / "MyGame.uproject").read_text(encoding="utf-8"))
    plugins = {p["Name"]: p["Enabled"] for p in uproject["Plugins"]}

    assert uproject["EngineAssociation"] == "5.8"
    assert plugins["PythonScriptPlugin"] and plugins["RemoteControl"]
    assert (root / "Content").is_dir() and (root / "Config").is_dir()

    # le impostazioni Remote Control vanno in DefaultRemoteControl.ini
    # (URemoteControlSettings è UCLASS(config = RemoteControl), modulo RemoteControlCommon)
    rc_ini = (root / "Config/DefaultRemoteControl.ini").read_text(encoding="utf-8")
    assert "[/Script/RemoteControlCommon.RemoteControlSettings]" in rc_ini
    assert "bEnableRemotePythonExecution=True" in rc_ini
    assert "RemoteControlHttpServerPort=30010" in rc_ini
    # senza l'allowlist la chiamata a ExecutePythonCommandEx viene rifiutata
    assert "PythonScriptPlugin.PythonScriptLibrary" in rc_ini
    assert "bAllowAnyRemoteFunctionCall=False" in rc_ini

    # Il bridge non chiama mai ExecuteConsoleCommand via web API: quel gate
    # resta chiuso. I comandi console partono da dentro Python.
    assert "bAllowConsoleCommandRemoteExecution=False" in rc_ini
    assert "bAllowConsoleCommandRemoteExecution=True" not in rc_ini

    init_py = (root / "Content/Python/init_unreal.py").read_text(encoding="utf-8")
    assert "WebControl.StartServer" in init_py


def test_create_project_con_gamemode_e_mappa(engines, tmp_path):
    result = local.create_project(
        "MyGame",
        str(tmp_path / "Projects"),
        default_map="/Game/MyGame/Levels/L_Main",
        default_game_mode="/Game/MyGame/Blueprints/BP_GM.BP_GM_C",
        plugins=["ModelingToolsEditorMode", "GameplayAbilities"],
    )
    root = Path(result["root"])
    assert "GameDefaultMap=/Game/MyGame/Levels/L_Main" in (
        root / "Config/DefaultEngine.ini"
    ).read_text(encoding="utf-8")
    assert "GlobalDefaultGameMode=/Game/MyGame/Blueprints/BP_GM.BP_GM_C" in (
        root / "Config/DefaultGame.ini"
    ).read_text(encoding="utf-8")
    assert "GameplayAbilities" in result["plugins_enabled"]


def test_create_project_da_template_blueprint_only(engines, tmp_path):
    result = local.create_project(
        "DaTemplate", str(tmp_path / "Projects"), engine_version="5.4",
        template="TP_ThirdPerson",
    )
    root = Path(result["root"])
    assert (root / "Content/ThirdPerson/BP_Char.uasset").exists()
    assert not (root / "Source").exists()          # escluso: blueprint_only
    assert not (root / "TP_ThirdPerson.uproject").exists()  # .uproject riscritto col nome nuovo
    assert (root / "DaTemplate.uproject").exists()


def test_create_project_template_con_source(engines, tmp_path):
    result = local.create_project(
        "ConCpp", str(tmp_path / "Projects"), engine_version="5.4",
        template="TP_ThirdPerson", blueprint_only=False,
    )
    assert (Path(result["root"]) / "Source/TP_ThirdPerson/Module.cpp").exists()


def test_create_project_template_inesistente(engines, tmp_path):
    with pytest.raises(local.LocalError) as excinfo:
        local.create_project("X", str(tmp_path / "P"), template="TP_Inventato")
    assert "Disponibili" in str(excinfo.value)


def test_create_project_nome_invalido(engines, tmp_path):
    with pytest.raises(local.LocalError) as excinfo:
        local.create_project("My Game", str(tmp_path / "P"))
    assert "non valido" in str(excinfo.value)


def test_create_project_gia_esistente(engines, tmp_path):
    local.create_project("MyGame", str(tmp_path / "P"))
    with pytest.raises(local.LocalError) as excinfo:
        local.create_project("MyGame", str(tmp_path / "P"))
    assert "force=True" in str(excinfo.value)
    local.create_project("MyGame", str(tmp_path / "P"), force=True)  # non solleva


# ------------------------------------------------------------ info e plugin


def test_project_info_e_plugin(engines, tmp_path):
    created = local.create_project("MyGame", str(tmp_path / "P"))
    info = local.project_info(created["uproject"])
    assert info["bridge_ready"] is True
    assert info["engine_association"] == "5.8"

    local.set_project_plugins(created["uproject"], enable=["AdvancedSessions"], disable=["Metasound"])
    dopo = local.project_info(created["uproject"])
    assert "AdvancedSessions" in dopo["plugins_enabled"]
    assert "Metasound" not in dopo["plugins_enabled"]


def test_find_projects(engines, tmp_path):
    local.create_project("Uno", str(tmp_path / "P"))
    local.create_project("Due", str(tmp_path / "P/Sub"))
    trovati = {p["name"] for p in local.find_projects(str(tmp_path / "P"))}
    assert trovati == {"Uno", "Due"}


# ------------------------------------------------------- avvio/chiusura editor


class _FakePopen:
    def __init__(self, args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.pid = 4242


def test_launch_editor(engines, tmp_path, monkeypatch):
    created = local.create_project("MyGame", str(tmp_path / "P"))
    catturato = {}

    def fake_popen(args, **kwargs):
        process = _FakePopen(args, **kwargs)
        catturato["args"] = args
        return process

    monkeypatch.setattr(local.subprocess, "Popen", fake_popen)
    result = local.launch_editor(created["uproject"])

    assert result["pid"] == 4242
    assert "-RCWebControlEnable" in catturato["args"]
    assert created["uproject"] in catturato["args"]
    assert Path(catturato["args"][0]).name.startswith("UnrealEditor")
    assert json.loads(local.STATE_FILE.read_text())["pid"] == 4242


def test_launch_editor_senza_plugin_bridge(engines, tmp_path, monkeypatch):
    created = local.create_project("Senza", str(tmp_path / "P"))
    path = Path(created["uproject"])
    path.write_text(json.dumps({"FileVersion": 3, "EngineAssociation": "5.8", "Plugins": []}), encoding="utf-8")

    monkeypatch.setattr(local.subprocess, "Popen", _FakePopen)
    with pytest.raises(local.LocalError) as excinfo:
        local.launch_editor(str(path))
    assert "ue_project_set_plugins" in str(excinfo.value)


def test_editor_status_e_kill(engines, tmp_path, monkeypatch):
    created = local.create_project("MyGame", str(tmp_path / "P"))
    monkeypatch.setattr(local.subprocess, "Popen", _FakePopen)
    local.launch_editor(created["uproject"])

    vivo = {"stato": True}
    monkeypatch.setattr(local, "_process_alive", lambda pid: vivo["stato"])
    assert local.editor_status()["running"] is True

    comandi = []

    def fake_run(args, **kwargs):
        comandi.append(args)
        vivo["stato"] = False
        return type("R", (), {"stdout": "", "returncode": 0})()

    monkeypatch.setattr(local.subprocess, "run", fake_run)
    monkeypatch.setattr(local.platform, "system", lambda: "Windows")

    killed = local.kill_editor(timeout=2)
    assert killed["killed"] is True and killed["still_alive"] is False
    assert comandi[0][:2] == ["taskkill", "/PID"]
    assert local.editor_status()["running"] is False


def test_kill_editor_senza_processo(engines, tmp_path):
    local.STATE_DIR.mkdir(parents=True, exist_ok=True)
    local.STATE_FILE.write_text("{}", encoding="utf-8")
    assert local.kill_editor()["killed"] is False


@pytest.mark.skipif(platform.system() == "Windows", reason="path specifici Windows")
def test_editor_executable_multipiattaforma(engines):
    engine = local.resolve_engine("5.4")
    assert Path(engine.editor).exists()

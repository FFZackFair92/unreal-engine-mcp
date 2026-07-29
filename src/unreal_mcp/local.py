"""Operazioni sulla macchina locale: trovare le installazioni di Unreal, creare
progetti da specifica, aprirli e chiuderli.

La Remote Control API funziona solo con un editor **già aperto**: tutto ciò che
riguarda il ciclo di vita del progetto (creazione, avvio, chiusura) passa da
qui, lanciando `UnrealEditor.exe` come processo figlio.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# ------------------------------------------------------------------ costanti

#: Plugin abilitati di default nei progetti creati da qui: senza i primi due
#: il bridge MCP non funziona.
DEFAULT_PLUGINS = [
    "PythonScriptPlugin",   # Python Editor Script Plugin
    "RemoteControl",        # Remote Control API (web server :30010)
    "Metasound",            # audio procedurale
    "ModelingToolsEditorMode",
]

WINDOWS_LAUNCHER_DAT = Path(
    r"C:\ProgramData\Epic\UnrealEngineLauncher\LauncherInstalled.dat"
)

STATE_DIR = Path(os.environ.get("UE_MCP_STATE_DIR", Path.home() / ".unreal-mcp"))
STATE_FILE = STATE_DIR / "state.json"

#: Copiato in <Progetto>/Content/Python/init_unreal.py: Unreal lo esegue a ogni
#: avvio dell'editor, garantendo che la porta 30010 sia in ascolto.
INIT_UNREAL_SOURCE = '''"""Avvio automatico del web server Remote Control (generato da unreal-mcp)."""

import unreal

try:
    unreal.SystemLibrary.execute_console_command(None, "WebControl.StartServer")
    unreal.log("[unreal-mcp] Remote Control web server avviato sulla porta 30010.")
except Exception as exc:  # noqa: BLE001
    unreal.log_error("[unreal-mcp] Avvio web server fallito: %s" % exc)
'''


class LocalError(RuntimeError):
    """Errore di un'operazione locale (engine non trovato, progetto esistente, ...)."""


# ------------------------------------------------------------------- engine


@dataclass(frozen=True)
class EngineInstall:
    version: str
    root: str
    editor: str
    source: str
    #: Identificativo con cui il .uproject può riferirsi a questo motore.
    #: Per le build registrate a mano è un GUID tipo "{129D8DB7-...}".
    identifier: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _editor_executable(root: Path) -> Path | None:
    system = platform.system()
    candidates = {
        "Windows": [
            root / "Engine/Binaries/Win64/UnrealEditor.exe",
            root / "Engine/Binaries/Win64/UE4Editor.exe",
        ],
        "Linux": [root / "Engine/Binaries/Linux/UnrealEditor"],
        "Darwin": [
            root / "Engine/Binaries/Mac/UnrealEditor.app/Contents/MacOS/UnrealEditor",
            root / "Engine/Binaries/Mac/UnrealEditor",
        ],
    }.get(system, [])
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _version_from_root(root: Path) -> str:
    build_version = root / "Engine/Build/Build.version"
    try:
        data = json.loads(build_version.read_text(encoding="utf-8"))
        return "%s.%s" % (data.get("MajorVersion"), data.get("MinorVersion"))
    except Exception:  # noqa: BLE001
        name = root.name
        return name[3:] if name.upper().startswith("UE_") else name


def _engines_from_launcher() -> list[EngineInstall]:
    if not WINDOWS_LAUNCHER_DAT.exists():
        return []
    try:
        data = json.loads(WINDOWS_LAUNCHER_DAT.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return []
    found = []
    for entry in data.get("InstallationList", []):
        if not str(entry.get("AppName", "")).upper().startswith("UE_"):
            continue
        root = Path(entry["InstallLocation"])
        editor = _editor_executable(root)
        if editor:
            found.append(
                EngineInstall(
                    version=str(entry.get("AppName"))[3:],
                    root=str(root),
                    editor=str(editor),
                    source="EpicLauncher",
                )
            )
    return found


def _engines_from_registry() -> list[EngineInstall]:
    """Motori registrati nel registro di Windows.

    Due strutture diverse:
    - `HKLM\\SOFTWARE\\EpicGames\\Unreal Engine\\<versione>` ha **sottochiavi** con
      dentro il valore `InstalledDirectory`;
    - `HKCU\\SOFTWARE\\Epic Games\\Unreal Engine\\Builds` ha **valori** in cui il
      nome è l'identificativo (di solito un GUID) e il dato è il percorso.
      È qui che finiscono i motori registrati a mano, ed è a questo GUID che si
      riferisce un `.uproject` copiato e riaperto.
    """
    if platform.system() != "Windows":
        return []
    try:
        import winreg
    except ImportError:
        return []

    found: list[EngineInstall] = []

    # 1) Installazioni "ufficiali": sottochiavi con InstalledDirectory
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\EpicGames\Unreal Engine") as key:
            for index in range(winreg.QueryInfoKey(key)[0] or 0):
                name = winreg.EnumKey(key, index)
                try:
                    with winreg.OpenKey(key, name) as sub:
                        root = Path(winreg.QueryValueEx(sub, "InstalledDirectory")[0])
                except OSError:
                    continue
                editor = _editor_executable(root)
                if editor:
                    found.append(EngineInstall(name, str(root), str(editor), "Registry", name))
    except OSError:
        pass

    # 2) Build registrate dall'utente: valori GUID -> percorso
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"SOFTWARE\Epic Games\Unreal Engine\Builds") as key:
            for index in range(winreg.QueryInfoKey(key)[1] or 0):
                identifier, value, _tipo = winreg.EnumValue(key, index)
                root = Path(str(value))
                editor = _editor_executable(root)
                if editor:
                    found.append(
                        EngineInstall(
                            _version_from_root(root), str(root), str(editor),
                            "RegistryBuilds", identifier,
                        )
                    )
    except OSError:
        pass

    return found


#: File opzionale accanto al .uproject con il percorso del motore da usare.
#: È la via di scampo quando registro ed elenco del Launcher non bastano —
#: succede con i motori installati in percorsi non standard.
ENGINE_OVERRIDE_FILE = "mcp_engine.txt"


def _engine_from_override(project_dir: Path) -> EngineInstall | None:
    """Motore indicato esplicitamente dal file di override del progetto."""
    override = project_dir / ENGINE_OVERRIDE_FILE
    if not override.exists():
        return None

    for riga in override.read_text(encoding="utf-8").splitlines():
        riga = riga.split("#", 1)[0].strip().strip('"')
        if not riga:
            continue
        root = Path(riga)
        editor = _editor_executable(root)
        if editor:
            return EngineInstall(
                _version_from_root(root), str(root), str(editor), "override", ""
            )
        raise LocalError(
            "Il file %s indica '%s', ma lì non c'è un eseguibile dell'editor."
            % (override, riga)
        )
    return None


def _engines_from_env() -> list[EngineInstall]:
    """Path espliciti via UE_MCP_ENGINE_DIRS (separati da os.pathsep) o UE_ROOT."""
    raw = os.environ.get("UE_MCP_ENGINE_DIRS", "")
    paths = [p for p in raw.split(os.pathsep) if p.strip()]
    if os.environ.get("UE_ROOT"):
        paths.append(os.environ["UE_ROOT"])

    found = []
    for item in paths:
        base = Path(item)
        roots = [base] if _editor_executable(base) else [d for d in base.iterdir() if d.is_dir()] if base.is_dir() else []
        for root in roots:
            editor = _editor_executable(root)
            if editor:
                found.append(
                    EngineInstall(_version_from_root(root), str(root), str(editor), "env")
                )
    return found


def find_engines() -> list[EngineInstall]:
    """Tutte le installazioni di Unreal trovate, senza duplicati."""
    seen: dict[str, EngineInstall] = {}
    for engine in _engines_from_env() + _engines_from_launcher() + _engines_from_registry():
        seen.setdefault(str(Path(engine.root).resolve()).lower(), engine)
    return sorted(seen.values(), key=lambda e: e.version, reverse=True)


def resolve_engine(
    version: str | None = None,
    engine_root: str | None = None,
    project_dir: str | Path | None = None,
) -> EngineInstall:
    """Sceglie l'engine, in ordine di precedenza:

    1. `engine_root` passato esplicitamente;
    2. il file `mcp_engine.txt` accanto al `.uproject`;
    3. la ricerca automatica (variabile d'ambiente, Launcher, registro).

    `version` può essere un numero ("5.8") oppure l'identificativo GUID che
    Unreal scrive nel `.uproject` quando il progetto punta a una build
    registrata a mano — tipico dopo aver copiato e riaperto un progetto.
    """
    if engine_root:
        root = Path(engine_root).expanduser()
        editor = _editor_executable(root)
        if editor is None:
            raise LocalError(
                "In '%s' non c'è un eseguibile dell'editor: controlla il percorso "
                "del motore (deve essere la cartella che contiene Engine/)." % root
            )
        return EngineInstall(_version_from_root(root), str(root), str(editor), "esplicito", "")

    if project_dir is not None:
        forzato = _engine_from_override(Path(project_dir))
        if forzato is not None:
            return forzato

    engines = find_engines()
    if not engines:
        raise LocalError(
            "Nessuna installazione di Unreal Engine trovata. Tre modi per risolvere:\n"
            "  1. passa engine_root con il percorso del motore (es. C:\\Program Files\\Epic Games\\UE_5.8);\n"
            "  2. crea un file %s accanto al .uproject con dentro quel percorso;\n"
            "  3. imposta la variabile d'ambiente UE_MCP_ENGINE_DIRS con la cartella "
            "che contiene le versioni (es. C:\\Program Files\\Epic Games)." % ENGINE_OVERRIDE_FILE
        )
    if version is None:
        return engines[0]

    # Corrispondenza esatta sull'identificativo (GUID delle build registrate)
    for engine in engines:
        if engine.identifier and engine.identifier.lower() == version.lower():
            return engine

    for engine in engines:
        if engine.version == version or engine.version.startswith(version):
            return engine

    # Un GUID sconosciuto non è un errore fatale: se c'è un solo motore è quello.
    if version.startswith("{") and len(engines) == 1:
        return engines[0]

    disponibili = ", ".join(
        "%s%s" % (e.version, " [%s]" % e.identifier if e.identifier else "") for e in engines
    )
    raise LocalError("Engine %s non trovato. Disponibili: %s" % (version, disponibili))


def list_templates(engine: EngineInstall) -> list[dict]:
    """Template ufficiali presenti nell'installazione (TP_Blank, TP_ThirdPerson, ...)."""
    templates_dir = Path(engine.root) / "Templates"
    if not templates_dir.is_dir():
        return []
    out = []
    for folder in sorted(templates_dir.iterdir()):
        if not folder.is_dir():
            continue
        uprojects = list(folder.glob("*.uproject"))
        if not uprojects:
            continue
        out.append(
            {
                "name": folder.name,
                "path": str(folder),
                "has_source": (folder / "Source").is_dir(),
            }
        )
    return out


# ------------------------------------------------------------------ progetto


def _default_remote_control_ini(spec: dict) -> str:
    """Config/DefaultRemoteControl.ini.

    `URemoteControlSettings` è dichiarata `UCLASS(config = RemoteControl)` nel
    modulo RemoteControlCommon: le sue chiavi vanno in questo file, non in
    DefaultEngine.ini. Senza `bEnableRemotePythonExecution` la Remote Control API
    rifiuta le chiamate a PythonScriptLibrary con
    "Object ... cannot be accessed remotely" — cioè il bridge MCP non funziona.
    """
    return f"""[/Script/RemoteControlCommon.RemoteControlSettings]
; --- unreal-mcp ---
; Il server ascolta solo su 127.0.0.1: niente esposizione fuori dalla macchina.
bAutoStartWebServer=True
bAutoStartWebSocketServer=True
RemoteControlHttpServerPort={int(spec.get("remote_control_port", 30010))}

; Sblocca l'oggetto PythonScriptLibrary (FRemoteControlModule::CanBeAccessedRemotely).
bEnableRemotePythonExecution=True

; Sblocca la chiamata di funzione (WebRemoteControlInternalUtils::ValidateFunctionCall).
; Allowlist mirata: solo questa classe, invece di bAllowAnyRemoteFunctionCall=True
; che aprirebbe qualunque UFUNCTION del progetto alle chiamate HTTP.
bAllowAnyRemoteFunctionCall=False
+CustomAllowedRemoteFunctionCalls=(ClassPath="/Script/PythonScriptPlugin.PythonScriptLibrary")

; Lasciato disattivo di proposito: abilita ExecuteConsoleCommand *via web API*,
; che il bridge non usa mai. I comandi console che servono (LiveCoding.Compile,
; WebControl.StartServer, HighResShot) partono da dentro Python con
; unreal.SystemLibrary.execute_console_command e non passano da questo gate.
bAllowConsoleCommandRemoteExecution=False
"""


def _default_engine_ini(spec: dict) -> str:
    default_map = spec.get("default_map") or "/Engine/Maps/Templates/OpenWorld"
    return f"""[/Script/EngineSettings.GameMapsSettings]
EditorStartupMap={default_map}
GameDefaultMap={default_map}

[/Script/WindowsTargetPlatform.WindowsTargetSettings]
DefaultGraphicsRHI=DefaultGraphicsRHI_DX12

[/Script/Engine.RendererSettings]
r.DefaultFeature.AutoExposure=False
r.DynamicGlobalIlluminationMethod=1
r.ReflectionMethod=1
r.Shadow.Virtual.Enable=1

[/Script/Engine.PhysicsSettings]
DefaultGravityZ=-980.000000
"""


def _default_game_ini(spec: dict) -> str:
    game_mode = spec.get("default_game_mode")
    block = f"GlobalDefaultGameMode={game_mode}\n" if game_mode else ""
    return f"""[/Script/EngineSettings.GeneralProjectSettings]
ProjectName={spec["name"]}
ProjectVersion=0.1.0

[/Script/EngineSettings.GameMapsSettings]
{block}"""


def _uproject_content(engine: EngineInstall, plugins: list[str], description: str) -> dict:
    return {
        "FileVersion": 3,
        "EngineAssociation": engine.version,
        "Category": "",
        "Description": description,
        "Plugins": [{"Name": name, "Enabled": True} for name in plugins],
    }


def _copy_template(template_dir: Path, target: Path, blueprint_only: bool) -> list[str]:
    notes = []
    for item in template_dir.iterdir():
        if item.suffix == ".uproject" or item.name in {"Binaries", "Intermediate", "Saved", "DerivedDataCache"}:
            continue
        if blueprint_only and item.name == "Source":
            notes.append("cartella Source del template esclusa (progetto Blueprint-only)")
            continue
        destination = target / item.name
        if item.is_dir():
            shutil.copytree(item, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(item, destination)
    return notes


def create_project(
    name: str,
    directory: str,
    engine_version: str | None = None,
    template: str = "blank",
    blueprint_only: bool = True,
    plugins: list[str] | None = None,
    default_map: str | None = None,
    default_game_mode: str | None = None,
    description: str = "",
    force: bool = False,
    engine_root: str | None = None,
) -> dict:
    """Crea un progetto Unreal da specifica, con i plugin del bridge già attivi."""
    if not name.isidentifier():
        raise LocalError(
            "Nome progetto '%s' non valido: usa solo lettere, numeri e underscore, "
            "senza iniziare con un numero." % name
        )

    engine = resolve_engine(engine_version, engine_root)
    target = Path(directory).expanduser() / name

    if target.exists():
        if not force:
            raise LocalError(
                "La cartella %s esiste già. Usa force=True per riutilizzarla o scegli "
                "un'altra destinazione." % target
            )
    target.mkdir(parents=True, exist_ok=True)

    notes: list[str] = []
    if template and template.lower() != "blank":
        available = {t["name"].lower(): t for t in list_templates(engine)}
        chosen = available.get(template.lower()) or available.get(("tp_" + template).lower())
        if chosen is None:
            raise LocalError(
                "Template '%s' non trovato in %s. Disponibili: %s"
                % (template, engine.root, ", ".join(sorted(available)) or "nessuno")
            )
        notes.extend(_copy_template(Path(chosen["path"]), target, blueprint_only))
        notes.append("template %s copiato" % chosen["name"])

    for folder in ("Content", "Config"):
        (target / folder).mkdir(parents=True, exist_ok=True)

    enabled = list(dict.fromkeys(DEFAULT_PLUGINS + list(plugins or [])))
    uproject_path = target / f"{name}.uproject"
    uproject_path.write_text(
        json.dumps(_uproject_content(engine, enabled, description), indent=2) + "\n",
        encoding="utf-8",
    )

    spec = {
        "name": name,
        "default_map": default_map,
        "default_game_mode": default_game_mode,
    }
    engine_ini = target / "Config/DefaultEngine.ini"
    if engine_ini.exists():
        # template già fornito di config: aggiungiamo solo le sezioni nostre
        engine_ini.write_text(
            engine_ini.read_text(encoding="utf-8-sig").rstrip()
            + "\n\n"
            + _default_engine_ini(spec),
            encoding="utf-8",
        )
        notes.append("DefaultEngine.ini del template esteso")
    else:
        engine_ini.write_text(_default_engine_ini(spec), encoding="utf-8")

    # senza questo file il bridge riceve "cannot be accessed remotely"
    (target / "Config/DefaultRemoteControl.ini").write_text(
        _default_remote_control_ini(spec), encoding="utf-8"
    )

    game_ini = target / "Config/DefaultGame.ini"
    if not game_ini.exists():
        game_ini.write_text(_default_game_ini(spec), encoding="utf-8")

    # Avvio automatico del web server: ridondante rispetto alla config .ini, ma
    # rende il bridge indipendente dai nomi delle chiavi ini fra versioni diverse.
    python_dir = target / "Content/Python"
    python_dir.mkdir(parents=True, exist_ok=True)
    (python_dir / "init_unreal.py").write_text(INIT_UNREAL_SOURCE, encoding="utf-8")

    return {
        "project": name,
        "uproject": str(uproject_path),
        "root": str(target),
        "engine": engine.as_dict(),
        "plugins_enabled": enabled,
        "blueprint_only": blueprint_only,
        "notes": notes,
    }


def find_projects(directory: str, max_depth: int = 3) -> list[dict]:
    """Cerca file .uproject sotto una cartella."""
    base = Path(directory).expanduser()
    if not base.is_dir():
        raise LocalError("Cartella non trovata: %s" % base)
    out = []
    for depth in range(max_depth + 1):
        pattern = "/".join(["*"] * depth + ["*.uproject"]) if depth else "*.uproject"
        for path in base.glob(pattern):
            out.append({"name": path.stem, "uproject": str(path), "root": str(path.parent)})
    return out


def project_info(uproject: str) -> dict:
    path = Path(uproject).expanduser()
    if not path.exists():
        raise LocalError("File .uproject non trovato: %s" % path)
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    plugins = [p["Name"] for p in data.get("Plugins", []) if p.get("Enabled")]
    return {
        "name": path.stem,
        "uproject": str(path),
        "root": str(path.parent),
        "engine_association": data.get("EngineAssociation"),
        "plugins_enabled": plugins,
        "bridge_ready": {"PythonScriptPlugin", "RemoteControl"}.issubset(set(plugins)),
        "has_source": (path.parent / "Source").is_dir(),
    }


def set_project_plugins(uproject: str, enable: list[str], disable: list[str] | None = None) -> dict:
    """Abilita/disabilita plugin scrivendo direttamente nel .uproject."""
    path = Path(uproject).expanduser()
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    entries = {p["Name"]: p for p in data.get("Plugins", [])}
    for name in enable:
        entries[name] = {"Name": name, "Enabled": True}
    for name in disable or []:
        entries[name] = {"Name": name, "Enabled": False}
    data["Plugins"] = list(entries.values())
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return {
        "uproject": str(path),
        "plugins": [p["Name"] for p in data["Plugins"] if p["Enabled"]],
        "note": "restart the editor if it was already open",
    }


# --------------------------------------------------------------- codice C++

#: Prefisso della classe secondo la convenzione Unreal, per gerarchia.
#: `A` per gli attori, `U` per gli UObject e i componenti.
_ACTOR_PARENTS = {
    "Actor", "Pawn", "Character", "GameModeBase", "GameMode", "PlayerController",
    "AIController", "Controller", "GameStateBase", "PlayerState", "HUD",
    "DefaultPawn", "SpectatorPawn", "Info",
}

#: Header di dichiarazione delle classi base più usate. Non stanno tutte sotto
#: `GameFramework/`: sbagliare il percorso fa fallire la compilazione con un
#: errore che non nomina la classe, quindi la mappa è esplicita.
_PARENT_INCLUDES = {
    "Actor": "GameFramework/Actor.h",
    "Pawn": "GameFramework/Pawn.h",
    "Character": "GameFramework/Character.h",
    "DefaultPawn": "GameFramework/DefaultPawn.h",
    "SpectatorPawn": "GameFramework/SpectatorPawn.h",
    "GameModeBase": "GameFramework/GameModeBase.h",
    "GameMode": "GameFramework/GameMode.h",
    "GameStateBase": "GameFramework/GameStateBase.h",
    "PlayerState": "GameFramework/PlayerState.h",
    "PlayerController": "GameFramework/PlayerController.h",
    "Controller": "GameFramework/Controller.h",
    "HUD": "GameFramework/HUD.h",
    "Info": "GameFramework/Info.h",
    "AIController": "AIController.h",
    "ActorComponent": "Components/ActorComponent.h",
    "SceneComponent": "Components/SceneComponent.h",
    "StaticMeshComponent": "Components/StaticMeshComponent.h",
    "BoxComponent": "Components/BoxComponent.h",
    "SphereComponent": "Components/SphereComponent.h",
    "Object": "UObject/Object.h",
    "DataAsset": "Engine/DataAsset.h",
    "SaveGame": "GameFramework/SaveGame.h",
    "GameInstance": "Engine/GameInstance.h",
}

_TARGET_CS = """using UnrealBuildTool;

public class {name}Target : TargetRules
{{
    public {name}Target(TargetInfo Target) : base(Target)
    {{
        Type = TargetType.Game;
        DefaultBuildSettings = BuildSettingsVersion.V5;
        IncludeOrderVersion = EngineIncludeOrderVersion.Latest;
        ExtraModuleNames.Add("{module}");
    }}
}}
"""

_EDITOR_TARGET_CS = """using UnrealBuildTool;

public class {name}EditorTarget : TargetRules
{{
    public {name}EditorTarget(TargetInfo Target) : base(Target)
    {{
        Type = TargetType.Editor;
        DefaultBuildSettings = BuildSettingsVersion.V5;
        IncludeOrderVersion = EngineIncludeOrderVersion.Latest;
        ExtraModuleNames.Add("{module}");
    }}
}}
"""

_BUILD_CS = """using UnrealBuildTool;

public class {module} : ModuleRules
{{
    public {module}(ReadOnlyTargetRules Target) : base(Target)
    {{
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

        PublicDependencyModuleNames.AddRange(new string[] {{
            "Core", "CoreUObject", "Engine", "InputCore", "EnhancedInput"
        }});

        PrivateDependencyModuleNames.AddRange(new string[] {{ }});
    }}
}}
"""

_MODULE_H = """#pragma once

#include "CoreMinimal.h"
"""

_MODULE_CPP = """#include "{module}.h"
#include "Modules/ModuleManager.h"

IMPLEMENT_PRIMARY_GAME_MODULE(FDefaultGameModuleImpl, {module}, "{module}");
"""


def _strip_prefix(nome: str) -> str:
    """Toglie il prefisso Unreal solo se ne resta un nome noto.

    Non basta `lstrip("AU")`: `ActorComponent` inizia per A ma la classe è
    `UActorComponent`, e togliere la lettera darebbe `ctorComponent`.
    """
    if nome in _PARENT_INCLUDES or nome in _ACTOR_PARENTS:
        return nome
    if len(nome) > 1 and nome[0] in "AUF" and nome[1].isupper():
        candidato = nome[1:]
        if candidato in _PARENT_INCLUDES or candidato in _ACTOR_PARENTS:
            return candidato
    return nome


def _cpp_prefix(parent_class: str) -> str:
    """`A` per le classi che discendono da AActor, `U` per gli altri UObject."""
    return "A" if _strip_prefix(parent_class) in _ACTOR_PARENTS else "U"


def _qualified_parent(parent_class: str) -> str:
    """Nome C++ completo del parent, con il prefisso giusto."""
    nudo = _strip_prefix(parent_class)
    return _cpp_prefix(nudo) + nudo


#: Tipi puntatore a UObject citati in una firma: `TObjectPtr<AFoo>`, `AFoo*`,
#: `const UBar*`. Servono per generare le forward declaration.
_UOBJECT_REF = re.compile(r"\b(?:TObjectPtr\s*<\s*)?([AU][A-Z]\w*)\s*(?:>|\*)")


def _forward_declarations(tipi: list[str], escludi: set[str]) -> list[str]:
    """Forward declaration per le classi citate come puntatore.

    Un `TObjectPtr<APlayerState>` in un header non compila se la classe non è
    almeno dichiarata, e `CoreMinimal.h` non la porta dentro. Per un puntatore
    la forward declaration basta e non appesantisce le dipendenze.
    """
    trovate: list[str] = []
    for testo in tipi:
        for nome in _UOBJECT_REF.findall(testo or ""):
            if nome not in escludi and nome not in trovate:
                trovate.append(nome)
    return sorted(trovate)


def _render_property(prop: dict) -> tuple[str, str]:
    """Restituisce (blocco UPROPERTY, eventuale dichiarazione OnRep)."""
    nome = prop["name"]
    tipo = prop.get("type", "float")
    categoria = prop.get("category", "Gameplay")

    specificatori = ["EditAnywhere", "BlueprintReadWrite"]
    if prop.get("read_only"):
        specificatori = ["VisibleAnywhere", "BlueprintReadOnly"]

    on_rep = ""
    if prop.get("replicated"):
        if prop.get("rep_notify"):
            specificatori.append("ReplicatedUsing = OnRep_%s" % nome)
            on_rep = "    UFUNCTION()\n    void OnRep_%s();\n" % nome
        else:
            specificatori.append("Replicated")

    specificatori.append('Category = "%s"' % categoria)

    default = prop.get("default")
    inizializzatore = " = %s" % default if default is not None else ""

    blocco = "    UPROPERTY(%s)\n    %s %s%s;\n" % (
        ", ".join(specificatori), tipo, nome, inizializzatore,
    )
    return blocco, on_rep


def create_cpp_class(
    uproject: str,
    class_name: str,
    parent_class: str = "Actor",
    module: str | None = None,
    properties: list[dict] | None = None,
    functions: list[dict] | None = None,
    with_tick: bool = False,
    force: bool = False,
) -> dict:
    """Genera una classe C++ compilabile nel modulo del progetto.

    Il boilerplate di Unreal (macro API, GENERATED_BODY, Build.cs, Target.cs,
    IMPLEMENT_PRIMARY_GAME_MODULE) è la parte che si sbaglia più facilmente:
    qui viene generata corretta, e se il progetto è Blueprint-only il modulo
    C++ viene creato da zero.

    Le funzioni dichiarate come `BlueprintCallable` diventano chiamabili dai
    grafi Blueprint — è il modo per dare all'agente logica eseguibile pur non
    potendo scrivere i nodi.
    """
    path = Path(uproject).expanduser()
    if not path.exists():
        raise LocalError("File .uproject non trovato: %s" % path)
    if not class_name.isidentifier():
        raise LocalError(
            "Nome classe '%s' non valido: usa lettere, numeri e underscore, "
            "senza prefisso (viene aggiunto in automatico)." % class_name
        )

    nome_modulo = module or path.stem
    radice = path.parent
    source = radice / "Source"
    cartella_modulo = source / nome_modulo

    creati: list[str] = []
    note: list[str] = []

    # --- modulo C++ da zero se il progetto era Blueprint-only
    modulo_nuovo = not cartella_modulo.is_dir()
    if modulo_nuovo:
        cartella_modulo.mkdir(parents=True, exist_ok=True)
        for nome_file, contenuto in (
            (source / f"{path.stem}.Target.cs", _TARGET_CS.format(name=path.stem, module=nome_modulo)),
            (source / f"{path.stem}Editor.Target.cs", _EDITOR_TARGET_CS.format(name=path.stem, module=nome_modulo)),
            (cartella_modulo / f"{nome_modulo}.Build.cs", _BUILD_CS.format(module=nome_modulo)),
            (cartella_modulo / f"{nome_modulo}.h", _MODULE_H),
            (cartella_modulo / f"{nome_modulo}.cpp", _MODULE_CPP.format(module=nome_modulo)),
        ):
            if not nome_file.exists():
                nome_file.write_text(contenuto, encoding="utf-8")
                creati.append(str(nome_file))

        # Senza la voce Modules il .uproject resta Blueprint-only e il codice
        # non viene nemmeno compilato.
        dati = json.loads(path.read_text(encoding="utf-8-sig"))
        moduli = dati.setdefault("Modules", [])
        if not any(m.get("Name") == nome_modulo for m in moduli):
            moduli.append(
                {"Name": nome_modulo, "Type": "Runtime", "LoadingPhase": "Default"}
            )
            path.write_text(json.dumps(dati, indent=2) + "\n", encoding="utf-8")
        note.append(
            "modulo C++ '%s' creato: il progetto non era più Blueprint-only" % nome_modulo
        )

    # --- la classe
    prefisso = _cpp_prefix(parent_class)
    parent = _qualified_parent(parent_class)
    completo = prefisso + class_name

    header = cartella_modulo / f"{class_name}.h"
    implementazione = cartella_modulo / f"{class_name}.cpp"
    if header.exists() and not force:
        raise LocalError(
            "%s esiste già. Usa force=True per sovrascriverlo." % header
        )

    blocchi: list[str] = []
    on_reps: list[str] = []
    replicate = []
    for prop in properties or []:
        blocco, on_rep = _render_property(prop)
        blocchi.append(blocco)
        if on_rep:
            on_reps.append(on_rep)
        if prop.get("replicated"):
            replicate.append(prop["name"])

    dichiarazioni_funzioni = []
    definizioni_funzioni = []
    for funzione in functions or []:
        nome = funzione["name"]
        ritorno = funzione.get("return_type", "void")
        parametri = funzione.get("params", "")
        specificatori = funzione.get("specifiers", "BlueprintCallable, Category = \"Gameplay\"")
        corpo = funzione.get("body", "// TODO")
        dichiarazioni_funzioni.append(
            "    UFUNCTION(%s)\n    %s %s(%s);\n" % (specificatori, ritorno, nome, parametri)
        )
        definizioni_funzioni.append(
            "%s %s::%s(%s)\n{\n    %s\n}\n" % (ritorno, completo, nome, parametri, corpo)
        )

    api_macro = "%s_API" % nome_modulo.upper()
    e_attore = prefisso == "A"

    parent_nudo = _strip_prefix(parent)
    include_parent = _PARENT_INCLUDES.get(parent_nudo)
    if include_parent is None:
        include_parent = "GameFramework/%s.h" % parent_nudo
        note.append(
            "header del parent '%s' non in tabella: incluso come '%s', "
            "correggilo se la compilazione non lo trova." % (parent_nudo, include_parent)
        )

    citati = [p.get("type", "") for p in properties or []]
    citati += [f.get("params", "") for f in functions or []]
    citati += [f.get("return_type", "") for f in functions or []]
    forward = _forward_declarations(citati, escludi={completo, parent})

    corpo_header = [
        "#pragma once\n\n",
        '#include "CoreMinimal.h"\n',
        '#include "%s"\n' % include_parent,
        '#include "%s.generated.h"\n\n' % class_name,
    ]
    if forward:
        corpo_header.extend("class %s;\n" % nome for nome in forward)
        corpo_header.append("\n")
    corpo_header += [
        "UCLASS()\n",
        "class %s %s : public %s\n{\n" % (api_macro, completo, parent),
        "    GENERATED_BODY()\n\n",
        "public:\n",
        "    %s();\n\n" % completo,
    ]
    if blocchi:
        corpo_header.extend(blocchi)
        corpo_header.append("\n")
    if on_reps:
        corpo_header.extend(on_reps)
        corpo_header.append("\n")
    if dichiarazioni_funzioni:
        corpo_header.extend(dichiarazioni_funzioni)
        corpo_header.append("\n")
    if replicate:
        corpo_header.append(
            "    virtual void GetLifetimeReplicatedProps("
            "TArray<FLifetimeProperty>& OutLifetimeProps) const override;\n\n"
        )
    if e_attore:
        corpo_header.append("protected:\n    virtual void BeginPlay() override;\n")
        if with_tick:
            corpo_header.append("\npublic:\n    virtual void Tick(float DeltaTime) override;\n")
    corpo_header.append("};\n")

    corpo_cpp = ['#include "%s.h"\n' % class_name]
    # Nell'header la forward declaration basta; nel .cpp il corpo di una
    # funzione può volerne i membri, quindi si include per esteso quando
    # l'header della classe è noto.
    for nome in forward:
        header_noto = _PARENT_INCLUDES.get(_strip_prefix(nome))
        if header_noto:
            corpo_cpp.append('#include "%s"\n' % header_noto)
    if replicate:
        corpo_cpp.append('#include "Net/UnrealNetwork.h"\n')
    corpo_cpp.append("\n")
    corpo_cpp.append("%s::%s()\n{\n" % (completo, completo))
    if e_attore:
        corpo_cpp.append("    PrimaryActorTick.bCanEverTick = %s;\n" % ("true" if with_tick else "false"))
    if replicate:
        corpo_cpp.append("    bReplicates = true;\n")
    corpo_cpp.append("}\n\n")

    if e_attore:
        corpo_cpp.append("void %s::BeginPlay()\n{\n    Super::BeginPlay();\n}\n\n" % completo)
        if with_tick:
            corpo_cpp.append(
                "void %s::Tick(float DeltaTime)\n{\n    Super::Tick(DeltaTime);\n}\n\n" % completo
            )

    if replicate:
        corpo_cpp.append(
            "void %s::GetLifetimeReplicatedProps("
            "TArray<FLifetimeProperty>& OutLifetimeProps) const\n{\n"
            "    Super::GetLifetimeReplicatedProps(OutLifetimeProps);\n" % completo
        )
        for nome_prop in replicate:
            corpo_cpp.append("    DOREPLIFETIME(%s, %s);\n" % (completo, nome_prop))
        corpo_cpp.append("}\n\n")

    for on_rep in on_reps:
        nome_prop = on_rep.split("OnRep_")[1].split("(")[0]
        corpo_cpp.append("void %s::OnRep_%s()\n{\n}\n\n" % (completo, nome_prop))

    corpo_cpp.extend(definizioni_funzioni)

    header.write_text("".join(corpo_header), encoding="utf-8")
    implementazione.write_text("".join(corpo_cpp), encoding="utf-8")
    creati.extend([str(header), str(implementazione)])

    return {
        "class": completo,
        "parent": parent,
        "module": nome_modulo,
        "header": str(header),
        "source": str(implementazione),
        "files_created": creati,
        "module_created": modulo_nuovo,
        "replicated_properties": replicate,
        "notes": note,
        "next_steps": [
            "ue_editor_close",
            "ue_build_start",
            "ue_build_status (until running=false)",
            "ue_editor_open",
            "ue_reparent_blueprint (to attach a Blueprint to %s)" % completo,
        ],
    }


# ------------------------------------------------------- ciclo di vita editor


BUILD_STATE_FILE = STATE_DIR / "build.json"


def _save_job(state_file: Path, uproject: str, state: dict) -> None:
    """Registra lo stato di un job, indicizzato per progetto.

    Uno slot solo significava che due progetti compilati in parallelo — o due
    client MCP sulla stessa macchina — si sovrascrivevano lo stato a vicenda, e
    il secondo ue_build_status rispondeva sul build sbagliato.
    """
    jobs = _load_jobs(state_file)
    jobs[str(uproject)] = state
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps({"jobs": jobs, "last": str(uproject)}, indent=2), encoding="utf-8"
    )


def _load_jobs(state_file: Path) -> dict:
    try:
        raw = json.loads(state_file.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    if isinstance(raw, dict) and "jobs" in raw:
        return raw.get("jobs") or {}
    # Formato precedente: un singolo stato in cima al file.
    if isinstance(raw, dict) and raw.get("uproject"):
        return {str(raw["uproject"]): raw}
    return {}


def _load_job(state_file: Path, uproject: str | None) -> dict | None:
    """Stato di un job. Senza `uproject` restituisce il più recente."""
    try:
        raw = json.loads(state_file.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    jobs = _load_jobs(state_file)
    if not jobs:
        return None
    if uproject:
        chiave = str(Path(uproject).expanduser())
        return jobs.get(chiave) or jobs.get(str(uproject))
    ultimo = raw.get("last") if isinstance(raw, dict) else None
    if ultimo and ultimo in jobs:
        return jobs[ultimo]
    return max(jobs.values(), key=lambda j: float(j.get("started_at", 0)))


def _batch_file(engine: EngineInstall, stem: str) -> Path:
    """Script di build del motore per la piattaforma corrente.

    Epic distribuisce `Build.bat`/`RunUAT.bat` su Windows e gli equivalenti
    `.sh` su Linux/macOS, nella stessa cartella.
    """
    cartella = Path(engine.root) / "Engine/Build/BatchFiles"
    if platform.system() == "Windows":
        candidati = [cartella / f"{stem}.bat"]
    else:
        # Su macOS gli script stanno in una sottocartella Mac/, su Linux in Linux/.
        sistema = "Mac" if platform.system() == "Darwin" else "Linux"
        candidati = [cartella / sistema / f"{stem}.sh", cartella / f"{stem}.sh"]

    for candidato in candidati:
        if candidato.exists():
            return candidato

    raise LocalError(
        "Script %s non trovato per questa piattaforma. Cercato in: %s"
        % (stem, ", ".join(str(c) for c in candidati))
    )


#: Configurazioni di build accettate da UnrealBuildTool / BuildCookRun.
VALID_CONFIGURATIONS = frozenset({"Debug", "DebugGame", "Development", "Test", "Shipping"})

#: Piattaforme di destinazione accettate.
VALID_PLATFORMS = frozenset(
    {"Win64", "Win32", "Linux", "LinuxArm64", "Mac", "Android", "IOS", "TVOS"}
)

#: Un target UBT è un identificatore C++: niente spazi, niente metacaratteri.
_TARGET_RE = re.compile(r"^[A-Za-z0-9_]+$")

#: Un path del Content Browser: /Game/..., lettere, cifre, _ - . e /
_UE_PATH_RE = re.compile(r"^/[A-Za-z0-9_./-]+$")


def _check_choice(nome: str, valore: str, ammessi: frozenset[str]) -> str:
    """Verifica che `valore` sia uno dei valori ammessi.

    Questi argomenti vengono interpolati nel corpo di uno script .bat/.sh: senza
    controllo, un `configuration="Development & qualcosa"` diventerebbe un
    comando in più eseguito dalla shell. Una allowlist è più semplice — e più
    robusta — di qualsiasi tentativo di quoting portabile fra cmd.exe e sh.
    """
    if valore not in ammessi:
        raise LocalError(
            "%s non valido: %r. Valori ammessi: %s."
            % (nome, valore, ", ".join(sorted(ammessi)))
        )
    return valore


def _check_token(nome: str, valore: str, pattern: re.Pattern[str]) -> str:
    """Come :func:`_check_choice`, ma per valori liberi con una forma nota."""
    if not pattern.match(valore):
        raise LocalError(
            "%s non valido: %r. Ammessi solo i caratteri della forma %s."
            % (nome, valore, pattern.pattern)
        )
    return valore


def _check_path_arg(nome: str, valore: Path) -> Path:
    """Rifiuta i percorsi che romperebbero il quoting dello script."""
    testo = str(valore)
    if '"' in testo or "\n" in testo or "\r" in testo:
        raise LocalError(
            "%s contiene virgolette o a capo e non può essere passato alla shell: %r"
            % (nome, testo)
        )
    return valore


def _invoke(comando: str) -> str:
    """Prefissa `call` solo dove serve: è sintassi di cmd.exe, non di sh."""
    return ("call " + comando) if platform.system() == "Windows" else comando


def default_target_platform() -> str:
    """Piattaforma di build predefinita, dedotta dal sistema corrente."""
    return {"Windows": "Win64", "Darwin": "Mac", "Linux": "Linux"}.get(
        platform.system(), "Win64"
    )

#: Righe che indicano un vero problema di compilazione nel log di UnrealBuildTool.
_BUILD_ERROR_MARKERS = ("error C", "error LNK", "error :", ": error", "Error:", "fatal error")


def start_build(
    uproject: str,
    engine_version: str | None = None,
    target: str | None = None,
    platform: str = "Win64",
    configuration: str = "Development",
    engine_root: str | None = None,
) -> dict:
    """Avvia la compilazione del modulo C++ in background.

    Non attende: una compilazione completa dura minuti, ben oltre il timeout di
    una singola chiamata MCP. Lo stato si consulta con :func:`build_status`.

    L'editor deve essere **chiuso**: con Live Coding attivo UnrealBuildTool
    rifiuta di scrivere le DLL, e per un modulo nuovo Live Coding non basta
    (può solo applicare patch a binari già esistenti).
    """
    path = Path(uproject).expanduser()
    if not path.exists():
        raise LocalError("File .uproject non trovato: %s" % path)
    if not (path.parent / "Source").is_dir():
        raise LocalError(
            "Il progetto non ha una cartella Source: non c'è codice C++ da compilare."
        )

    status = editor_status()
    if status.get("running") or status.get("editor_process_detected"):
        raise LocalError(
            "L'editor Unreal è ancora aperto: con Live Coding attivo la compilazione "
            "fallisce. Chiudilo con ue_editor_close e riprova."
        )

    # Il processo di console di Live Coding sopravvive alla chiusura dell'editor e
    # continua a tenere il lock sulle DLL: senza questo, il build fallisce con
    # "Unable to build while Live Coding is active" a editor già chiuso.
    terminate_process_by_name("LiveCodingConsole.exe")

    engine = resolve_engine(
        engine_version or project_info(str(path)).get("engine_association"),
        engine_root,
        path.parent,
    )
    build_bat = _batch_file(engine, "Build")

    target_name = _check_token("target", target or f"{path.stem}Editor", _TARGET_RE)
    platform_name = _check_choice(
        "platform", platform or default_target_platform(), VALID_PLATFORMS
    )
    _check_choice("configuration", configuration, VALID_CONFIGURATIONS)
    _check_path_arg("uproject", path)
    _check_path_arg("Build script", build_bat)

    log_path = path.parent / "Saved/Logs/mcp_build.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.exists():
        log_path.unlink()

    comando = _invoke(
        f'"{build_bat}" {target_name} {platform_name} '
        f'{configuration} -Project="{path}" -WaitMutex'
    )
    _script, args = _write_launch_script(
        path.parent / "Saved" / "mcp_build_run", comando, log_path
    )

    kwargs: dict[str, Any] = {"cwd": str(path.parent)}
    if platform_module_is_windows():
        kwargs["creationflags"] = 0x00000008  # DETACHED_PROCESS

    process = subprocess.Popen(args, **kwargs)  # noqa: S603
    state = {
        "pid": process.pid,
        "uproject": str(path),
        "target": target_name,
        "log": str(log_path),
        "started_at": time.time(),
    }
    _save_job(BUILD_STATE_FILE, str(path), state)
    return {**state, "note": "build started: poll ue_build_status"}


def platform_module_is_windows() -> bool:
    return platform.system() == "Windows"


def build_status(tail_lines: int = 30, uproject: str | None = None) -> dict:
    """Stato della compilazione avviata da :func:`start_build`.

    Senza `uproject` risponde sull'ultima compilazione avviata.
    """
    state = _load_job(BUILD_STATE_FILE, uproject)
    if state is None:
        return {"running": False, "reason": "nessuna compilazione avviata da questo MCP"}

    log_path = Path(state.get("log", ""))
    lines: list[str] = []
    if log_path.exists():
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()

    running = _process_alive(int(state.get("pid", 0)))
    errors = [r.strip() for r in lines if any(m in r for m in _BUILD_ERROR_MARKERS)]
    warnings = [r.strip() for r in lines if "warning" in r.lower()]

    exit_code = None
    for line in reversed(lines):
        if line.startswith("EXITCODE="):
            exit_code = line.split("=", 1)[1].strip()
            break

    succeeded = (not running) and not errors and any(
        "Total execution time" in r or "Build succeeded" in r or r.startswith("EXITCODE=0")
        for r in lines
    )

    return {
        "running": running,
        "uproject": state.get("uproject"),
        "target": state.get("target"),
        "log": str(log_path),
        "elapsed_seconds": round(time.time() - float(state.get("started_at", time.time())), 1),
        "exit_code": exit_code,
        "succeeded": succeeded,
        "errors": errors[:20],
        "warnings": warnings[:10],
        "tail": lines[-int(tail_lines):],
    }


PACKAGE_STATE_FILE = STATE_DIR / "package.json"

#: Righe che segnalano un fallimento in un log di AutomationTool.
_PACKAGE_ERROR_MARKERS = (
    "ERROR:", "BUILD FAILED", "AutomationException", "Cook failed",
    "error C", "error LNK", "fatal error",
)


def _write_launch_script(
    destination: Path, body: str, log_path: Path
) -> tuple[Path, list[str]]:
    """Scrive uno script che esegue `body` e ne registra l'esito nel log.

    Si passa da un file invece che da `cmd /c "..."`: i percorsi contengono
    spazi e la citazione annidata attraverso cmd è fragile — si perde la
    redirezione e il log resta vuoto.
    """
    if platform.system() == "Windows":
        script = destination.with_suffix(".bat")
        script.write_text(
            "@echo off\r\n"
            f'{body} > "{log_path}" 2>&1\r\n'
            f'echo EXITCODE=%ERRORLEVEL% >> "{log_path}"\r\n',
            encoding="utf-8",
        )
        return script, [str(script)]

    script = destination.with_suffix(".sh")
    script.write_text(
        "#!/bin/sh\n"
        f'{body} > "{log_path}" 2>&1\n'
        f'echo "EXITCODE=$?" >> "{log_path}"\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script, [str(script)]


def start_package(
    uproject: str,
    engine_version: str | None = None,
    configuration: str = "Development",
    platform_name: str = "Win64",
    maps: list[str] | None = None,
    output_dir: str | None = None,
    dedicated_server: bool = False,
    engine_root: str | None = None,
) -> dict:
    """Avvia cook + package del gioco in background (RunUAT BuildCookRun).

    Produce un eseguibile autonomo, avviabile senza l'editor. È un'operazione
    lunga (decine di minuti la prima volta, molto meno dopo grazie alla cache
    del cook): come per la compilazione non si attende, si consulta
    :func:`package_status`.
    """
    path = Path(uproject).expanduser()
    if not path.exists():
        raise LocalError("File .uproject non trovato: %s" % path)

    _check_choice("configuration", configuration, VALID_CONFIGURATIONS)
    _check_choice("target_platform", platform_name, VALID_PLATFORMS)
    _check_path_arg("uproject", path)
    for mappa in maps or []:
        _check_token("maps", mappa, _UE_PATH_RE)

    status = editor_status()
    if status.get("running") or status.get("editor_process_detected"):
        raise LocalError(
            "L'editor Unreal è aperto: il cook userebbe asset ancora in memoria e "
            "il build fallirebbe sui binari bloccati. Chiudilo con ue_editor_close."
        )
    terminate_process_by_name("LiveCodingConsole.exe")

    engine = resolve_engine(
        engine_version or project_info(str(path)).get("engine_association"),
        engine_root,
        path.parent,
    )
    run_uat = _batch_file(engine, "RunUAT")

    archive_dir = Path(output_dir).expanduser() if output_dir else path.parent / "Packaged"
    _check_path_arg("output_dir", archive_dir)
    _check_path_arg("RunUAT script", run_uat)
    archive_dir.mkdir(parents=True, exist_ok=True)

    log_path = path.parent / "Saved/Logs/mcp_package.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.exists():
        log_path.unlink()

    argomenti = [
        f'"{run_uat}"', "BuildCookRun",
        f'-project="{path}"',
        "-noP4", "-utf8output",
        f"-platform={platform_name}",
        f"-clientconfig={configuration}",
        "-cook", "-build", "-stage", "-pak", "-archive",
        f'-archivedirectory="{archive_dir}"',
    ]
    # Cuocere solo le mappe che servono: senza questo elenco il cook prende
    # tutte quelle referenziate dalle impostazioni di progetto, e su un progetto
    # con molti livelli il tempo si allunga parecchio senza motivo.
    for mappa in maps or []:
        argomenti.append(f"-map={mappa}")
    if dedicated_server:
        argomenti += ["-server", f"-serverconfig={configuration}"]

    _script, args = _write_launch_script(
        path.parent / "Saved" / "mcp_package_run", _invoke(" ".join(argomenti)), log_path
    )

    kwargs: dict[str, Any] = {"cwd": str(path.parent)}
    if platform.system() == "Windows":
        kwargs["creationflags"] = 0x00000008  # DETACHED_PROCESS

    process = subprocess.Popen(args, **kwargs)  # noqa: S603
    state = {
        "pid": process.pid,
        "uproject": str(path),
        "configuration": configuration,
        "platform": platform_name,
        "archive": str(archive_dir),
        "log": str(log_path),
        "started_at": time.time(),
    }
    _save_job(PACKAGE_STATE_FILE, str(path), state)
    return {**state, "note": "packaging started: poll ue_package_status"}


def package_status(tail_lines: int = 30, uproject: str | None = None) -> dict:
    """Stato del packaging avviato da :func:`start_package`.

    Senza `uproject` risponde sull'ultimo packaging avviato.
    """
    state = _load_job(PACKAGE_STATE_FILE, uproject)
    if state is None:
        return {"running": False, "reason": "nessun packaging avviato da questo MCP"}

    log_path = Path(state.get("log", ""))
    lines: list[str] = []
    if log_path.exists():
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()

    running = _process_alive(int(state.get("pid", 0)))
    errors = [r.strip() for r in lines if any(m in r for m in _PACKAGE_ERROR_MARKERS)]

    exit_code = None
    for line in reversed(lines):
        if line.startswith("EXITCODE="):
            exit_code = line.split("=", 1)[1].strip()
            break

    succeeded = (not running) and exit_code == "0"

    # L'eseguibile prodotto, se c'è
    archive = Path(state.get("archive", ""))
    executables = [str(p) for p in archive.rglob("*.exe")][:5] if archive.exists() else []

    # Fase corrente, per capire a che punto è: UAT le annuncia nel log
    fase = None
    for line in reversed(lines):
        for nome in ("Archive", "Package", "Stage", "Cook", "Compile", "Build"):
            if "********** %s" % nome.upper() in line:
                fase = nome
                break
        if fase:
            break

    return {
        "running": running,
        "configuration": state.get("configuration"),
        "phase": fase,
        "log": str(log_path),
        "elapsed_seconds": round(time.time() - float(state.get("started_at", time.time())), 1),
        "exit_code": exit_code,
        "succeeded": succeeded,
        "archive": str(archive),
        "executables": executables,
        "errors": errors[:15],
        "tail": lines[-int(tail_lines):],
    }


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if platform.system() == "Windows":
            output = subprocess.run(  # noqa: S603
                ["tasklist", "/FI", "PID eq %d" % pid],
                capture_output=True, text=True, timeout=15,
            ).stdout
            return str(pid) in output
        os.kill(pid, 0)
        return True
    except Exception:  # noqa: BLE001
        return False


def launch_editor(
    uproject: str,
    engine_version: str | None = None,
    extra_args: list[str] | None = None,
    engine_root: str | None = None,
) -> dict:
    """Avvia l'editor su un progetto, con il web server Remote Control attivo."""
    path = Path(uproject).expanduser()
    if not path.exists():
        raise LocalError("File .uproject non trovato: %s" % path)

    info = project_info(str(path))
    if not info["bridge_ready"]:
        raise LocalError(
            "Il progetto non ha i plugin del bridge abilitati (%s). "
            "Chiama prima ue_project_set_plugins con "
            "['PythonScriptPlugin', 'RemoteControl']."
            % ", ".join(info["plugins_enabled"]) or "nessuno"
        )

    engine = resolve_engine(
        engine_version or info.get("engine_association"), engine_root, path.parent
    )
    args = [engine.editor, str(path), "-RCWebControlEnable", "-RCWebInterfaceEnable"]
    args += extra_args or []

    kwargs: dict[str, Any] = {"cwd": str(path.parent)}
    if platform.system() == "Windows":
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    process = subprocess.Popen(args, **kwargs)  # noqa: S603
    _save_state({"pid": process.pid, "uproject": str(path), "started_at": time.time()})
    return {
        "pid": process.pid,
        "uproject": str(path),
        "engine": engine.as_dict(),
        "command": args,
    }


def _process_name_variants(name: str) -> list[str]:
    """Nomi con cui lo stesso processo compare sulle varie piattaforme.

    Su Windows è `UnrealEditor.exe`, su Linux/macOS `UnrealEditor` — chi chiama
    usa il nome Windows, qui si normalizza.
    """
    base = name[:-4] if name.lower().endswith(".exe") else name
    return [name, base] if base != name else [name]


def process_running_by_name(name: str) -> bool:
    """Un processo con questo nome è in esecuzione?"""
    varianti = _process_name_variants(name)
    try:
        if platform.system() == "Windows":
            output = subprocess.run(  # noqa: S603
                ["tasklist", "/FI", "IMAGENAME eq %s" % name],
                capture_output=True, text=True, timeout=20,
            )
            testo = ((output.stdout or "") + (output.stderr or "")).lower()
            return any(v.lower() in testo for v in varianti)

        # Senza questo, su Linux/macOS il controllo "editor chiuso" prima di una
        # compilazione passava sempre, e il build falliva più avanti.
        for variante in varianti:
            output = subprocess.run(  # noqa: S603
                ["pgrep", "-f", variante], capture_output=True, text=True, timeout=20
            )
            if output.returncode == 0 and (output.stdout or "").strip():
                return True
        return False
    except Exception:  # noqa: BLE001
        return False


def terminate_process_by_name(name: str) -> bool:
    """Termina un processo per nome. Ritorna True se ce n'era uno da chiudere."""
    if not process_running_by_name(name):
        return False
    try:
        if platform.system() == "Windows":
            subprocess.run(  # noqa: S603
                ["taskkill", "/F", "/IM", name], capture_output=True, timeout=30
            )
        else:
            for variante in _process_name_variants(name):
                subprocess.run(  # noqa: S603
                    ["pkill", "-f", variante], capture_output=True, timeout=30
                )
        return True
    except Exception:  # noqa: BLE001
        return False


def editor_status() -> dict:
    """Stato dell'editor.

    Oltre al processo lanciato da noi, rileva un editor aperto a mano: senza
    questo controllo la compilazione partirebbe comunque, per poi fallire.
    """
    state = _load_state()
    pid = int(state.get("pid", 0))
    return {
        "pid": pid or None,
        "uproject": state.get("uproject"),
        "running": _process_alive(pid) if pid else False,
        "editor_process_detected": process_running_by_name("UnrealEditor.exe"),
        "live_coding_detected": process_running_by_name("LiveCodingConsole.exe"),
    }


def kill_editor(timeout: float = 30.0) -> dict:
    """Termina il processo editor avviato da noi (fallback se il quit pulito fallisce)."""
    state = _load_state()
    pid = int(state.get("pid", 0))
    if not pid or not _process_alive(pid):
        return {"killed": False, "reason": "nessun editor avviato da questo MCP risulta attivo"}

    if platform.system() == "Windows":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=30)  # noqa: S603
    else:
        os.kill(pid, 15)

    deadline = time.time() + timeout
    while time.time() < deadline and _process_alive(pid):
        time.sleep(0.5)

    _save_state({})
    return {"killed": True, "pid": pid, "still_alive": _process_alive(pid)}

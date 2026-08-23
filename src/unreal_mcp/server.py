"""Server MCP per Unreal Engine 5.

Ogni tool traduce la richiesta in uno snippet Python eseguito dentro l'editor
tramite la Remote Control API. Vedi README.md per l'attivazione dei plugin.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import inspect
import json
import os
import sys
from pathlib import Path
from typing import Any, TypedDict

from mcp.server.fastmcp import Context, FastMCP, Image
from mcp.server.transport_security import TransportSecuritySettings

from . import assets, local, ui
from . import flow as flow_engine
from .bridge import (
    BridgeConfig,
    UnrealBridge,
    UnrealBridgeError,
    UnrealNotConnected,
    UnrealPythonError,
)

mcp = FastMCP(
    # `name` posizionale, tutto il resto per keyword. In mcp 2 il costruttore
    # ha inserito `title` e `description` prima di `instructions`: passarle per
    # posizione le farebbe finire nel titolo in silenzio, e il modello non le
    # riceverebbe più. Vale la pena rispettarlo anche restando sulla 1.x, così
    # una futura migrazione non cambia questa riga.
    "unreal-mcp",
    instructions=(
        "Drive Unreal Engine 5 on two levels: "
        "(1) LOCAL — find engine installs, create projects from a spec, open/close the editor, "
        "compile C++, package the game, download free assets, install purchased Fab packs "
        "(ue_engine_*, ue_project_*, ue_editor_*, ue_build_*, ue_package_*, preset_*); "
        "(2) EDITOR — drive the running editor over the Remote Control API (every other tool). "
        "Cold start: ue_engine_list -> ue_project_create -> ue_editor_open -> ue_status. "
        "On a running editor, always call ue_status first. "
        "Asset paths follow the Unreal convention (/Game/...); positions are in centimetres "
        "(1 unit = 1 cm) with Z up. Compiling C++ needs the editor closed (ue_build_start), "
        "unless the change only touches function bodies (ue_live_compile). "
        "Blueprint node graphs ARE scriptable on UE 5.8+ (ue_bp_add_call_function, "
        "ue_bp_connect, ue_bp_set_pin_value, ue_bp_graph_info; check "
        "ue_status capabilities.blueprint_graph_authoring first) — nodes are addressed "
        "by object name, and event nodes by the alias event:ReceiveBeginPlay. On older "
        "engines, put logic in a C++ parent class instead "
        "(ue_cpp_class_create -> build -> ue_reparent_blueprint). UMG layout is "
        "scriptable too (ue_umg_add_widget, ue_umg_set_slot), but only under a root "
        "widget that already exists — an empty tree cannot get its first widget from "
        "Python. Niagara emitter stacks and EQS remain unscriptable. Material graphs, Behavior "
        "Trees and PCG graphs are fully scriptable "
        "(ue_create_material, ue_bt_add_node, ue_pcg_add_node/ue_pcg_connect). "
        "A landscape cannot be created from Python — it has to exist already "
        "(Landscape Mode in the editor); ue_landscape_* then drives it. "
        "Foliage and Level Sequences are fully authorable "
        "(ue_foliage_scatter/ue_foliage_query, ue_sequence_add_track/ue_sequence_add_key); "
        "for sequences, name channels without their numeric suffix ('Location.Z', not "
        "'Location.Z_3' — the suffix is unstable) and address tracks by type or index, "
        "because display names are localised. "
        "When the same scene takes ten or twenty calls, write them once as a YAML flow "
        "and run ue_flow_run (dry_run=True first) instead of spending a turn per call. "
        "Do not assume the action is at the world origin: real levels are often built "
        "thousands of units away from [0,0,0], so an actor spawned there can be "
        "off-screen and invisible. Anchor new actors to what is already in the scene — "
        "read a reference actor with ue_list_actors, or the camera with ue_get_camera, "
        "and place relative to that. "
        "Verify visually: ue_focus_actor on what you touched, then ue_screenshot, which "
        "returns the viewport as an image — look at it before reporting success. "
        "Use ue_spawn_many when placing more than a few actors. "
        "Purchased Fab/Marketplace content: preset_fab_status -> preset_fab_list_vault -> "
        "preset_fab_install, which downloads the pack and copies it into the project's "
        "Content/Plugins folders, then rescans the Asset Registry. preset_fab_install also "
        "accepts a folder or zip already on disk, for packs downloaded by hand from the "
        "Epic Games Launcher or the editor's Fab window."
    ),
)

_bridge = UnrealBridge(BridgeConfig.from_env())


def lit(value: Any) -> str:
    """Serializza un valore in un literal Python sicuro da inserire in uno snippet.

    Si usa ``repr`` e non ``json.dumps`` perché il risultato deve essere codice
    Python valido (``None``/``True``, non ``null``/``true``).
    """
    return repr(value)


async def run(code: str) -> Any:
    """Esegue lo snippet nell'editor traducendo gli errori in messaggi utili."""
    try:
        return await _bridge.run(code)
    except UnrealPythonError as exc:
        raise RuntimeError(
            f"Errore Python dentro l'editor Unreal: {exc}\n\n"
            f"Traceback:\n{exc.traceback_text[-1500:]}"
        ) from exc
    except UnrealBridgeError as exc:
        raise RuntimeError(str(exc)) from exc


async def local_call(func, *args, **kwargs):
    """Esegue un'operazione locale su un thread, traducendo LocalError/AssetError.

    ## Perche' `to_thread` e non una chiamata diretta

    `func` e' codice **bloccante**: `subprocess.run` su UnrealBuildTool, letture
    di log da centinaia di MB, `legendary` che parla con i server Epic. Chiamarlo
    dentro una coroutine lo esegue sull'event loop, e finche' non ritorna il
    server **non risponde a nessun altro tool** — non e' il singolo tool a essere
    lento, e' tutto fermo. Con 24 tool che passano di qui, un solo
    `ue_build_status` su un log grosso bastava a far scadere per timeout
    chiamate che non c'entravano niente.

    `bridge.py` faceva gia' la cosa giusta con `asyncio.to_thread`: questa e' la
    meta' che mancava.
    """
    try:
        return await asyncio.to_thread(func, *args, **kwargs)
    except (local.LocalError, assets.AssetError) as exc:
        raise RuntimeError(str(exc)) from exc


async def _progress(ctx: Context | None, fatto: float, totale: float, messaggio: str) -> None:
    """Notifica di avanzamento, se il client la supporta.

    `report_progress` fallisce quando la richiesta non ha un progressToken (il
    client non l'ha chiesto): non è un errore, è la norma su metà dei client.
    """
    if ctx is None:
        return
    with contextlib.suppress(Exception):
        await ctx.report_progress(fatto, totale, messaggio)


async def _attendi_job(
    leggi_stato,
    wait_seconds: float,
    ctx: Context | None,
    etichetta: str,
) -> dict:
    """Polla lo stato di un job locale finché finisce o scade l'attesa.

    Senza questo l'agente deve richiamare ue_*_status a mano decine di volte su
    un'operazione che dura minuti, e ogni giro costa un round-trip e del
    contesto. Qui il polling avviene dentro una sola chiamata e l'avanzamento
    arriva al client come notifica.
    """
    scadenza = asyncio.get_event_loop().time() + wait_seconds
    async def stato_ora() -> dict:
        """Legge lo stato, che `leggi_stato` sia sincrona o una coroutine.

        Da quando `local_call` e' passata a `asyncio.to_thread`, le closure che
        i tool build/package/render passano qui sono `async def`. Ma
        `_attendi_job` prende una callable qualsiasi, e chi la chiama con una
        funzione normale ha ragione: **il contratto non e' cambiato**, si e'
        allargato. Piegare i chiamanti per un dettaglio interno sarebbe stato il
        verso sbagliato.
        """
        esito = leggi_stato()
        return await esito if inspect.isawaitable(esito) else esito

    stato = await stato_ora()
    await _progress(ctx, 0, wait_seconds, "%s: avviato" % etichetta)

    while stato.get("running") and asyncio.get_event_loop().time() < scadenza:
        await asyncio.sleep(min(5.0, max(1.0, wait_seconds / 60)))
        stato = await stato_ora()
        trascorso = stato.get("elapsed_seconds") or 0
        await _progress(
            ctx,
            min(float(trascorso), wait_seconds),
            wait_seconds,
            "%s: %s" % (etichetta, stato.get("phase") or ("in corso" if stato.get("running") else "concluso")),
        )

    if stato.get("running"):
        stato["note"] = (
            "Ancora in corso dopo %.0f s di attesa: richiama con wait_seconds "
            "più alto, o senza per una lettura istantanea." % wait_seconds
        )
    return stato


# ========================================== livello LOCALE: motore e progetti


@mcp.tool()
async def ue_engine_list() -> dict:
    """Elenca le installazioni di Unreal Engine trovate sulla macchina.

    Cerca in: variabile UE_MCP_ENGINE_DIRS, elenco dell'Epic Games Launcher,
    registro di Windows. Da chiamare per prima quando non c'è ancora un progetto.
    """
    engines = await local_call(local.find_engines)
    return {
        "count": len(engines),
        "engines": [e.as_dict() for e in engines],
        "default": engines[0].as_dict() if engines else None,
    }


@mcp.tool()
async def ue_engine_templates(
    engine_version: str | None = None, engine_root: str | None = None
) -> dict:
    """Elenca i template ufficiali disponibili nell'installazione (TP_Blank, TP_ThirdPerson, ...)."""
    engine = await local_call(local.resolve_engine, engine_version, engine_root)
    return {"engine": engine.version, "templates": await local_call(local.list_templates, engine)}


@mcp.tool()
async def ue_project_create(
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
    """Crea un progetto Unreal da specifica, pronto per il bridge MCP.

    Scrive `.uproject` (con `PythonScriptPlugin`, `RemoteControl` e `Metasound`
    già abilitati), `Config/DefaultEngine.ini` con l'autostart del web server
    Remote Control, `Config/DefaultGame.ini` e `Content/Python/init_unreal.py`.

    Args:
        name: nome progetto (identificatore valido, es. "MyGame").
        directory: cartella genitore, es. "C:/UnrealProjects".
        engine_version: es. "5.4"; se omesso usa la più recente installata.
        template: "blank" (generato da zero) o il nome di un template del motore
            (vedi ue_engine_templates), es. "TP_ThirdPerson".
        blueprint_only: esclude la cartella Source del template (niente compilazione C++).
        plugins: plugin extra da abilitare oltre a quelli di default.
        default_map: mappa di avvio, es. "/Game/MyGame/Levels/L_Main".
        default_game_mode: GameMode di default (path della classe `..._C`).
        force: procede anche se la cartella di destinazione esiste già.
        engine_root: percorso del motore, quando la ricerca automatica non lo trova
            (es. "C:/Program Files/Epic Games/UE_5.8").
    """
    return await local_call(
        local.create_project,
        name=name,
        directory=directory,
        engine_version=engine_version,
        engine_root=engine_root,
        template=template,
        blueprint_only=blueprint_only,
        plugins=plugins,
        default_map=default_map,
        default_game_mode=default_game_mode,
        description=description,
        force=force,
    )


@mcp.tool()
async def ue_project_find(directory: str, max_depth: int = 3) -> dict:
    """Cerca file .uproject sotto una cartella."""
    projects = await local_call(local.find_projects, directory, max_depth)
    return {"count": len(projects), "projects": projects}


@mcp.tool()
async def ue_project_info(uproject: str) -> dict:
    """Legge un .uproject: versione motore associata, plugin attivi e se il bridge è pronto."""
    return await local_call(local.project_info, uproject)


@mcp.tool()
async def ue_project_set_plugins(
    uproject: str, enable: list[str], disable: list[str] | None = None
) -> dict:
    """Abilita/disabilita plugin scrivendo nel .uproject (utile su progetti esistenti).

    Per usare il bridge servono almeno `PythonScriptPlugin` e `RemoteControl`.
    L'editor va riavviato se era già aperto.
    """
    return await local_call(local.set_project_plugins, uproject, enable, disable)


@mcp.tool()
async def ue_editor_open(
    uproject: str,
    engine_version: str | None = None,
    wait_seconds: int = 50,
    extra_args: list[str] | None = None,
    engine_root: str | None = None,
    skip_module_check: bool = False,
    ctx: Context | None = None,
) -> dict:
    """Apre un progetto nell'editor e attende che il bridge risponda.

    L'attesa predefinita è breve di proposito: molti client MCP interrompono una
    richiesta dopo 60 secondi, e un `wait_seconds` più lungo faceva fallire la
    chiamata con "Request timed out" **anche quando l'editor era partito
    benissimo**. Se il bridge non è ancora pronto la risposta lo dice e basta
    richiamare ue_editor_status finché `running` diventa `bridge_ready`.

    Args:
        uproject: percorso del file .uproject.
        engine_version: forza una versione del motore diversa da quella associata.
        wait_seconds: quanto attendere il bridge. Il primo avvio compila gli
            shader e può volerci molto di più: in quel caso non alzarlo oltre il
            timeout del client, si fa polling con ue_editor_status.
        extra_args: argomenti aggiuntivi per la riga di comando dell'editor.
        engine_root: percorso del motore, quando non è registrato nel sistema.
        skip_module_check: lancia anche se i moduli C++ non corrispondono al
            motore (l'editor resterà bloccato su una modale dietro allo splash).
    """
    # L'editor riparte da zero: gli helper installati nella sessione precedente
    # non ci sono più.
    _bridge.forget_helpers()
    launched = await local_call(
        local.launch_editor, uproject, engine_version, extra_args, engine_root, skip_module_check
    )

    if wait_seconds <= 0:
        return {**launched, "bridge_ready": False, "note": "launch not awaited"}

    inizio = asyncio.get_event_loop().time()
    deadline = inizio + wait_seconds
    await _progress(ctx, 0, wait_seconds, "editor avviato, attendo il bridge")
    while asyncio.get_event_loop().time() < deadline:
        try:
            await _bridge.info()
            status = await run("result = mcp_project_status()")
            await _progress(ctx, wait_seconds, wait_seconds, "bridge pronto")
            return {**launched, "bridge_ready": True, "status": status}
        except (UnrealNotConnected, UnrealBridgeError, RuntimeError):
            await asyncio.sleep(3)
            await _progress(
                ctx,
                asyncio.get_event_loop().time() - inizio,
                wait_seconds,
                "attendo l'editor (il primo avvio compila gli shader)",
            )

    return {
        **launched,
        "bridge_ready": False,
        "note": (
            "Editor avviato (pid %s), ma il bridge non ha risposto entro %d s. "
            "Non è un errore: il primo avvio compila gli shader e ci mette "
            "minuti. Richiama ue_editor_status per seguirlo, e ue_status quando "
            "risulta pronto. Se resta fermo a '0%% - Initializing..' cerca con "
            "Alt-Tab una finestra dell'editor dietro allo splash."
            % (launched.get("pid"), wait_seconds)
        ),
    }


@mcp.tool()
async def ue_cpp_class_create(
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

    È la risposta al limite principale di questo MCP: i grafi Blueprint non
    sono scrivibili da Python, ma la logica può stare in C++ e arrivare ai
    Blueprint per ereditarietà. Flusso completo:

        ue_cpp_class_create -> ue_editor_close -> ue_build_start ->
        ue_build_status (finché running=false) -> ue_editor_open ->
        ue_reparent_blueprint

    Se il progetto è Blueprint-only, il modulo C++ (Build.cs, Target.cs,
    IMPLEMENT_PRIMARY_GAME_MODULE, voce Modules nel .uproject) viene creato.

    Args:
        uproject: percorso del file .uproject.
        class_name: nome senza prefisso, es. "DoorBase" -> ADoorBase.
        parent_class: "Actor", "Pawn", "Character", "ActorComponent",
            "GameModeBase", "PlayerState", ...
        module: nome del modulo C++; default il nome del progetto.
        properties: lista di dict con `name`, `type` (es. "float", "FVector",
            "TObjectPtr<AActor>"), e opzionali `category`, `default`,
            `replicated`, `rep_notify`, `read_only`. Le proprietà replicate
            generano anche GetLifetimeReplicatedProps e i DOREPLIFETIME.
        functions: lista di dict con `name`, `return_type`, `params`,
            `specifiers` (default BlueprintCallable), `body`. Le funzioni
            BlueprintCallable diventano chiamabili dai grafi Blueprint.
        with_tick: abilita il Tick (di default è spento, come conviene).
        force: sovrascrive i file se esistono già.
    """
    return await local_call(
        local.create_cpp_class,
        uproject=uproject,
        class_name=class_name,
        parent_class=parent_class,
        module=module,
        properties=properties,
        functions=functions,
        with_tick=with_tick,
        force=force,
    )


@mcp.tool()
async def ue_build_start(
    uproject: str,
    engine_version: str | None = None,
    target: str | None = None,
    configuration: str = "Development",
    engine_root: str | None = None,
    force: bool = False,
) -> dict:
    """Avvia la compilazione del modulo C++ del progetto (in background).

    **L'editor deve essere chiuso**: con Live Coding attivo UnrealBuildTool si
    rifiuta di scrivere le DLL, e per un modulo nuovo Live Coding non è
    sufficiente. Sequenza tipica: ue_editor_close -> ue_build_start ->
    ue_build_status (finché running=false) -> ue_editor_open.

    Args:
        uproject: percorso del file .uproject.
        engine_version: forza una versione del motore diversa da quella associata.
        target: default "<Progetto>Editor".
        configuration: "Development" | "DebugGame" | "Shipping".
        engine_root: percorso del motore, quando non è registrato nel sistema
            e la ricerca automatica non lo trova.
        force: avvia anche se risulta già una compilazione in corso. Serve solo
            quando la precedente è rimasta bloccata e ne hai chiuso i processi:
            due Build.bat insieme si accodano sullo stesso mutex.
    """
    return await local_call(
        local.start_build,
        uproject,
        engine_version,
        target,
        None,
        configuration,
        engine_root,
        force,
    )


@mcp.tool()
async def ue_build_status(
    tail_lines: int = 30,
    uproject: str | None = None,
    wait_seconds: float = 0,
    ctx: Context | None = None,
) -> dict:
    """Stato della compilazione avviata con ue_build_start: in corso, errori, coda del log.

    Args:
        tail_lines: quante righe finali del log restituire.
        uproject: a quale progetto si riferisce; se omesso, l'ultima compilazione avviata.
        wait_seconds: se > 0 attende la fine della compilazione fino a questo
            limite, riportando l'avanzamento, invece di restituire subito. Una
            build completa dura minuti: meglio un'attesa sola che venti letture.
    """
    async def leggi() -> dict:
        return await local_call(local.build_status, tail_lines, uproject)

    if wait_seconds > 0:
        return await _attendi_job(leggi, wait_seconds, ctx, "compilazione")
    return await leggi()


@mcp.tool()
async def ue_live_compile(max_wait_seconds: float = 20.0) -> dict:
    """Ricompila il C++ **senza chiudere l'editor**, tramite Live Coding.

    È la via veloce per iterare: nessuna chiusura, nessun riavvio, il gioco
    riparte con il codice nuovo. Funziona però solo sulle modifiche al corpo
    delle funzioni: aggiungere o cambiare UCLASS, UFUNCTION o UPROPERTY altera
    i dati di reflection e richiede ue_build_start a editor chiuso.

    Args:
        max_wait_seconds: quanto attendere l'esito prima di restituire
            "in corso" (la compilazione prosegue comunque).
    """
    return await run(f"result = mcp_live_compile({float(max_wait_seconds)})")


@mcp.tool()
async def ue_package_start(
    uproject: str,
    configuration: str = "Development",
    maps: list[str] | None = None,
    output_dir: str | None = None,
    dedicated_server: bool = False,
    engine_version: str | None = None,
    engine_root: str | None = None,
    target_platform: str | None = None,
) -> dict:
    """Crea il pacchetto giocabile del gioco (cook + build + stage + pak).

    Produce un eseguibile autonomo, avviabile senza l'editor. Operazione lunga
    (decine di minuti al primo giro, molto meno dopo): parte in background,
    lo stato si consulta con ue_package_status.

    **L'editor deve essere chiuso.** Sequenza: ue_editor_close ->
    ue_package_start -> ue_package_status (finché running=false).

    Args:
        uproject: percorso del file .uproject.
        configuration: "Development" (con log e console, per il prototipo) o
            "Shipping" (ottimizzato, senza log, per la distribuzione).
        maps: mappe da cuocere, es. ["/Game/MyGame/Levels/L_Main"].
            Se omesso cuoce secondo le impostazioni di progetto, che è più lento.
        output_dir: cartella di destinazione; default <Progetto>/Packaged.
        dedicated_server: produce anche un server dedicato per le partite LAN.
        engine_root: percorso del motore, se la ricerca automatica non lo trova.
        target_platform: "Win64" | "Linux" | "Mac"; default la piattaforma corrente.
    """
    return await local_call(
        local.start_package,
        uproject,
        engine_version,
        configuration,
        target_platform or local.default_target_platform(),
        maps,
        output_dir,
        dedicated_server,
        engine_root,
    )


@mcp.tool()
async def ue_package_status(
    tail_lines: int = 30,
    uproject: str | None = None,
    wait_seconds: float = 0,
    ctx: Context | None = None,
) -> dict:
    """Stato del packaging avviato con ue_package_start.

    Riporta la fase corrente (Cook, Stage, Package, Archive), gli errori e,
    a fine corsa, il percorso dell'eseguibile prodotto.

    Args:
        tail_lines: quante righe finali del log restituire.
        uproject: a quale progetto si riferisce; se omesso, l'ultimo packaging avviato.
        wait_seconds: se > 0 attende la fine del packaging fino a questo limite,
            riportando la fase corrente, invece di restituire subito.
    """
    async def leggi() -> dict:
        return await local_call(local.package_status, tail_lines, uproject)

    if wait_seconds > 0:
        return await _attendi_job(leggi, wait_seconds, ctx, "packaging")
    return await leggi()


@mcp.tool()
async def ue_build_unblock(
    dry_run: bool = True,
    engine_version: str | None = None,
    engine_root: str | None = None,
) -> dict:
    """Trova — e se vuoi termina — i processi che tengono occupato il lock di build.

    Serve quando ue_build_status riporta `blocked`: gli script di Epic prendono
    un lock globale e un'istanza rimasta orfana non lo rilascia mai, quindi
    Build.bat aspetta all'infinito e ogni nuovo tentativo si accoda.

    La ricerca è sulla **riga di comando**, non sul nome dell'immagine: su UE 5
    UnrealBuildTool è un assembly .NET dentro `dotnet.exe` e gli script girano
    dentro `cmd.exe`, quindi nessuno dei due si trova con
    `taskkill /IM UnrealBuildTool.exe` — ed è il motivo per cui il lock sembra
    inestirpabile.

    Args:
        dry_run: con True (default) elenca soltanto. La ricerca per riga di
            comando può intercettare un `dotnet.exe` che sta facendo altro:
            guarda l'elenco prima di terminarlo.
        engine_version, engine_root: quale motore, per sapere quale file di
            lock controllare (`%TMP%\\<percorso di Build.bat>.lock`).
    """
    return await local_call(local.clear_build_locks, dry_run, engine_version, engine_root)


@mcp.tool()
async def ue_editor_status() -> dict:
    """Stato del processo editor avviato da questo MCP e del bridge Remote Control."""
    process = await local_call(local.editor_status)
    try:
        await _bridge.info()
        bridge_ready = True
    except UnrealBridgeError:
        bridge_ready = False
    return {**process, "bridge_ready": bridge_ready}


@mcp.tool()
async def ue_editor_close(save_all: bool = True, force: bool = False) -> dict:
    """Chiude l'editor.

    Prima tenta la chiusura pulita (salvataggio + `quit_editor` via bridge); se
    fallisce o se `force=True`, termina il processo.

    Args:
        save_all: salva livello e asset modificati prima di chiudere.
        force: salta il tentativo pulito e termina direttamente il processo.
    """
    if not force:
        try:
            if save_all:
                await run(
                    """
mcp_level_subsystem().save_current_level()
unreal.EditorAssetLibrary.save_directory("/Game", only_if_is_dirty=True, recursive=True)
result = True
"""
                )
            await _bridge.exec_python_raw(
                "import unreal\nunreal.SystemLibrary.quit_editor()\n"
            )
            await asyncio.sleep(3)
            _bridge.forget_helpers()
            return {"closed": True, "mode": "clean", "saved": save_all, **await local_call(local.editor_status)}
        except (UnrealBridgeError, RuntimeError) as exc:
            fallback_reason = str(exc)
    else:
        fallback_reason = "force=True"

    killed = await local_call(local.kill_editor)
    _bridge.forget_helpers()
    return {
        "closed": killed.get("killed", False),
        "mode": "process terminated",
        "reason": fallback_reason,
        **killed,
    }


# =============================== livello LOCALE: download preset e asset


@mcp.tool()
async def preset_library_list(
    subfolder: str | None = None, extensions: list[str] | None = None
) -> dict:
    """Elenca i file già scaricati nella libreria locale (default: ~/UnrealAssetLibrary).

    I percorsi restituiti si passano direttamente a ue_import_assets.
    """
    files = await local_call(assets.list_library, subfolder, extensions)
    return {"library": str(assets.library_dir()), "count": len(files), "files": files[:300]}


@mcp.tool()
async def preset_download_url(
    url: str, destination: str | None = None, filename: str | None = None, extract: bool = True
) -> dict:
    """Scarica un file da un URL diretto (zip, glb, fbx, wav) nella libreria locale.

    Se è un archivio zip/tar e `extract=True`, lo estrae subito.
    """
    downloaded = await assets.download_file(url, destination, filename)
    result: dict[str, Any] = {"download": downloaded}
    if extract and downloaded["file"].lower().endswith((".zip", ".tar", ".tar.gz", ".tgz")):
        result["extracted"] = await local_call(assets.extract_archive, downloaded["file"])
    return result


@mcp.tool()
async def preset_extract_archive(archive: str, destination: str | None = None) -> dict:
    """Estrae un archivio zip/tar già presente sul disco (i .rar non sono supportati)."""
    return await local_call(assets.extract_archive, archive, destination)


@mcp.tool()
async def preset_search_polyhaven(
    asset_type: str = "textures", categories: list[str] | None = None, limit: int = 40
) -> dict:
    """Cerca asset CC0 su Poly Haven.

    Args:
        asset_type: "hdris" | "textures" | "models".
        categories: filtri, es. ["brick"], ["outdoor"].
        limit: massimo di risultati.
    """
    found = await assets.polyhaven_search(asset_type, categories, limit)
    return {"source": "polyhaven", "license": "CC0", "count": len(found), "assets": found}


@mcp.tool()
async def preset_download_polyhaven(
    asset_id: str,
    resolution: str = "2k",
    formats: list[str] | None = None,
    destination: str | None = None,
) -> dict:
    """Scarica un asset Poly Haven (CC0) nella libreria locale.

    Args:
        asset_id: id dell'asset, es. "brick_wall_02".
        resolution: "1k" | "2k" | "4k" | "8k".
        formats: ["jpg"] per texture, ["gltf"] per modelli, ["hdr"] per HDRI.
        destination: cartella alternativa alla libreria.
    """
    return await assets.polyhaven_download(asset_id, resolution, formats, destination)


@mcp.tool()
async def preset_search_ambientcg(
    query: str = "", asset_type: str = "Material", limit: int = 20
) -> dict:
    """Cerca materiali/HDRI/modelli CC0 su ambientCG.

    Args:
        query: parole chiave, es. "concrete".
        asset_type: "Material" | "HDRI" | "3DModel" | "Decal" | "Atlas" | "Terrain".
    """
    found = await assets.ambientcg_search(query, asset_type, limit)
    return {"source": "ambientcg", "license": "CC0", "count": len(found), "assets": found}


@mcp.tool()
async def preset_download_ambientcg(
    asset_id: str, variant: str = "2K-JPG", destination: str | None = None
) -> dict:
    """Scarica ed estrae un asset ambientCG (CC0).

    Args:
        asset_id: es. "PavingStones036".
        variant: es. "1K-JPG", "2K-JPG", "4K-PNG".
    """
    return await assets.ambientcg_download(asset_id, variant, destination)


@mcp.tool()
async def preset_download_kenney(slug: str, destination: str | None = None) -> dict:
    """Scarica ed estrae un pack Kenney (CC0) risolvendo il link dalla pagina.

    Args:
        slug: ultima parte dell'URL, es. "mini-characters-1" per
            kenney.nl/assets/mini-characters-1.
    """
    return await assets.kenney_download(slug, destination)


@mcp.tool()
async def preset_fab_status() -> dict:
    """Verifica il ponte verso la libreria Fab/Marketplace dell'account Epic.

    Dice se il client community `legendary` è installato e se il login Epic è
    stato fatto: sono i due prerequisiti di preset_fab_list_vault e
    preset_fab_install, e falliscono in modo diverso. Da chiamare per primo
    quando si lavora con contenuti acquistati.
    """
    return await local_call(assets.fab_status)


@mcp.tool()
async def preset_fab_list_vault(query: str | None = None) -> dict:
    """Elenca il contenuto Unreal acquistato sull'account Epic (Fab/Marketplace).

    Non esiste un'API pubblica: serve il client community `legendary`
    (`pip install legendary-gl` + `legendary auth`). Senza di esso il tool
    spiega come procedere dall'Epic Games Launcher.

    Args:
        query: filtro parziale su titolo o app_name, es. "soul" per "Soul City".
    """
    return await local_call(assets.fab_list_vault, query)


@mcp.tool()
async def preset_fab_download(app_name: str, destination: str | None = None) -> dict:
    """Scarica un pack del vault Epic sul disco, senza installarlo.

    Per portarlo dentro il progetto conviene preset_fab_install, che fa
    download e installazione in un colpo solo.

    Args:
        app_name: identificativo restituito da preset_fab_list_vault.
        destination: cartella alternativa alla libreria locale.
    """
    return await local_call(assets.fab_download, app_name, destination)


@mcp.tool()
async def preset_fab_install(
    source: str,
    uproject: str | None = None,
    subfolder: str | None = None,
    mode: str = "auto",
    overwrite: bool = False,
    enable_plugins: bool = True,
    refresh_editor: bool = True,
) -> dict:
    """Installa un pack Fab dentro il progetto e lo rende visibile nell'editor.

    Scarica il pack dal vault Epic se serve, ne riconosce la struttura e copia
    il contenuto in `Content/<subfolder>` (compare come /Game/<subfolder>) e gli
    eventuali plugin in `Plugins/`. I plugin vengono anche abilitati nel
    .uproject; l'Asset Registry dell'editor viene aggiornato subito, così gli
    asset compaiono nel Content Browser senza riavviare.

    Args:
        source: app_name del vault (vedi preset_fab_list_vault) oppure percorso
            di una cartella o di uno zip già sul disco — per esempio un pack
            scaricato a mano dalla finestra Fab dell'editor.
        uproject: progetto di destinazione. Se omesso usa quello aperto
            nell'editor.
        subfolder: sottocartella di Content; default il nome del pack.
        mode: "auto" (plugin + contenuto), "content" o "plugin".
        overwrite: sovrascrive una destinazione già esistente.
        enable_plugins: abilita nel .uproject i plugin appena installati.
        refresh_editor: rilegge l'Asset Registry sui path appena creati.
    """
    progetto = await _risolvi_uproject(uproject)
    esito = await local_call(assets.fab_install, source, progetto, subfolder, mode, overwrite)

    if enable_plugins and esito["plugins_installed"]:
        nomi = [p["name"] for p in esito["plugins_installed"]]
        esito["plugins_enabled"] = await local_call(local.set_project_plugins, progetto, nomi)

    if refresh_editor and esito["unreal_paths"]:
        esito["editor_refresh"] = await _rileggi_asset_registry(esito["unreal_paths"])

    return esito


async def _risolvi_uproject(esplicito: str | None) -> str:
    """Progetto su cui operare: quello passato, quello aperto, o niente.

    Chiedere all'editor è la fonte più affidabile — è il progetto che l'utente
    sta guardando — ma non deve essere un requisito: con l'editor chiuso si
    ripiega sull'ultimo aperto da noi e infine su UE_MCP_PROJECT.
    """
    if esplicito:
        return esplicito
    with contextlib.suppress(Exception):
        stato = await run("result = mcp_project_status()")
        if isinstance(stato, dict) and stato.get("project_file"):
            return str(stato["project_file"])
    with contextlib.suppress(Exception):
        locale = local.editor_status()
        if locale.get("uproject"):
            return str(locale["uproject"])
    dall_ambiente = os.environ.get("UE_MCP_PROJECT")
    if dall_ambiente:
        return dall_ambiente
    raise RuntimeError(
        "Non so in quale progetto installare: apri l'editor (ue_editor_open) "
        "oppure passa `uproject` con il percorso del file .uproject."
    )


async def _rileggi_asset_registry(paths: list[str]) -> dict:
    """Fa rileggere all'editor le cartelle appena copiate su disco.

    Senza questo i .uasset ci sono ma il Content Browser non li mostra finché
    non si riavvia: l'Asset Registry scansiona all'avvio.
    """
    try:
        return await run(
            "registry = unreal.AssetRegistryHelpers.get_asset_registry()\n"
            "registry.scan_paths_synchronous(%s, force_rescan=True)\n"
            "result = {\n"
            "    'scanned': %s,\n"
            "    'assets': {p: len(unreal.EditorAssetLibrary.list_assets(p, True, False))\n"
            "               for p in %s},\n"
            "}" % (lit(paths), lit(paths), lit(paths))
        )
    except Exception as exc:  # noqa: BLE001 - l'editor chiuso non invalida l'installazione
        return {
            "scanned": [],
            "error": str(exc)[-400:],
            "hint": "I file sono stati copiati: compariranno all'apertura dell'editor.",
        }


# =============================================================== editor / stato


@mcp.tool()
async def ue_status() -> dict:
    """Verifica la connessione all'editor Unreal e restituisce versione motore,
    progetto aperto, livello corrente, numero di attori e trasporto in uso.
    Da usare per prima."""
    stato = await run("result = mcp_project_status()")
    if isinstance(stato, dict):
        # Quale dei due canali sta servendo le chiamate: quando qualcosa non va,
        # è la prima cosa da sapere e altrimenti non è visibile da nessuna parte.
        stato["transport"] = _bridge.transport
    return stato


@mcp.tool()
async def ue_read_log(lines: int = 80, only_errors: bool = False) -> dict:
    """Legge la coda del log di Unreal: è il modo per vedere cosa è andato storto
    dopo un'operazione che non ha sollevato eccezioni.

    Args:
        lines: quante righe finali restituire (default 80).
        only_errors: se True filtra solo righe con Error/Warning.
    """
    return await run(f"result = mcp_tail_log({int(lines)}, {bool(only_errors)})")


@mcp.tool()
async def ue_exec_python(code: str) -> Any:
    """Esegue codice Python arbitrario dentro l'editor Unreal (escape hatch).

    Il modulo `unreal` e tutti gli helper `mcp_*` sono disponibili. Assegna il
    valore da restituire alla variabile `result`.

    Args:
        code: snippet Python, es. "result = len(mcp_actor_subsystem().get_all_level_actors())".
    """
    return await run(code)


@mcp.tool()
async def ue_save_all() -> dict:
    """Salva il livello corrente e tutti gli asset modificati."""
    return await run(
        """
mcp_level_subsystem().save_current_level()
unreal.EditorAssetLibrary.save_directory("/Game", only_if_is_dirty=True, recursive=True)
result = {"saved": True}
"""
    )


# ================================================================ asset / import


@mcp.tool()
async def ue_import_assets(
    files: list[str],
    destination: str = "/Game/Imported",
    replace_existing: bool = True,
    import_as_skeletal: bool = False,
) -> list[dict]:
    """Importa file 3D/audio (.glb, .gltf, .fbx, .wav) nel Content Browser.

    I .glb/.gltf passano dal framework Interchange nativo.
    Per i personaggi con scheletro usare .fbx con import_as_skeletal=True.

    Args:
        files: percorsi assoluti sul disco (es. ["C:/Assets/Rocks/rock_01.glb"]).
        destination: cartella Unreal di destinazione (/Game/...).
        replace_existing: sovrascrive asset con lo stesso nome.
        import_as_skeletal: per .fbx, importa come Skeletal Mesh con animazioni.
    """
    return await run(
        f"result = mcp_import_assets({lit(files)}, {lit(destination)}, "
        f"{bool(replace_existing)}, {bool(import_as_skeletal)})"
    )


@mcp.tool()
async def ue_list_assets(
    path: str = "/Game", recursive: bool = True, class_filter: str | None = None
) -> list[dict]:
    """Elenca gli asset presenti sotto un path del Content Browser.

    Args:
        path: cartella Unreal (es. "/Game/MyGame").
        recursive: include le sottocartelle.
        class_filter: filtro parziale sul nome classe (es. "Blueprint", "StaticMesh").
    """
    return await run(
        f"result = mcp_list_assets({lit(path)}, {bool(recursive)}, {lit(class_filter)})"
    )


@mcp.tool()
async def ue_delete_asset(path: str, force: bool = False) -> dict:
    """Elimina un asset o una cartella dal Content Browser.

    Serve per rimediare a un import sbagliato senza aprire l'editor a mano.
    Di default rifiuta la cancellazione se qualcosa referenzia l'asset:
    cancellarlo comunque lascia riferimenti rotti nei livelli e nei Blueprint.

    Args:
        path: es. "/Game/Imported/rock_01" oppure una cartella "/Game/Imported".
        force: cancella anche se referenziato.
    """
    return await run(f"result = mcp_delete_asset({lit(path)}, {bool(force)})")


@mcp.tool()
async def ue_rename_asset(path: str, new_path: str) -> dict:
    """Sposta o rinomina un asset aggiornando i riferimenti.

    Args:
        path: percorso attuale, es. "/Game/Imported/SM_rock".
        new_path: percorso nuovo, es. "/Game/MyGame/Meshes/SM_Roccia".
    """
    return await run(f"result = mcp_rename_asset({lit(path)}, {lit(new_path)})")


@mcp.tool()
async def ue_duplicate_asset(path: str, new_path: str) -> dict:
    """Duplica un asset: la via rapida per crearne una variante da modificare."""
    return await run(f"result = mcp_duplicate_asset({lit(path)}, {lit(new_path)})")


@mcp.tool()
async def ue_make_folder(path: str) -> dict:
    """Crea una cartella nel Content Browser. Idempotente.

    Args:
        path: es. "/Game/MyGame/Meshes".
    """
    return await run(f"result = mcp_make_folder({lit(path)})")


@mcp.tool()
async def ue_attach_actor(
    child_label: str,
    parent_label: str,
    socket: str | None = None,
    attach_rule: str = "KEEP_WORLD",
) -> dict:
    """Aggancia un attore a un altro: muovendo il padre si muove il figlio.

    È il modo in cui si compone una scena (le luci a un lampione, le casse su
    un pallet) invece di lasciare oggetti slegati da riposizionare uno per uno.

    Args:
        child_label: label dell'attore da agganciare.
        parent_label: label dell'attore padre.
        socket: nome del socket sul padre, se ne ha.
        attach_rule: "KEEP_WORLD" (resta dov'è), "KEEP_RELATIVE" o
            "SNAP_TO_TARGET" (si allinea al padre).
    """
    return await run(
        f"result = mcp_attach_actor({lit(child_label)}, {lit(parent_label)}, "
        f"{lit(socket)}, {lit(attach_rule)})"
    )


@mcp.tool()
async def ue_detach_actor(label: str, keep_world: bool = True) -> dict:
    """Sgancia un attore dal suo padre.

    Args:
        label: label dell'attore.
        keep_world: mantiene la posizione nel mondo invece di quella relativa.
    """
    return await run(f"result = mcp_detach_actor({lit(label)}, {bool(keep_world)})")


@mcp.tool()
async def ue_actor_hierarchy(label: str | None = None) -> list[dict]:
    """Albero padre/figli degli attori del livello.

    Args:
        label: se indicato, parte da quell'attore invece che dalle radici.
    """
    return await run(f"result = mcp_actor_hierarchy({lit(label)})")


@mcp.tool()
async def ue_new_level(path: str, template: str | None = None) -> dict:
    """Crea un nuovo livello e lo apre.

    Args:
        path: path completo del livello, es. "/Game/MyGame/Levels/L_Main".
        template: livello sorgente da duplicare (opzionale).
    """
    if template:
        code = f"""
ok = mcp_level_subsystem().new_level_from_template({lit(path)}, {lit(template)})
result = {{"path": {lit(path)}, "created": bool(ok), "template": {lit(template)}}}
"""
    else:
        code = f"""
ok = mcp_level_subsystem().new_level({lit(path)})
result = {{"path": {lit(path)}, "created": bool(ok)}}
"""
    return await run(code)


@mcp.tool()
async def ue_open_level(path: str) -> dict:
    """Apre un livello esistente nell'editor.

    Args:
        path: es. "/Game/MyGame/Levels/L_Main".
    """
    return await run(
        f"""
ok = mcp_level_subsystem().load_level({lit(path)})
result = {{"path": {lit(path)}, "opened": bool(ok)}}
"""
    )


# ======================================================================= attori


@mcp.tool()
async def ue_spawn_actor(
    class_ref: str,
    location: list[float] | None = None,
    rotation: list[float] | None = None,
    scale: list[float] | None = None,
    label: str | None = None,
) -> dict:
    """Spawna un attore nel livello corrente.

    Args:
        class_ref: "StaticMeshActor", "/Script/Engine.PointLight", "/Game/MyGame/BP_Enemy",
            oppure il path di uno StaticMesh importato (spawn diretto della mesh).
        location: [x, y, z] in cm.
        rotation: [pitch, yaw, roll] in gradi.
        scale: [x, y, z] moltiplicatore.
        label: etichetta leggibile nell'Outliner (usata dagli altri tool per ritrovarlo).
    """
    return await run(
        f"result = mcp_spawn({lit(class_ref)}, {lit(location)}, {lit(rotation)}, "
        f"{lit(scale)}, {lit(label)})"
    )


@mcp.tool()
async def ue_list_actors(
    name_contains: str | None = None, class_contains: str | None = None
) -> list[dict]:
    """Elenca gli attori del livello corrente, con filtri opzionali su nome e classe."""
    return await run(
        f"result = mcp_find_actors({lit(name_contains)}, {lit(class_contains)})"
    )


@mcp.tool()
async def ue_spawn_many(actors: list[dict]) -> dict:
    """Spawna molti attori in una sola chiamata e in una sola transazione undo.

    Costruire un livello con ue_spawn_actor costa un round-trip HTTP per attore:
    per una scena di qualche decina di elementi conviene questo.

    Args:
        actors: lista di dict con `class_ref` (obbligatorio) e, opzionali,
            `location` [x,y,z], `rotation` [pitch,yaw,roll], `scale` [x,y,z],
            `label`. Esempio: [{"class_ref": "PointLight", "location": [0,0,300]}].
    """
    return await run(f"result = mcp_spawn_many({lit(actors)})")


@mcp.tool()
async def ue_set_actor_transform(
    label: str,
    location: list[float] | None = None,
    rotation: list[float] | None = None,
    scale: list[float] | None = None,
) -> dict:
    """Modifica posizione/rotazione/scala di un attore identificato dalla sua label."""
    return await run(
        f"result = mcp_set_transform({lit(label)}, {lit(location)}, "
        f"{lit(rotation)}, {lit(scale)})"
    )


@mcp.tool()
async def ue_set_actor_property(
    label: str, properties: dict, component: str | None = None
) -> dict:
    """Imposta proprietà su un attore già piazzato, o su un suo componente.

    Copre tutto ciò che non sono i Class Defaults di un Blueprint: assegnare
    una mesh a uno StaticMeshActor, l'intensità di una luce, il raggio di un
    trigger. I valori seguono il JSON: un vettore è {"x":0,"y":0,"z":0}, un
    colore {"r":1,"g":0,"b":0}, e il path di un asset (/Game/...) viene
    caricato in automatico.

    Args:
        label: etichetta dell'attore nell'Outliner.
        properties: mappa nome_proprieta -> valore.
        component: nome o classe del componente su cui scrivere
            (es. "StaticMeshComponent"); se omesso scrive sull'attore.
    """
    return await run(
        f"result = mcp_set_actor_property({lit(label)}, {lit(properties)}, {lit(component)})"
    )


@mcp.tool()
async def ue_list_actor_components(label: str) -> dict:
    """Elenca i componenti di un attore piazzato, con nome e classe.

    Serve a sapere cosa passare come `component` a ue_set_actor_property.
    """
    return await run(f"result = mcp_actor_component_list({lit(label)})")


@mcp.tool()
async def ue_delete_actor(label: str) -> dict:
    """Elimina dal livello corrente l'attore con la label indicata."""
    return await run(f"result = mcp_delete_actor({lit(label)})")


# =================================================================== blueprint


@mcp.tool()
async def ue_create_blueprint(
    package_path: str, name: str, parent_class: str = "Actor"
) -> dict:
    """Crea un Blueprint.

    Args:
        package_path: cartella, es. "/Game/MyGame/Blueprints".
        name: nome asset, es. "BP_CornerSlot".
        parent_class: "Actor", "Character", "Pawn", "GameModeBase", "PlayerState",
            "GameStateBase", "AIController" o un path completo.
    """
    return await run(
        f"result = mcp_create_blueprint({lit(package_path)}, {lit(name)}, {lit(parent_class)})"
    )


@mcp.tool()
async def ue_add_component(
    blueprint_path: str, component_class: str, name: str | None = None
) -> dict:
    """Aggiunge un componente a un Blueprint e lo ricompila.

    Args:
        blueprint_path: es. "/Game/MyGame/Blueprints/BP_Door".
        component_class: es. "StaticMeshComponent", "BoxComponent", "PointLightComponent".
        name: nome del componente nel Blueprint.
    """
    return await run(
        f"result = mcp_add_component({lit(blueprint_path)}, {lit(component_class)}, {lit(name)})"
    )


@mcp.tool()
async def ue_add_variable(
    blueprint_path: str,
    var_name: str,
    var_type: str = "float",
    sub_type: str | None = None,
    replicated: bool = False,
    instance_editable: bool = True,
    default_value: Any = None,
) -> dict:
    """Aggiunge una variabile membro a un Blueprint e ricompila. Richiede UE 5.4+
    (l'API Python per le variabili non esiste nei motori precedenti; ue_status
    riporta in `capabilities` cosa supporta il motore corrente).

    Args:
        blueprint_path: path del Blueprint.
        var_name: nome variabile, es. "OccupiedBy".
        var_type: bool | int | int64 | float | string | name | text | struct | object | class.
        sub_type: per struct ("Vector", "Rotator", "Transform", "LinearColor") o per
            object/class il nome della classe ("Actor", "PlayerState").
        replicated: marca la variabile come replicata (networking).
        instance_editable: esposta e modificabile sulle istanze nel livello.
        default_value: valore di default scritto nei Class Defaults (solo tipi semplici).
    """
    return await run(
        f"result = mcp_add_variable({lit(blueprint_path)}, {lit(var_name)}, {lit(var_type)}, "
        f"{lit(sub_type)}, {bool(replicated)}, {bool(instance_editable)}, {lit(default_value)})"
    )


@mcp.tool()
async def ue_set_class_defaults(blueprint_path: str, properties: dict) -> dict:
    """Imposta i Class Defaults (CDO) di un Blueprint.

    Args:
        blueprint_path: path del Blueprint.
        properties: mappa nome_proprieta -> valore, es. {"replicates": true}.
    """
    return await run(
        f"result = mcp_set_class_defaults({lit(blueprint_path)}, {lit(properties)})"
    )


@mcp.tool()
async def ue_compile_blueprint(blueprint_path: str) -> dict:
    """Compila e salva un Blueprint. Usare dopo modifiche manuali nell'editor."""
    return await run(f"result = mcp_compile_blueprint({lit(blueprint_path)})")


@mcp.tool()
async def ue_reparent_blueprint(
    blueprint_path: str, new_parent: str, remove_unused_variables: bool = False
) -> dict:
    """Riassegna il parent di un Blueprint, tipicamente a una classe C++.

    È la via per dare logica eseguibile a un Blueprint: i grafi non sono
    scrivibili da Python, ma la logica può stare nella classe C++ padre
    (creata con ue_cpp_class_create) mentre il Blueprint resta il contenitore
    di componenti e valori.

    Le variabili del Blueprint con lo stesso nome di una UPROPERTY del nuovo
    padre vengono assorbite; le altre sopravvivono rinominate con `_0`.

    Args:
        blueprint_path: es. "/Game/MyGame/Blueprints/BP_Door".
        new_parent: nome o path della nuova classe padre, es. "ADoorBase"
            oppure "/Script/MyGame.DoorBase".
        remove_unused_variables: ripulisce le variabili rimaste orfane.
    """
    return await run(
        f"result = mcp_reparent_blueprint({lit(blueprint_path)}, {lit(new_parent)}, "
        f"{bool(remove_unused_variables)})"
    )


# =================================================================== materiali


@mcp.tool()
async def ue_create_material(
    package_path: str,
    name: str,
    textures: dict | None = None,
    scalars: dict | None = None,
    two_sided: bool = False,
) -> dict:
    """Crea un materiale collegando le texture ai canali PBR.

    A differenza dei grafi Blueprint, il grafo *materiale* è pienamente
    scriptabile: qui i nodi vengono creati e collegati davvero.

    Args:
        package_path: cartella, es. "/Game/MyGame/Materials".
        name: nome asset, es. "M_Brick".
        textures: mappa canale -> path della texture importata. Canali:
            base_color, normal, roughness, metallic, ambient_occlusion,
            emissive, opacity. Usa la chiave "auto" con il path per far
            dedurre il canale dal nome file (convenzioni ambientCG/Poly Haven).
        scalars: costanti sui canali senza texture, es. {"roughness": 0.4}.
        two_sided: disattiva il backface culling.
    """
    return await run(
        f"result = mcp_create_material({lit(package_path)}, {lit(name)}, "
        f"{lit(textures)}, {lit(scalars)}, {bool(two_sided)})"
    )


@mcp.tool()
async def ue_create_material_instance(
    package_path: str, name: str, parent_path: str, parameters: dict | None = None
) -> dict:
    """Crea una Material Instance da un materiale padre e ne imposta i parametri.

    Variare un materiale via istanza non ricompila il grafo: è la via
    economica per avere molte varianti dello stesso materiale.

    Args:
        package_path: cartella di destinazione.
        name: es. "MI_Brick_Red".
        parent_path: materiale padre, es. "/Game/MyGame/Materials/M_Brick".
        parameters: mappa nome -> valore. Numero = scalare, dict {"r","g","b"}
            = colore, bool = static switch, path /Game/... = texture.
    """
    return await run(
        f"result = mcp_create_material_instance({lit(package_path)}, {lit(name)}, "
        f"{lit(parent_path)}, {lit(parameters)})"
    )


@mcp.tool()
async def ue_assign_material(
    label: str, material_path: str, slot: int = 0, component: str | None = None
) -> dict:
    """Assegna un materiale a un attore piazzato.

    Args:
        label: etichetta dell'attore nell'Outliner.
        material_path: es. "/Game/MyGame/Materials/M_Brick".
        slot: indice dello slot materiale sulla mesh.
        component: componente su cui scrivere; default il primo MeshComponent.
    """
    return await run(
        f"result = mcp_assign_material({lit(label)}, {lit(material_path)}, "
        f"{int(slot)}, {lit(component)})"
    )


# ================================================================== screenshot


#: Oltre questa soglia il PNG non viene allegato alla risposta: in base64 un
#: megabyte costa circa 350k caratteri, cioè più contesto di quanto ne valga
#: una singola immagine. Override: UE_MCP_MAX_SCREENSHOT (byte).
MAX_SCREENSHOT_BYTES = int(os.environ.get("UE_MCP_MAX_SCREENSHOT", 1_500_000))


# ============================================================ console e render


@mcp.tool()
async def ue_console_command(command: str, wait_seconds: float = 1.0) -> dict:
    """Esegue un comando della console dell'editor e restituisce ciò che stampa.

    I comandi di console non restituiscono valori: scrivono nel log. Questo tool
    misura il log prima e dopo e riporta solo le righe nuove, altrimenti la
    risposta sarebbe "fatto" e nient'altro.

    Utile per `stat unit`, `r.ScreenPercentage 50`, `showflag.*`, `DumpConsoleCommands`.
    Passa dall'interprete Python dell'editor, non dal gate
    `bAllowConsoleCommandRemoteExecution` della Remote Control API, che resta
    spento: da qui si può fare tutto quello che si fa dalla console, `quit`
    compreso.

    Args:
        command: il comando, es. "stat fps".
        wait_seconds: quanto attendere che il motore scriva nel log.
    """
    return await run(
        f"result = mcp_console_command({lit(command)}, {float(wait_seconds)})"
    )


@mcp.tool()
async def ue_render_sequence(
    uproject: str,
    sequence: str,
    config: str | None = None,
    map_path: str | None = None,
    output_dir: str | None = None,
    resolution: list[int] | None = None,
    engine_version: str | None = None,
    engine_root: str | None = None,
    force: bool = False,
) -> dict:
    """Renderizza una Level Sequence con la Movie Render Queue, in background.

    Gira in un processo `UnrealEditor-Cmd` headless, non nell'editor aperto: la
    MRQ in-editor è asincrona e terrebbe l'editor occupato per tutta la durata,
    senza un modo pulito di attenderla dal bridge. Come per le build, si avvia e
    si consulta ue_render_status.

    Args:
        uproject: percorso del file .uproject.
        sequence: la Level Sequence, es. "/Game/Cinematics/LS_Intro".
        config: preset di Movie Pipeline salvato, es.
            "/Game/Cinematics/MRQ_Preset". **È il modo di scegliere formato,
            risoluzione e cartella di uscita**: senza, la MRQ usa le
            impostazioni predefinite del progetto e potrebbe non scrivere nulla.
        map_path: livello da caricare; default quello di avvio del progetto.
        output_dir: dove cercare i file prodotti; default <Progetto>/Saved/MovieRenders.
        resolution: [larghezza, altezza]; default [1920, 1080].
        force: avvia anche se risulta già un render in corso.
    """
    return await local_call(
        local.start_render,
        uproject,
        sequence,
        config,
        map_path,
        output_dir,
        resolution,
        engine_version,
        engine_root,
        force,
    )


@mcp.tool()
async def ue_render_status(
    tail_lines: int = 30,
    uproject: str | None = None,
    wait_seconds: float = 0,
    ctx: Context | None = None,
) -> dict:
    """Stato del render avviato con ue_render_sequence.

    `succeeded` guarda i file prodotti, non il codice di uscita: la MRQ headless
    può chiudere con 0 senza aver scritto un fotogramma, se la config non aveva
    nodi di output.

    Args:
        tail_lines: quante righe finali del log restituire.
        uproject: a quale progetto si riferisce; se omesso, l'ultimo render avviato.
        wait_seconds: se > 0 attende la fine fino a questo limite riportando
            l'avanzamento, invece di restituire subito.
    """
    async def leggi() -> dict:
        return await local_call(local.render_status, tail_lines, uproject)

    if wait_seconds > 0:
        return await _attendi_job(leggi, wait_seconds, ctx, "render")
    return await leggi()


# ================================================================ viewport


@mcp.tool()
async def ue_get_camera() -> dict:
    """Posizione e orientamento della camera della viewport dell'editor.

    Da chiamare **prima di spawnare**: i livelli veri sono spesso costruiti a
    migliaia di unità dall'origine, e un attore messo a [0,0,0] finisce fuori
    campo, invisibile a chi guarda l'editor.
    """
    return await run("result = mcp_get_camera()")


@mcp.tool()
async def ue_set_camera(
    location: list[float] | None = None, rotation: list[float] | None = None
) -> dict:
    """Sposta la camera della viewport.

    Args:
        location: [x, y, z] in cm.
        rotation: [pitch, yaw, roll] in gradi.
    """
    return await run(
        f"result = mcp_set_camera({lit(location)}, {lit(rotation)})"
    )


@mcp.tool()
async def ue_focus_actor(label: str | None = None, distance: float | None = None) -> dict:
    """Inquadra un attore con la camera, come il tasto F nell'editor.

    È il complemento di ue_screenshot: senza, si fotografa quello che la camera
    stava già guardando, che di rado è quello appena costruito.

    Args:
        label: attore da inquadrare; se omesso usa la selezione corrente.
        distance: distanza della camera in cm (default 500).
    """
    return await run(
        f"result = mcp_focus_actor({lit(label)}, {lit(distance)})"
    )


# ================================================================ screenshot


async def _diagnosi_cattura_mancata() -> str:
    """Perché la cattura non è arrivata, quando si riesce a saperlo.

    Il sospettato numero uno è "Use Less CPU when in Background": con quello
    attivo l'editor che non ha il fuoco smette di ridisegnare la viewport, e
    `take_high_res_screenshot` non ha nessun frame da salvare. Il sintomo è
    perfido — l'editor risponde a tutto il resto, quindi sembra un problema
    della cattura — e usare il pannello sposta il fuoco su Claude, cioè
    provoca esattamente la condizione che lo rompe.
    """
    try:
        attivo = await run(
            "import unreal\n"
            "_c = unreal.find_object(None, '/Script/UnrealEd.EditorPerformanceSettings')\n"
            "result = bool(unreal.get_default_object(_c).get_editor_property("
            "'bThrottleCPUWhenNotForeground')) if _c else None"
        )
    except Exception:  # noqa: BLE001
        return ""

    if attivo:
        return (
            " Causa probabile: in Editor Preferences → Performance è attivo "
            "'Use Less CPU when in Background', e con l'editor non in primo "
            "piano la viewport non ridisegna. Disattivalo."
        )
    return ""


async def _attendi_screenshot(percorso: str | None, secondi: float = 15.0) -> Path | None:
    """Aspetta che l'editor scriva il PNG, restituendo il file quando c'è.

    L'attesa deve stare qui e non dentro l'editor: là girerebbe sul game
    thread, che è lo stesso che disegna il frame da cui esce il PNG, e
    bloccherebbe la cosa che sta aspettando. Qui invece l'editor continua a
    girare mentre noi dormiamo, e il file compare dopo un paio di frame.

    Se l'editor è su un'altra macchina il file non comparirà mai da questa
    parte: si esce allo scadere del tempo e il chiamante lo segnala.
    """
    if not percorso:
        return None

    file = Path(percorso)
    scadenza = asyncio.get_running_loop().time() + secondi
    while asyncio.get_running_loop().time() < scadenza:
        # La dimensione conta: il PNG appare vuoto per un istante mentre viene
        # scritto, e leggerlo lì darebbe un'immagine troncata.
        if file.is_file() and file.stat().st_size > 0:
            return file
        await asyncio.sleep(0.25)
    return None

# structured_output=False: il tool restituisce [Image, dict], e l'output
# strutturato di FastMCP sa serializzare solo JSON — con lo schema attivo la
# chiamata fallisce con "Unable to serialize unknown type: Image".
@mcp.tool(structured_output=False)
async def ue_screenshot(
    filename: str | None = None,
    width: int = 960,
    height: int = 540,
    return_image: bool = True,
) -> Any:
    """Cattura la viewport dell'editor e **restituisce l'immagine all'agente**.

    Senza questo l'agente costruisce alla cieca: è l'unico modo per verificare
    davvero com'è venuta una scena invece di dedurlo dalle coordinate. Il file
    resta in <Progetto>/Saved/Screenshots/MCP.

    La risoluzione predefinita è volutamente modesta: il PNG viaggia in base64
    dentro la risposta, e a 1280x720 costa spesso più contesto di quanto
    l'immagine ne faccia risparmiare. Alzala quando serve leggere un dettaglio.

    Args:
        filename: nome file; se omesso ne genera uno con il timestamp.
        width, height: risoluzione della cattura.
        return_image: se False restituisce solo il percorso, senza allegare il PNG.
    """
    esito = await run(
        f"result = mcp_screenshot({lit(filename)}, {int(width)}, {int(height)})"
    )

    richiesto = (esito or {}).get("requested") if isinstance(esito, dict) else None
    file = await _attendi_screenshot(richiesto)
    if file is not None:
        esito["file"] = str(file)
        esito["captured"] = True
        esito["note"] = None
    if not return_image:
        return esito

    # L'editor potrebbe essere su un'altra macchina (UE_MCP_HOST): in quel caso
    # il path esiste per lui e non per noi, e c'è solo da dirlo.
    if file is None:
        esito["image"] = None
        esito["image_note"] = (
            "PNG non comparso entro l'attesa: se l'editor è su un'altra "
            "macchina il percorso resta valido per lui."
        ) + await _diagnosi_cattura_mancata()
        return esito

    peso = file.stat().st_size
    if peso > MAX_SCREENSHOT_BYTES:
        esito["image"] = None
        esito["image_note"] = (
            "PNG di %.1f MB, oltre il limite di %.1f MB: non allegato. Abbassa "
            "width/height oppure alza UE_MCP_MAX_SCREENSHOT."
            % (peso / 1e6, MAX_SCREENSHOT_BYTES / 1e6)
        )
        return esito

    esito["image"] = "allegata alla risposta"
    esito["bytes"] = peso
    # Lista: FastMCP la converte in [ImageContent, TextContent], così il modello
    # vede davvero la viewport e ha comunque i metadati accanto.
    return [Image(path=str(file)), esito]


# ============================================================= pannello viewport

#: Nome del file che il pannello riusa per ogni cattura. Fisso di proposito:
#: vedi il commento in _cattura_data_uri.
PANNELLO_SCREENSHOT = "viewport_panel.png"


class PannelloViewport(TypedDict):
    """Forma del risultato di ue_viewport_panel.

    Serve un TypedDict e non un `dict` generico: FastMCP genera lo schema di
    output solo da un tipo strutturato, e senza schema non popola
    `structuredContent` — che è il campo da cui il pannello legge i dati.
    Con `-> dict` la vista si carica e resta vuota, senza errori.

    Qui dentro non c'è l'immagine, di proposito: vedi ue_viewport_frame.
    """

    error: str | None
    status: dict[str, Any]
    actors: list[dict[str, Any]]
    focused: str | None


class FrameViewport(TypedDict):
    """Forma del risultato di ue_viewport_frame."""

    error: str | None
    screenshot: str | None
    screenshot_note: str | None


async def _cattura_data_uri(width: int, height: int) -> tuple[str | None, str | None]:
    """Cattura la viewport e la restituisce come data URI, o dice perché no.

    Il pannello vive in un iframe sandboxed che non ha accesso al filesystem:
    un percorso non gli servirebbe a niente, l'immagine deve viaggiare dentro
    la risposta. Restituisce (data_uri, nota): esattamente uno dei due è None.
    """
    # Nome fisso, non col timestamp: il pannello ricattura a ogni comando e in
    # auto-refresh ogni cinque secondi, e un file nuovo da mezzo mega ogni
    # volta riempirebbe il disco senza che nessuno se ne accorga. L'unico
    # PNG che serve è l'ultimo, e l'editor lo sovrascrive.
    esito = await run(
        f"result = mcp_screenshot({lit(PANNELLO_SCREENSHOT)}, {int(width)}, {int(height)})"
    )
    richiesto = (esito or {}).get("requested") if isinstance(esito, dict) else None
    if not richiesto:
        return None, "L'editor non ha restituito nessun percorso."

    file = await _attendi_screenshot(richiesto)
    if file is None:
        return None, (
            "Cattura non comparsa entro l'attesa: se l'editor è su un'altra "
            "macchina il file non è leggibile da qui."
        ) + await _diagnosi_cattura_mancata()

    peso = file.stat().st_size
    # Il base64 gonfia di circa un terzo e qui il PNG viaggia due volte (dal
    # server all'host, dall'host all'iframe): vale il limite dello screenshot.
    if peso > MAX_SCREENSHOT_BYTES:
        return None, (
            "PNG di %.1f MB, oltre il limite di %.1f MB. Abbassa width/height "
            "oppure alza UE_MCP_MAX_SCREENSHOT." % (peso / 1e6, MAX_SCREENSHOT_BYTES / 1e6)
        )

    b64 = base64.b64encode(file.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{b64}", None


@mcp.tool(meta={"ui": {"resourceUri": ui.VIEWPORT_URI}})
async def ue_viewport_panel(focus: str | None = None) -> PannelloViewport:
    """Apre il pannello viewport: cattura, outliner e stato in un'unica vista.

    A differenza di ue_screenshot questo tool è pensato per l'occhio umano, non
    per quello del modello: l'host lo renderizza come MCP App dentro la chat,
    con la lista degli attori cliccabile. Il pannello richiama questo stesso
    tool per aggiornarsi, passando `focus` quando l'utente clicca un attore.

    La cattura non è qui dentro ma in ue_viewport_frame, che è la vista a
    chiamare per conto suo: così il PNG non passa dal contesto del modello.

    Per la verifica visiva durante la costruzione di una scena resta più
    economico ue_screenshot, che allega il PNG direttamente alla risposta.

    Args:
        focus: attore da inquadrare; se omesso lascia la camera dov'è.
    """
    try:
        if focus:
            await run(f"result = mcp_focus_actor({lit(focus)}, None)")

        stato = await run("result = mcp_project_status()")
        if isinstance(stato, dict):
            stato["transport"] = _bridge.transport
        attori = await run("result = mcp_find_actors(None, None)")
    except Exception as exc:  # noqa: BLE001
        # Un'eccezione qui farebbe sparire il pannello e l'utente vedrebbe
        # solo un errore rosso: meglio renderizzarlo dentro la vista, dove
        # resta accanto al bottone Aggiorna per riprovare.
        return {"error": str(exc), "status": {}, "actors": [], "focused": focus}

    return {
        "error": None,
        "status": stato if isinstance(stato, dict) else {},
        "actors": attori if isinstance(attori, list) else [],
        "focused": focus,
    }


@mcp.tool()
async def ue_viewport_camera(
    yaw: float = 0.0,
    pitch: float = 0.0,
    dolly: float = 0.0,
    view: str | None = None,
    distance: float | None = None,
) -> dict:
    """Orbita, avvicina o allinea la camera della viewport.

    Ruota attorno al punto che la camera sta guardando, invece di ruotare sul
    posto: è quello che serve per girare intorno a una scena senza perderla di
    vista. Usato dai controlli del pannello viewport.

    Args:
        yaw, pitch: gradi di rotazione attorno al centro inquadrato.
        dolly: avvicinamento in cm; negativo allontana.
        view: vista preimpostata fra top, front, back, left, right, persp.
        distance: distanza del centro di rotazione, default 1000 cm.
    """
    return await run(
        f"result = mcp_orbit_camera({float(yaw)}, {float(pitch)}, {float(dolly)}, "
        f"{lit(view)}, {lit(distance)})"
    )


@mcp.tool()
async def ue_viewport_frame(width: int = 960, height: int = 540) -> FrameViewport:
    """Cattura per il pannello viewport. **Non chiamarlo direttamente.**

    Esiste separato da ue_viewport_panel per una ragione precisa: un PNG della
    viewport pesa mezzo megabyte, e in base64 dentro un risultato di tool sono
    circa 200.000 token. Nel pannello l'immagine la chiede la vista con una sua
    chiamata, che non passa dal contesto del modello; se il risultato del
    pannello contenesse il data URI, ogni apertura brucerebbe il contesto.

    Se sei il modello e ti serve vedere la viewport, usa ue_screenshot: allega
    il PNG come immagine vera, che costa una frazione del base64.

    Args:
        width, height: risoluzione della cattura.
    """
    try:
        immagine, nota = await _cattura_data_uri(width, height)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "screenshot": None, "screenshot_note": None}
    return {"error": None, "screenshot": immagine, "screenshot_note": nota}


@mcp.resource(
    ui.VIEWPORT_URI,
    name="Pannello viewport",
    description="Interfaccia interattiva del pannello viewport, servita all'host.",
    mime_type=ui.UI_MIME,
    meta={"ui": {"csp": ui.VIEWPORT_CSP}},
)
def resource_viewport_ui() -> str:
    return ui.VIEWPORT_HTML


# ============================================================= pannello contenuti

#: Come le classi Unreal finiscono nelle categorie del pannello.
#:
#: Un pattern che comincia con "=" richiede il nome esatto, gli altri sono
#: sottostringhe. La distinzione non è pedanteria: "World" come sottostringa
#: cattura anche WorldDataLayers e WorldPartitionMiniMap, che sono oggetti
#: interni di un livello e non livelli — e l'utente si ritrova righe che non
#: può aprire. Dove il nome della classe è preciso si usa "=", dove è una
#: famiglia (MaterialInstance, MaterialFunction...) serve la sottostringa.
#:
#: L'ordine conta: vince la prima categoria che combacia.
CATEGORIE_ASSET: list[tuple[str, tuple[str, ...]]] = [
    ("livelli", ("=World",)),
    ("audio", ("Sound", "MetaSound", "Submix", "Dialogue")),
    ("blueprint", ("Blueprint",)),
    ("mesh", ("StaticMesh", "SkeletalMesh", "GeometryCollection")),
    ("animazioni", ("Anim", "BlendSpace", "=Skeleton", "PhysicsAsset")),
    ("materiali", ("Material",)),
    ("texture", ("Texture", "RenderTarget")),
    ("effetti", ("Niagara", "ParticleSystem", "=CascadeParticleSystem")),
]


def _combacia(classe: str, pattern: str) -> bool:
    if pattern.startswith("="):
        return classe.lower() == pattern[1:].lower()
    return pattern.lower() in classe.lower()


def _categoria(classe: str) -> str:
    for nome, pattern in CATEGORIE_ASSET:
        if any(_combacia(classe, p) for p in pattern):
            return nome
    return "altro"


class PannelloContenuti(TypedDict):
    """Risultato di ue_content_panel: solo il riassunto, mai l'elenco intero.

    Un progetto vero ha migliaia di asset, e questo risultato passa dal
    contesto del modello: l'elenco lo chiede la vista con ue_content_list.
    """

    error: str | None
    project: str | None
    totale: int
    conteggi: dict[str, int]
    levels: list[dict[str, Any]]


class ElencoAsset(TypedDict):
    """Risultato di ue_content_list."""

    error: str | None
    assets: list[dict[str, Any]]
    totale: int
    troncato: bool


async def _tutti_gli_asset() -> list[dict[str, Any]]:
    elenco = await run("result = mcp_list_assets('/Game', True, None)")
    return elenco if isinstance(elenco, list) else []


@mcp.tool(meta={"ui": {"resourceUri": ui.CONTENUTI_URI}})
async def ue_content_panel() -> PannelloContenuti:
    """Apre il pannello contenuti: livelli, audio e asset del progetto.

    Come ue_viewport_panel, è una vista per l'occhio umano: l'host la
    renderizza come MCP App dentro la chat, con le categorie navigabili.

    Restituisce solo i conteggi e i livelli, che sono pochi e servono subito.
    L'elenco degli asset lo chiede la vista con ue_content_list, così migliaia
    di righe non passano dal contesto del modello.
    """
    try:
        asset = await _tutti_gli_asset()
        stato = await run("result = mcp_project_status()")
    except Exception as exc:  # noqa: BLE001
        return {
            "error": str(exc),
            "project": None,
            "totale": 0,
            "conteggi": {},
            "levels": [],
        }

    conteggi: dict[str, int] = {}
    livelli: list[dict[str, Any]] = []
    for voce in asset:
        categoria = _categoria(str(voce.get("class", "")))
        conteggi[categoria] = conteggi.get(categoria, 0) + 1
        if categoria == "livelli":
            livelli.append(voce)

    progetto = None
    if isinstance(stato, dict) and stato.get("project_file"):
        progetto = Path(str(stato["project_file"])).stem

    return {
        "error": None,
        "project": progetto,
        "totale": len(asset),
        "conteggi": conteggi,
        # I livelli sono l'unica categoria quasi sempre corta, e sono ciò che
        # si vuole vedere per primo aprendo un progetto.
        "levels": sorted(livelli, key=lambda v: str(v.get("path", ""))),
    }


@mcp.tool()
async def ue_content_list(
    category: str | None = None, query: str | None = None, limit: int = 400
) -> ElencoAsset:
    """Elenco degli asset di una categoria. **Non chiamarlo direttamente.**

    Serve al pannello contenuti, che lo interroga per riempire la colonna
    dell'elenco. Su un progetto vero restituisce centinaia di righe: nel
    contesto del modello sono token buttati, e per cercare un asset da agente
    esiste ue_list_assets, che filtra per classe e per path.

    Args:
        category: una fra livelli, audio, blueprint, mesh, animazioni,
            materiali, texture, effetti, altro. Se omessa le prende tutte.
        query: sottostringa da cercare nel path.
        limit: massimo di righe restituite.
    """
    try:
        asset = await _tutti_gli_asset()
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "assets": [], "totale": 0, "troncato": False}

    trovati = [
        v for v in asset
        if (not category or _categoria(str(v.get("class", ""))) == category)
        and (not query or query.lower() in str(v.get("path", "")).lower())
    ]
    trovati.sort(key=lambda v: str(v.get("path", "")))
    tetto = max(1, int(limit))
    return {
        "error": None,
        "assets": trovati[:tetto],
        "totale": len(trovati),
        "troncato": len(trovati) > tetto,
    }


class StatoLavori(TypedDict):
    """Risultato di ue_jobs_status.

    Tipizzato e non `dict` per la stessa ragione degli altri pannelli: senza
    outputSchema FastMCP non popola structuredContent, e la vista dovrebbe
    ripiegare sul parsing del testo.
    """

    build: dict[str, Any]
    package: dict[str, Any]
    render: dict[str, Any]


@mcp.tool()
async def ue_jobs_status() -> StatoLavori:
    """Stato dei lavori lunghi avviati da questo MCP: build, packaging, render.

    Il pannello contenuti lo interroga per mostrare la barra di avanzamento.
    Non solleva se un lavoro non è mai partito: risponde `running: false`, che
    è l'informazione che serve.
    """
    lavori: dict[str, Any] = {}
    for nome, funzione in (
        ("build", local.build_status),
        ("package", local.package_status),
        ("render", local.render_status),
    ):
        try:
            lavori[nome] = await local_call(funzione, 0)
        except Exception as exc:  # noqa: BLE001
            # Un lavoro mai avviato non è un errore da propagare: il pannello
            # lo interroga ogni tre secondi, e sollevare farebbe lampeggiare
            # un errore per una condizione del tutto normale.
            lavori[nome] = {"running": False, "reason": str(exc)}
    return {
        "build": lavori["build"],
        "package": lavori["package"],
        "render": lavori["render"],
    }


@mcp.resource(
    ui.CONTENUTI_URI,
    name="Pannello contenuti",
    description="Interfaccia interattiva del pannello contenuti, servita all'host.",
    mime_type=ui.UI_MIME,
    meta={"ui": {"csp": ui.VIEWPORT_CSP}},
)
def resource_contenuti_ui() -> str:
    return ui.CONTENUTI_HTML


# ================================================================== networking


@mcp.tool()
async def ue_set_replication(
    blueprint_path: str,
    replicates: bool = True,
    replicate_movement: bool = True,
    always_relevant: bool = False,
) -> dict:
    """Configura la replication di un Blueprint: se e come viene sincronizzato
    fra server e client in una partita in rete."""
    return await run(
        f"result = mcp_set_replication({lit(blueprint_path)}, {bool(replicates)}, "
        f"{bool(replicate_movement)}, {bool(always_relevant)})"
    )


@mcp.tool()
async def ue_set_net_config(
    blueprint_path: str,
    dormancy: str | None = None,
    net_update_frequency: float | None = None,
    min_net_update_frequency: float | None = None,
    net_priority: float | None = None,
    net_cull_distance: float | None = None,
    only_relevant_to_owner: bool | None = None,
    net_use_owner_relevancy: bool | None = None,
    net_load_on_client: bool | None = None,
) -> dict:
    """Regola *quanto* costa in banda un attore replicato: dormancy, frequenza
    di aggiornamento, priorità e relevancy. `ue_set_replication` decide *se*
    replicare, questo decide con che intensità.

    Solo i parametri passati vengono toccati, gli altri restano come sono.

    Args:
        blueprint_path: Blueprint Actor da configurare.
        dormancy: "awake" | "initial" | "dormant_all" | "dormant_partial" | "never".
            "initial" è la scelta giusta per attori di scena che non cambiano
            mai dopo lo spawn: smettono di consumare banda finché non li si
            risveglia.
        net_update_frequency: aggiornamenti al secondo quando è rilevante (default UE 100).
        min_net_update_frequency: minimo a cui l'engine può scendere (default UE 2).
        net_priority: peso relativo nella coda di replication (default UE 1.0).
        net_cull_distance: distanza in cm oltre la quale il client smette di
            riceverlo (default UE 15000). Scritta come quadrato, che è come
            Unreal la memorizza.
        only_relevant_to_owner: replica solo al client che lo possiede.
        net_use_owner_relevancy: eredita la relevancy dall'owner.
        net_load_on_client: se False l'attore non viene creato sui client al
            caricamento del livello.
    """
    return await run(
        "result = mcp_set_net_config("
        f"{lit(blueprint_path)}, {lit(dormancy)}, {lit(net_update_frequency)}, "
        f"{lit(min_net_update_frequency)}, {lit(net_priority)}, {lit(net_cull_distance)}, "
        f"{lit(only_relevant_to_owner)}, {lit(net_use_owner_relevancy)}, {lit(net_load_on_client)})"
    )


@mcp.tool()
async def ue_net_info(blueprint_path: str) -> dict:
    """Stato di rete completo di un Blueprint: replication, dormancy,
    frequenze, priorità, relevancy e quali componenti replicano."""
    return await run(f"result = mcp_net_info({lit(blueprint_path)})")


@mcp.tool()
async def ue_set_component_replication(
    blueprint_path: str, component_name: str, replicates: bool = True
) -> dict:
    """Attiva la replication di un singolo componente di un Blueprint.

    Un attore replicato non replica automaticamente i suoi componenti: le
    proprietà di un componente arrivano ai client solo se il componente è
    marcato come replicato.

    Args:
        blueprint_path: Blueprint che contiene il componente.
        component_name: nome come si vede nell'editor (il suffisso
            `_GEN_VARIABLE` dei template è gestito da solo).
        replicates: True per attivare.
    """
    return await run(
        "result = mcp_set_component_replication("
        f"{lit(blueprint_path)}, {lit(component_name)}, {bool(replicates)})"
    )


@mcp.tool()
async def ue_set_component_default(
    blueprint_path: str, component_name: str, property_name: str, value: object
) -> dict:
    """Scrive una proprietà sul *template* di un componente di un Blueprint,
    non su un'istanza piazzata nel livello.

    È la via per le proprietà `EditDefaultsOnly`, che Unreal rifiuta di
    scrivere su un attore spawnato ("cannot be edited on instances") — per
    esempio `SensesConfig` di un AIPerceptionComponent. Per gli attori già
    nel livello usa invece `ue_set_actor_property`.

    Args:
        blueprint_path: Blueprint che contiene il componente.
        component_name: nome come si vede nell'editor.
        property_name: nome della proprietà (snake_case o PascalCase secondo
            quanto accetta la classe).
        value: valore JSON; dict con x/y/z, pitch/yaw/roll o r/g/b vengono
            convertiti nei tipi Unreal corrispondenti, e i path `/Game/...`
            vengono caricati come asset.
    """
    return await run(
        "result = mcp_set_component_default("
        f"{lit(blueprint_path)}, {lit(component_name)}, {lit(property_name)}, {lit(value)})"
    )


@mcp.tool()
async def ue_configure_pie(
    num_players: int = 2, net_mode: str = "listen_server", one_process: bool = True
) -> dict:
    """Configura il Play In Editor multi-client, per provare il multiplayer in locale.

    Args:
        num_players: numero di finestre client (server incluso in listen_server).
        net_mode: "standalone" | "listen_server" | "client".
        one_process: True = più finestre nello stesso processo (più veloce da avviare).
    """
    return await run(
        f"result = mcp_configure_pie({int(num_players)}, {lit(net_mode)}, {bool(one_process)})"
    )


@mcp.tool()
async def ue_start_pie(mode: str = "play") -> dict:
    """Avvia il Play In Editor con le impostazioni correnti.

    Args:
        mode: "play" (predefinito) è il Play vero, Alt+P — parte il GameMode, il
            PlayerController possiede il pawn, l'input del giocatore arriva.
            "simulate" è Simulate, Alt+S — il mondo gira ma nessuno possiede un
            pawn e l'input non è instradato: utile per osservare la fisica o una
            IA senza giocatore.
    """
    return await run(f"result = mcp_start_pie({lit(mode)})")


@mcp.tool()
async def ue_stop_pie() -> dict:
    """Ferma la sessione Play In Editor in corso."""
    return await run("result = mcp_stop_pie()")


@mcp.tool()
async def ue_set_project_setting(
    section: str, key: str, value: str, config: str = "Game"
) -> dict:
    """Scrive un'impostazione nei file Config/Default<config>.ini del progetto.

    Args:
        section: es. "/Script/EngineSettings.GameMapsSettings".
        key: es. "GlobalDefaultGameMode".
        value: es. "/Game/MyGame/Blueprints/BP_MyGameMode.BP_MyGameMode_C".
        config: "Game" | "Engine" | "Input".

    Nota: alcune impostazioni richiedono il riavvio dell'editor per avere effetto.
    """
    return await run(
        f"result = mcp_set_project_setting({lit(section)}, {lit(key)}, {lit(value)}, {lit(config)})"
    )


# ======================================================================= audio


@mcp.tool()
async def ue_import_audio(
    files: list[str], destination: str = "/Game/Audio"
) -> list[dict]:
    """Importa file .wav come SoundWave nel Content Browser."""
    return await run(f"result = mcp_import_assets({lit(files)}, {lit(destination)}, True, False)")


@mcp.tool()
async def ue_create_metasound_source(
    package_path: str = "/Game/Audio", name: str = "MS_Source"
) -> dict:
    """Crea un asset MetaSound Source vuoto (richiede il plugin MetaSound attivo).

    Il grafo va poi popolato nell'editor MetaSound o via ue_exec_python.
    """
    return await run(
        f"result = mcp_create_metasound_source({lit(package_path)}, {lit(name)})"
    )


@mcp.tool()
async def ue_create_sound_cue(
    package_path: str, name: str, wave_path: str | None = None
) -> dict:
    """Crea un Sound Cue, opzionalmente già collegato a un SoundWave importato.

    Args:
        package_path: es. "/Game/MyGame/Audio".
        name: es. "SC_Explosion".
        wave_path: path del SoundWave, es. "/Game/MyGame/Audio/explosion".
    """
    return await run(
        f"result = mcp_create_sound_cue({lit(package_path)}, {lit(name)}, {lit(wave_path)})"
    )


# ================================================================== reflection


@mcp.tool()
async def ue_find_classes(
    parent: str, name_contains: str | None = None, limit: int = 200
) -> dict:
    """Elenca le classi (native e Blueprint) derivate da una classe base, base inclusa.

    Copre il caso "che sottoclassi di Character esistono nel progetto?" o
    "elencami le luci disponibili": la Blueprint del progetto compare col
    suo nome generato (es. "BP_PlayerCharacter_C").

    Args:
        parent: nome esposto ai binding Python (es. "Character", "Actor") o
            percorso completo (es. "/Script/Engine.Light",
            "/Game/.../BP_Nemico.BP_Nemico_C").
        name_contains: filtro case-insensitive sul nome, opzionale.
        limit: massimo numero di risultati.
    """
    return await run(
        f"result = mcp_find_classes({lit(parent)}, {lit(name_contains)}, {int(limit)})"
    )


@mcp.tool()
async def ue_find_structs(
    parent: str, name_contains: str | None = None, limit: int = 200
) -> dict:
    """Elenca gli struct derivati da uno struct base, base incluso.

    Args:
        parent: nome esposto ai binding Python (es. "Vector") o percorso
            completo (es. "/Script/CoreUObject.Vector").
        name_contains: filtro case-insensitive sul nome, opzionale.
        limit: massimo numero di risultati.
    """
    return await run(
        f"result = mcp_find_structs({lit(parent)}, {lit(name_contains)}, {int(limit)})"
    )


@mcp.tool()
async def ue_reflect_enum(enum_name: str) -> dict:
    """Elenca nome, valore numerico e display name di un enum nativo del motore.

    Il nome va passato senza il prefisso "E" delle UENUM C++: "CollisionChannel",
    non "ECollisionChannel". Non copre gli enum definiti come Blueprint asset
    (`UserDefinedEnum` in /Game/...): quelli non hanno un binding Python
    generato, vanno letti con ue_exec_python e `unreal.load_asset(path)`.

    Args:
        enum_name: es. "CollisionChannel", "ObjectTypeQuery".
    """
    return await run(f"result = mcp_reflect_enum({lit(enum_name)})")


# ========================================================================= UMG


@mcp.tool()
async def ue_create_widget_blueprint(
    package_path: str,
    name: str,
    parent_class: str = "UserWidget",
    editor_utility: bool = False,
) -> dict:
    """Crea un asset Widget Blueprint (UMG) vuoto.

    **L'albero nasce senza radice**, e `WidgetTree.RootWidget` non è
    scrivibile da Python: per costruire un layout con i tool `ue_umg_*` parti
    invece da `ue_duplicate_asset` di un Widget Blueprint che una radice ce
    l'ha già, e svuotalo con `ue_umg_remove_widget`. Questo tool va bene
    quando la radice la metti a mano nel Widget Designer, o quando il Widget
    Blueprint serve solo come asset da riempire dopo.

    Args:
        package_path: es. "/Game/UI".
        name: es. "WBP_MainMenu".
        parent_class: nome esposto ai binding Python (es. "UserWidget") o
            percorso completo di una classe C++ del progetto, per la via
            BindWidget.
        editor_utility: True per un Editor Utility Widget (tool per l'editor,
            non per il gioco) invece di un Widget Blueprint normale.
    """
    return await run(
        f"result = mcp_create_widget_blueprint({lit(package_path)}, {lit(name)}, "
        f"{lit(parent_class)}, {lit(editor_utility)})"
    )


# ================================================================== UMG layout
#
# Il `WidgetTree` è una proprietà protetta, ma l'oggetto che sta dietro è un
# subobject del Widget Blueprint e si raggiunge per nome: da lì il layout è
# authorabile davvero (verificato dal vivo su UE 5.8).
#
# **Un limite resta**: `RootWidget` non è scrivibile, quindi il primo widget
# di un albero vuoto non è creabile da qui. La radice dev'esserci già —
# mettila nel Widget Designer, oppure duplica con `ue_duplicate_asset` un
# Widget Blueprint che ce l'ha e svuotalo.
#
# I widget si indirizzano per nome ("Titolo", "CanvasPanel_0"): sono univoci
# dentro un albero, e sono gli stessi che si vedono nel pannello Hierarchy.


@mcp.tool()
async def ue_umg_tree_info(widget_blueprint_path: str) -> dict:
    """La gerarchia dei widget di un Widget Blueprint: nomi, classi, figli e
    tipo di slot.

    `root: null` vuol dire albero vuoto — lì non si può aggiungere niente
    finché non c'è una radice.
    """
    return await run(f"result = mcp_umg_tree_info({lit(widget_blueprint_path)})")


@mcp.tool()
async def ue_umg_add_widget(
    widget_blueprint_path: str,
    widget_class: str,
    parent: str | None = None,
    name: str | None = None,
    slot: dict | None = None,
) -> dict:
    """Crea un widget e lo mette sotto un pannello dell'albero.

    Args:
        widget_blueprint_path: es. "/Game/UI/WBP_MainMenu".
        widget_class: "TextBlock", "Button", "Image", "VerticalBox",
            "HorizontalBox", "CanvasPanel", "Overlay", "Border", "ProgressBar"…
        parent: nome del pannello che lo conterrà; se omesso, la radice.
            Dev'essere un PanelWidget: un TextBlock non può contenere nulla.
        name: nome del widget, quello che si vedrà in Hierarchy e che serve
            per `BindWidget` da C++. Se omesso lo sceglie Unreal.
        slot: layout dentro il pannello, applicato subito — le stesse chiavi
            di `ue_umg_set_slot`.
    """
    return await run(
        "result = mcp_umg_add_widget("
        f"{lit(widget_blueprint_path)}, {lit(widget_class)}, {lit(parent)}, "
        f"{lit(name)}, {lit(slot)})"
    )


@mcp.tool()
async def ue_umg_set_widget_property(
    widget_blueprint_path: str, widget: str, properties: dict
) -> dict:
    """Imposta proprietà su un widget: testo, colore, visibilità, immagine.

    Le proprietà di testo sono `FText` nel motore: passa una stringa normale,
    la conversione è automatica. I dict con x/y/z, pitch/yaw/roll o r/g/b
    diventano i tipi Unreal corrispondenti, e i path "/Game/..." vengono
    caricati come asset.

    Restituisce `applied` e `failed` separati: una proprietà sbagliata non fa
    fallire le altre.
    """
    return await run(
        "result = mcp_umg_set_widget_property("
        f"{lit(widget_blueprint_path)}, {lit(widget)}, {lit(properties)})"
    )


@mcp.tool()
async def ue_umg_set_slot(widget_blueprint_path: str, widget: str, properties: dict) -> dict:
    """Imposta il layout di un widget dentro il suo pannello.

    Le chiavi valide dipendono dal pannello che lo contiene, e
    `ue_umg_tree_info` riporta `slot_class` per saperlo:

    - `CanvasPanelSlot`: `position` [x, y], `size` [x, y], `z_order`,
      `alignment`, `auto_size`.
    - `VerticalBoxSlot` / `HorizontalBoxSlot`: `padding` (numero singolo, o
      [left, top, right, bottom], o {"left":…}), `horizontal_alignment`,
      `vertical_alignment`.

    Le liste di 2 numeri diventano `Vector2D`, quelle di 4 un `Margin`.
    """
    return await run(
        f"result = mcp_umg_set_slot({lit(widget_blueprint_path)}, {lit(widget)}, {lit(properties)})"
    )


@mcp.tool()
async def ue_umg_remove_widget(widget_blueprint_path: str, widget: str) -> dict:
    """Toglie un widget dall'albero, con tutto quello che contiene.

    La radice non si può togliere (`RootWidget` non è scrivibile): per
    svuotare un albero, rimuovi i figli della radice.
    """
    return await run(
        f"result = mcp_umg_remove_widget({lit(widget_blueprint_path)}, {lit(widget)})"
    )


# ============================================================ blueprint graph
#
# Tre scorciatoie per compiti specifici: elencare i grafi, elencare gli
# eventi, aggiungere un override di evento o un grafo funzione. L'authoring
# vero dei nodi sta nella sezione "blueprint graph authoring" più sotto —
# quella che la fase 3 aveva dichiarato impossibile e la fase 11 ha smentito.


@mcp.tool()
async def ue_bp_list_graphs(blueprint_path: str) -> dict:
    """Elenca i grafi di un Blueprint (EventGraph, UserConstructionScript, funzioni...).

    Args:
        blueprint_path: es. "/Game/MyGame/BP_Player".
    """
    return await run(f"result = mcp_bp_list_graphs({lit(blueprint_path)})")


@mcp.tool()
async def ue_bp_list_events(blueprint_path: str) -> dict:
    """Elenca gli eventi visibili su un Blueprint: custom, ereditati overridabili, di interfaccia.

    `is_implemented` dice se esiste già un nodo per quell'evento nel grafo.

    Args:
        blueprint_path: es. "/Game/MyGame/BP_Player".
    """
    return await run(f"result = mcp_bp_list_events({lit(blueprint_path)})")


@mcp.tool()
async def ue_bp_add_event_override(
    blueprint_path: str, event_name: str, x: int = 0, y: int = 0
) -> dict:
    """Aggiunge (o ritrova) il nodo di un evento ereditato overridabile nell'event graph.

    Restituisce il path del nodo e i suoi pin — l'unico modo per riferirsi a
    quel nodo in seguito con ue_bp_connect_pins, dato che il grafo non elenca
    i propri nodi via Python.

    Args:
        blueprint_path: es. "/Game/MyGame/BP_Player".
        event_name: nome dell'evento ereditato, es. "ReceiveBeginPlay",
            "ReceiveTick", "ReceiveEndPlay". Vedi ue_bp_list_events per i nomi
            disponibili su questo Blueprint.
        x: posizione orizzontale del nodo nel grafo (solo visuale).
        y: posizione verticale del nodo nel grafo (solo visuale).
    """
    return await run(
        f"result = mcp_bp_add_event_override({lit(blueprint_path)}, {lit(event_name)}, "
        f"{int(x)}, {int(y)})"
    )


@mcp.tool()
async def ue_bp_add_function_graph(blueprint_path: str, func_name: str) -> dict:
    """Crea un grafo funzione vuoto (nodi Entry/Return di default).

    I nodi interni non sono raggiungibili da qui: il corpo va scritto a mano
    nel Blueprint Editor, o lasciato vuoto come slot da riempire in seguito.

    Args:
        blueprint_path: es. "/Game/MyGame/BP_Player".
        func_name: es. "ApriPorta".
    """
    return await run(
        f"result = mcp_bp_add_function_graph({lit(blueprint_path)}, {lit(func_name)})"
    )


# ================================================== blueprint graph authoring
#
# **UE 5.8+.** `ue_status` lo riporta in `capabilities.blueprint_graph_authoring`;
# sui motori che non ce l'hanno questi tool falliscono con un messaggio
# esplicito, e la via resta la classe C++ padre.
#
# Un nodo si indirizza col suo *nome oggetto* (`K2Node_CallFunction_0`), che
# ogni tool restituisce quando crea il nodo. I *titoli* invece seguono la
# lingua dell'editor ("Ramo" per Branch) e non vanno usati come chiave. Per i
# nodi evento, che esistono già nel grafo, c'è l'alias `event:<NomeMembro>`
# (es. `event:ReceiveBeginPlay`).
#
# Flusso tipico: `ue_bp_add_call_function` -> `ue_bp_connect` dal pin `then`
# dell'evento al pin `execute` del nodo -> `ue_bp_set_pin_value` per i
# letterali -> `ue_bp_graph_info` per rileggere e controllare `errors`.


@mcp.tool()
async def ue_bp_graph_info(blueprint_path: str, graph_name: str = "EventGraph") -> dict:
    """Nodi, pin, connessioni ed errori di compilazione di un grafo Blueprint.

    È il punto di partenza: dà i nomi oggetto dei nodi da usare in tutti gli
    altri tool, e `errors`/`warnings` dicono se il grafo compila.

    Args:
        blueprint_path: es. "/Game/MyGame/BP_Player".
        graph_name: nome oggetto del grafo ("EventGraph",
            "UserConstructionScript", o il nome di una funzione), non il
            titolo tradotto. `ue_bp_list_graphs` li elenca.
    """
    return await run(f"result = mcp_bp_graph_info({lit(blueprint_path)}, {lit(graph_name)})")


@mcp.tool()
async def ue_bp_add_call_function(
    blueprint_path: str,
    function_path: str,
    graph_name: str = "EventGraph",
    position: dict | list | None = None,
) -> dict:
    """Aggiunge un nodo di chiamata a funzione e restituisce i suoi pin.

    Args:
        blueprint_path: Blueprint da modificare.
        function_path: "/Script/<Modulo>.<Classe>:<Funzione>", per esempio
            "/Script/Engine.KismetSystemLibrary:PrintString" o
            "/Script/Engine.GameplayStatics:GetPlayerPawn".
        graph_name: grafo di destinazione.
        position: {"x": .., "y": ..} o [x, y], solo estetica.
    """
    return await run(
        "result = mcp_bp_add_call_function("
        f"{lit(blueprint_path)}, {lit(function_path)}, {lit(graph_name)}, {lit(position)})"
    )


@mcp.tool()
async def ue_bp_add_branch(
    blueprint_path: str, graph_name: str = "EventGraph", position: dict | list | None = None
) -> dict:
    """Aggiunge un nodo Branch (if/then/else): pin `Condition` in ingresso,
    `then` e `else` in uscita."""
    return await run(
        f"result = mcp_bp_add_branch({lit(blueprint_path)}, {lit(graph_name)}, {lit(position)})"
    )


@mcp.tool()
async def ue_bp_add_custom_event(
    blueprint_path: str,
    event_name: str,
    graph_name: str = "EventGraph",
    position: dict | list | None = None,
) -> dict:
    """Aggiunge un Custom Event. Solo nei grafi evento: una funzione non può
    contenerne."""
    return await run(
        "result = mcp_bp_add_custom_event("
        f"{lit(blueprint_path)}, {lit(event_name)}, {lit(graph_name)}, {lit(position)})"
    )


@mcp.tool()
async def ue_bp_add_variable_node(
    blueprint_path: str,
    variable_name: str,
    mode: str = "get",
    graph_name: str = "EventGraph",
    position: dict | list | None = None,
    class_path: str = "",
) -> dict:
    """Aggiunge un nodo Get o Set per una variabile membro.

    La variabile dev'essere già stata creata con `ue_add_variable`.

    Args:
        blueprint_path: Blueprint da modificare.
        variable_name: nome della variabile.
        mode: "get" o "set".
        graph_name: grafo di destinazione.
        position: {"x": .., "y": ..} o [x, y].
        class_path: per leggere una variabile di un'altra classe; vuoto = questo Blueprint.
    """
    return await run(
        "result = mcp_bp_add_variable_node("
        f"{lit(blueprint_path)}, {lit(variable_name)}, {lit(mode)}, {lit(graph_name)}, "
        f"{lit(position)}, {lit(class_path)})"
    )


@mcp.tool()
async def ue_bp_add_node_by_name(
    blueprint_path: str,
    node_name: str,
    graph_name: str = "EventGraph",
    position: dict | list | None = None,
) -> dict:
    """Aggiunge un nodo qualunque dalla palette, per "Categoria|Nome".

    Ultima spiaggia per i nodi che non hanno un tool dedicato. **I nomi sono
    localizzati come l'editor**: su un editor italiano il Branch è
    "Utilità|ControlloDiFlusso|Ramo", non "Utilities|FlowControl|Branch".
    Cerca la stringa esatta con `ue_bp_list_palette` prima di chiamare questo,
    o usa i tool tipizzati che non hanno il problema.
    """
    return await run(
        "result = mcp_bp_add_node_by_name("
        f"{lit(blueprint_path)}, {lit(node_name)}, {lit(graph_name)}, {lit(position)})"
    )


@mcp.tool()
async def ue_bp_list_palette(
    blueprint_path: str, graph_name: str = "EventGraph", contains: str | None = None, limit: int = 60
) -> dict:
    """Cerca fra i nodi aggiungibili a un grafo, filtrando per sottostringa.

    La palette completa ha migliaia di voci e segue la lingua dell'editor:
    filtrare è l'unico modo pratico di trovare il nome esatto da passare a
    `ue_bp_add_node_by_name`.

    Args:
        blueprint_path: Blueprint di riferimento.
        graph_name: grafo di cui si vuole la palette.
        contains: sottostringa da cercare, senza distinzione di maiuscole.
        limit: quante corrispondenze restituire.
    """
    return await run(
        "result = mcp_bp_list_palette("
        f"{lit(blueprint_path)}, {lit(graph_name)}, {lit(contains)}, {int(limit)})"
    )


@mcp.tool()
async def ue_bp_connect(
    blueprint_path: str,
    from_node: str,
    from_pin: str,
    to_node: str,
    to_pin: str,
    graph_name: str = "EventGraph",
) -> dict:
    """Collega un pin di uscita di un nodo a un pin di ingresso di un altro.

    Args:
        blueprint_path: Blueprint da modificare.
        from_node: nome oggetto del nodo di partenza, o `event:<NomeMembro>`
            (es. "event:ReceiveBeginPlay") per un nodo evento.
        from_pin: pin di uscita, es. "then" per il filo di esecuzione.
        to_node: nome oggetto del nodo di arrivo.
        to_pin: pin di ingresso, es. "execute".
        graph_name: grafo su cui lavorare.

    Se i tipi non sono compatibili il tool lo dice, riportando i due tipi
    invece di fallire in silenzio.
    """
    return await run(
        "result = mcp_bp_connect("
        f"{lit(blueprint_path)}, {lit(from_node)}, {lit(from_pin)}, {lit(to_node)}, "
        f"{lit(to_pin)}, {lit(graph_name)})"
    )


@mcp.tool()
async def ue_bp_break_pin(
    blueprint_path: str, node: str, pin: str, graph_name: str = "EventGraph"
) -> dict:
    """Stacca tutti i collegamenti di un pin, e riporta quanti erano."""
    return await run(
        f"result = mcp_bp_break_pin({lit(blueprint_path)}, {lit(node)}, {lit(pin)}, {lit(graph_name)})"
    )


@mcp.tool()
async def ue_bp_set_pin_value(
    blueprint_path: str, node: str, pin: str, value: object, graph_name: str = "EventGraph"
) -> dict:
    """Scrive il valore letterale di un pin di ingresso non collegato.

    **Unreal non valida il valore**: verificato dal vivo che scrivere
    "non_un_bool" su un pin booleano viene accettato e memorizzato così com'è.
    Per questo il tool rilegge sempre il pin dopo la scrittura e restituisce
    il valore vero — controllalo, invece di fidarti del successo.
    """
    return await run(
        "result = mcp_bp_set_pin_value("
        f"{lit(blueprint_path)}, {lit(node)}, {lit(pin)}, {lit(value)}, {lit(graph_name)})"
    )


@mcp.tool()
async def ue_bp_remove_node(
    blueprint_path: str, node: str, graph_name: str = "EventGraph"
) -> dict:
    """Cancella un nodo dal grafo, con tutti i suoi collegamenti."""
    return await run(
        f"result = mcp_bp_remove_node({lit(blueprint_path)}, {lit(node)}, {lit(graph_name)})"
    )


# ======================================================================= animazione
#
# A differenza di UMG e del grafo Blueprint (fasi 2-3), qui la scrittura
# funziona davvero: i dati di BlendSpace sono array di struct ordinari, non
# protetti, verificato dal vivo salvando e ricaricando l'asset da zero.
# L'AnimGraph di un Anim Blueprint resta invece un EdGraph come gli altri:
# stesso muro, quindi ue_create_anim_blueprint crea solo l'asset.


@mcp.tool()
async def ue_skeleton_info(skeleton_path: str) -> dict:
    """Elenca ossa e socket di uno Skeleton, dalla reference pose.

    Args:
        skeleton_path: es. "/Game/MyGame/Characters/Hero/Hero_Skeleton".
    """
    return await run(f"result = mcp_skeleton_info({lit(skeleton_path)})")


@mcp.tool()
async def ue_anim_sequence_info(anim_path: str) -> dict:
    """Durata, notify, sync marker e curve di un AnimSequence.

    Args:
        anim_path: es. "/Game/MyGame/Characters/Hero/Animations/Idle".
    """
    return await run(f"result = mcp_anim_sequence_info({lit(anim_path)})")


@mcp.tool()
async def ue_create_blend_space_1d(
    package_path: str,
    name: str,
    skeleton_path: str,
    axis_name: str = "Speed",
    axis_min: float = 0.0,
    axis_max: float = 1.0,
    grid_num: int = 4,
    samples: list[dict] | None = None,
) -> dict:
    """Crea un BlendSpace1D con un asse e, opzionalmente, i suoi sample.

    Solo 1D per ora: BlendSpace (2D) usa la stessa struttura dati ma non è
    stata verificata dal vivo in questa fase.

    Args:
        package_path: es. "/Game/MyGame/Characters/Hero/Animations".
        name: es. "BS_Locomotion".
        skeleton_path: Skeleton a cui il BlendSpace è associato.
        axis_name: nome dell'asse, es. "Speed".
        axis_min: valore minimo dell'asse.
        axis_max: valore massimo dell'asse.
        grid_num: numero di suddivisioni della griglia.
        samples: lista di `{"value": float, "animation": path}`, es.
            `[{"value": 0, "animation": ".../Idle"}, {"value": 300, "animation": ".../Running"}]`.
    """
    return await run(
        f"result = mcp_create_blend_space_1d({lit(package_path)}, {lit(name)}, "
        f"{lit(skeleton_path)}, {lit(axis_name)}, {float(axis_min)}, {float(axis_max)}, "
        f"{int(grid_num)}, {lit(samples)})"
    )


@mcp.tool()
async def ue_create_anim_montage(package_path: str, name: str, source_animation_path: str) -> dict:
    """Crea un AnimMontage a partire da un AnimSequence esistente.

    Args:
        package_path: es. "/Game/MyGame/Characters/Hero/Animations".
        name: es. "AM_Attack".
        source_animation_path: AnimSequence da incapsulare nel montage.
    """
    return await run(
        f"result = mcp_create_anim_montage({lit(package_path)}, {lit(name)}, "
        f"{lit(source_animation_path)})"
    )


@mcp.tool()
async def ue_create_anim_blueprint(
    package_path: str, name: str, skeleton_path: str, parent_class: str = "AnimInstance"
) -> dict:
    """Crea l'asset Anim Blueprint associato a uno Skeleton.

    L'AnimGraph non è raggiungibile da qui (stesso limite del grafo
    Blueprint): va disegnato a mano nell'Anim Blueprint Editor.

    Args:
        package_path: es. "/Game/MyGame/Characters/Hero".
        name: es. "ABP_Hero".
        skeleton_path: Skeleton a cui l'Anim Blueprint è associato.
        parent_class: nome esposto ai binding Python (default "AnimInstance")
            o percorso completo di una classe C++ del progetto.
    """
    return await run(
        f"result = mcp_create_anim_blueprint({lit(package_path)}, {lit(name)}, "
        f"{lit(skeleton_path)}, {lit(parent_class)})"
    )


# ========================================================================= niagara
#
# `EmitterHandles` di NiagaraSystem è protetta come `Nodes`/`WidgetTree`:
# niente aggiunta di emitter o moduli via Python, verificato anche su
# template popolati della libreria di sistema. L'introspezione di un sistema
# esistente invece funziona davvero e a livello di asset, senza bisogno del
# PIE in esecuzione.


@mcp.tool()
async def ue_create_niagara_system(package_path: str, name: str) -> dict:
    """Crea un asset Niagara System vuoto.

    L'emitter stack (aggiungere emitter, moduli, parametri) non è
    raggiungibile da qui — stesso limite del grafo Blueprint e del
    WidgetTree di UMG. Va costruito a mano nel Niagara Editor.

    Args:
        package_path: es. "/Game/MyGame/VFX".
        name: es. "NS_Explosion".
    """
    return await run(f"result = mcp_create_niagara_system({lit(package_path)}, {lit(name)})")


@mcp.tool()
async def ue_niagara_system_info(system_path: str) -> dict:
    """Emitter e parametri esposti (user parameters) di un Niagara System esistente.

    Funziona a livello di asset: non serve un PIE in esecuzione né un
    componente istanziato in scena.

    Args:
        system_path: es. "/Game/MyGame/VFX/NS_Explosion".
    """
    return await run(f"result = mcp_niagara_system_info({lit(system_path)})")


# ========================================================================= gameplay
#
# Fisica/collisione e navmesh sono pienamente scriptabili (verificato dal
# vivo). Blackboard e Behavior Tree rompono il pattern "grafo = protetto"
# delle sezioni precedenti: qui l'albero (RootNode, Children, Decorators,
# Services) SI scrive via Python, perché non sono un vero EdGraph ma UObject
# e struct normali. EQS resta bloccato come UMG/Blueprint/Niagara. L'AI
# Perception si aggiunge con ue_add_component generico, ma SensesConfig va
# configurato a mano (EditDefaultsOnly, non raggiungibile in modo affidabile
# dal component template via Python in tempi ragionevoli).


@mcp.tool()
async def ue_set_component_physics(
    actor: str,
    component: str,
    simulate_physics: bool | None = None,
    collision_enabled: str | None = None,
    collision_profile: str | None = None,
) -> dict:
    """Fisica e collisione di un componente (StaticMesh/Skeletal/Primitive).

    Args:
        actor: etichetta dell'attore nell'Outliner.
        component: nome o classe del componente (es. "StaticMeshComponent").
        simulate_physics: attiva/disattiva la simulazione fisica.
        collision_enabled: "NoCollision" | "QueryOnly" | "PhysicsOnly" |
            "QueryAndPhysics" | "QueryAndProbe" | "ProbeOnly" (case/underscore
            insensitive, es. va bene anche "query_and_physics").
        collision_profile: nome profilo collisione (es. "PhysicsActor", "BlockAll").
    """
    return await run(
        "result = mcp_set_component_physics("
        f"{lit(actor)}, {lit(component)}, {lit(simulate_physics)}, "
        f"{lit(collision_enabled)}, {lit(collision_profile)})"
    )


@mcp.tool()
async def ue_component_physics_info(actor: str, component: str) -> dict:
    """Stato fisica/collisione attuale di un componente."""
    return await run(f"result = mcp_component_physics_info({lit(actor)}, {lit(component)})")


@mcp.tool()
async def ue_nav_rebuild() -> dict:
    """Rigenera il navmesh del livello corrente (equivalente al comando
    console `RebuildNavigation`). Serve almeno un `NavMeshBoundsVolume` nel
    livello: piazzalo con `ue_spawn_actor` (classe "NavMeshBoundsVolume")."""
    return await run("result = mcp_nav_rebuild()")


@mcp.tool()
async def ue_nav_query_point(origin: dict, radius: float = 500.0) -> dict:
    """Trova un punto raggiungibile a caso sul navmesh entro un raggio da un'origine.

    Args:
        origin: {"x":.., "y":.., "z":..} in centimetri.
        radius: raggio di ricerca in centimetri.
    """
    return await run(f"result = mcp_nav_query_point({lit(origin)}, {lit(radius)})")


@mcp.tool()
async def ue_nav_find_path(start: dict, end: dict) -> dict:
    """Calcola un percorso sul navmesh tra due punti (pathfinding sincrono,
    non serve il PIE in esecuzione).

    Args:
        start, end: {"x":.., "y":.., "z":..} in centimetri.
    """
    return await run(f"result = mcp_nav_find_path({lit(start)}, {lit(end)})")


@mcp.tool()
async def ue_create_blackboard(package_path: str, name: str) -> dict:
    """Crea un asset Blackboard Data vuoto (solo la chiave "SelfActor" di default)."""
    return await run(f"result = mcp_create_blackboard({lit(package_path)}, {lit(name)})")


@mcp.tool()
async def ue_blackboard_add_key(blackboard_path: str, key_name: str, key_type: str = "object") -> dict:
    """Aggiunge una chiave a un Blackboard Data esistente.

    Args:
        blackboard_path: es. "/Game/MyGame/AI/BB_Guard".
        key_name: es. "TargetActor".
        key_type: object | class | bool | int | float | string | name |
            vector | rotator | enum.
    """
    return await run(
        f"result = mcp_blackboard_add_key({lit(blackboard_path)}, {lit(key_name)}, {lit(key_type)})"
    )


@mcp.tool()
async def ue_blackboard_info(blackboard_path: str) -> dict:
    """Elenca le chiavi di un Blackboard Data esistente."""
    return await run(f"result = mcp_blackboard_info({lit(blackboard_path)})")


@mcp.tool()
async def ue_create_behavior_tree(
    package_path: str,
    name: str,
    blackboard_path: str | None = None,
    root_composite: str = "BTComposite_Selector",
) -> dict:
    """Crea un Behavior Tree con un nodo radice già impostato (Selector o
    Sequence), opzionalmente collegato a un Blackboard.

    Args:
        package_path: es. "/Game/MyGame/AI".
        name: es. "BT_Guard".
        blackboard_path: Blackboard da collegare (opzionale).
        root_composite: "BTComposite_Selector" | "BTComposite_Sequence" |
            qualunque classe composite valida.
    """
    return await run(
        "result = mcp_create_behavior_tree("
        f"{lit(package_path)}, {lit(name)}, {lit(blackboard_path)}, {lit(root_composite)})"
    )


@mcp.tool()
async def ue_bt_add_node(bt_path: str, parent_path: str, node_class: str, index: int | None = None) -> dict:
    """Aggiunge un nodo (composite o task) come figlio di un nodo composite esistente.

    Il tipo di nodo (composite vs task) è dedotto dalla classe: se eredita da
    BTCompositeNode va in ChildComposite, altrimenti in ChildTask. Usa
    `ue_bt_info` per vedere i path esistenti prima di aggiungere.

    Args:
        bt_path: path del Behavior Tree.
        parent_path: "root" per la radice, oppure un path tipo "0" o "0.1"
            (indici dei figli separati da punto, restituiti da questa stessa
            funzione o da `ue_bt_info`).
        node_class: es. "BTComposite_Sequence", "BTTask_Wait", "BTTask_MoveTo".
        index: posizione tra i figli esistenti (in coda se omesso).
    """
    return await run(
        "result = mcp_bt_add_node("
        f"{lit(bt_path)}, {lit(parent_path)}, {lit(node_class)}, {lit(index)})"
    )


@mcp.tool()
async def ue_bt_add_decorator(bt_path: str, node_path: str, decorator_class: str) -> dict:
    """Aggiunge un decorator (condizione) al child link di un nodo.

    Args:
        bt_path: path del Behavior Tree.
        node_path: path del nodo (non può essere "root": la radice non ha un
            child link proprio), es. "0" o "0.1".
        decorator_class: es. "BTDecorator_Blackboard", "BTDecorator_Cooldown".
    """
    return await run(
        f"result = mcp_bt_add_decorator({lit(bt_path)}, {lit(node_path)}, {lit(decorator_class)})"
    )


@mcp.tool()
async def ue_bt_add_service(bt_path: str, node_path: str, service_class: str) -> dict:
    """Aggiunge un service a un nodo composite (solo Selector/Sequence, non i task).

    Args:
        bt_path: path del Behavior Tree.
        node_path: "root" o un path tipo "0" — deve essere un nodo composite.
        service_class: es. "BTService_DefaultFocus".
    """
    return await run(
        f"result = mcp_bt_add_service({lit(bt_path)}, {lit(node_path)}, {lit(service_class)})"
    )


@mcp.tool()
async def ue_bt_set_node_property(bt_path: str, node_path: str, property_name: str, value: Any) -> dict:
    """Imposta una proprietà su un nodo del Behavior Tree.

    Gestisce in automatico i campi bindable da blackboard (es.
    `BTTask_Wait.WaitTime`, uno struct `FValueOrBBKey_Float`): scrive nel
    valore di default fisso invece che nella chiave blackboard.

    Args:
        bt_path: path del Behavior Tree.
        node_path: "root" o un path tipo "0.1".
        property_name: nome della proprietà UE (es. "WaitTime", "BlackboardKey").
        value: valore JSON da scrivere (segue le stesse regole di ue_set_actor_property).
    """
    return await run(
        "result = mcp_bt_set_node_property("
        f"{lit(bt_path)}, {lit(node_path)}, {lit(property_name)}, {lit(value)})"
    )


@mcp.tool()
async def ue_bt_info(bt_path: str) -> dict:
    """Dump ricorsivo dell'albero di un Behavior Tree (nodi, decorator, service, path)."""
    return await run(f"result = mcp_bt_info({lit(bt_path)})")


@mcp.tool()
async def ue_create_eqs_asset(package_path: str, name: str) -> dict:
    """Crea un asset Environment Query (EQS) vuoto.

    Le query (Options, generator, test) non sono raggiungibili da qui — stesso
    limite del grafo Blueprint, del WidgetTree e dell'emitter stack Niagara.
    Va costruito a mano nell'EQS Editor.

    Args:
        package_path: es. "/Game/MyGame/AI".
        name: es. "EQS_FindCover".
    """
    return await run(f"result = mcp_create_eqs_asset({lit(package_path)}, {lit(name)})")


# ========================================================================= GAS
#
# Il plugin GameplayAbilities va abilitato (`ue_project_set_plugins`) ed
# editor riavviato prima che queste classi esistano in Python. GameplayEffect
# e AttributeSet sono Blueprint "normali": si creano già con
# `ue_create_blueprint` (`parent_class="GameplayEffect"`/`"AttributeSet"`).
# Un attributo si aggiunge a un AttributeSet con `ue_add_variable`
# (`var_type="struct"`, `sub_type="/Script/GameplayAbilities.GameplayAttributeData"`).
# Il muro reale — `GameplayModifierInfo.Attribute`/`.ModifierOp` rifiutano
# `set_editor_property` — è aggirato in `ue_ge_add_modifier` costruendo lo
# struct intero via `import_text`, verificato dal vivo persistere dopo
# salvataggio e ricarica dell'asset (non verificato in PIE).


@mcp.tool()
async def ue_create_gameplay_ability(
    package_path: str,
    name: str,
    instancing_policy: str | None = None,
    net_execution_policy: str | None = None,
) -> dict:
    """Crea un GameplayAbility Blueprint (asset dedicato, non un Blueprint generico).

    La logica dell'abilità (ActivateAbility, i suoi nodi) resta un EdGraph
    come tutti i grafi Blueprint — non scriptabile, va disegnata a mano.
    Le proprietà dati invece si impostano già qui.

    Args:
        package_path: es. "/Game/MyGame/Abilities".
        name: es. "GA_Dash".
        instancing_policy: "InstancedPerActor" | "InstancedPerExecution" | "NonInstanced".
        net_execution_policy: "LocalPredicted" | "LocalOnly" | "ServerInitiated" | "ServerOnly".
    """
    return await run(
        "result = mcp_create_gameplay_ability("
        f"{lit(package_path)}, {lit(name)}, {lit(instancing_policy)}, {lit(net_execution_policy)})"
    )


@mcp.tool()
async def ue_create_gameplay_effect(
    package_path: str, name: str, duration_policy: str | None = None, period: float | None = None
) -> dict:
    """Crea un GameplayEffect Blueprint (Blueprint generico con parent GameplayEffect).

    Args:
        package_path: es. "/Game/MyGame/Effects".
        name: es. "GE_Damage".
        duration_policy: "Instant" | "HasDuration" | "Infinite".
        period: intervallo di applicazione in secondi (per effetti periodici).
    """
    return await run(
        f"result = mcp_create_gameplay_effect({lit(package_path)}, {lit(name)}, {lit(duration_policy)}, {lit(period)})"
    )


@mcp.tool()
async def ue_ge_add_modifier(
    ge_path: str, attribute_set_path: str, attribute_name: str, modifier_op: str, magnitude: float
) -> dict:
    """Aggiunge un modifier a un GameplayEffect: collega un attributo di un
    AttributeSet Blueprint esistente, un'operazione e un valore fisso.

    Aggira un limite della Python API di UE (vedi nota sopra): il modo
    normale di costruire un modifier è bloccato, questo tool lo aggira con
    una tecnica di serializzazione testuale. Solo `ScalableFloat` costante,
    niente curve o attribute-based magnitude per ora.

    Args:
        ge_path: path del GameplayEffect Blueprint.
        attribute_set_path: path dell'AttributeSet Blueprint che possiede l'attributo.
        attribute_name: nome della variabile GameplayAttributeData su quell'AttributeSet (es. "Health").
        modifier_op: "add" | "add_final" | "multiply" | "divide" | "multiply_compound" | "override".
        magnitude: valore fisso applicato (es. -10 per un danno di 10).
    """
    return await run(
        "result = mcp_ge_add_modifier("
        f"{lit(ge_path)}, {lit(attribute_set_path)}, {lit(attribute_name)}, {lit(modifier_op)}, {lit(magnitude)})"
    )


@mcp.tool()
async def ue_ge_add_component(ge_path: str, component_class: str) -> dict:
    """Aggiunge un GameplayEffectComponent (es. "AssetTagsGameplayEffectComponent",
    "TargetTagRequirementsGameplayEffectComponent", "ChanceToApplyGameplayEffectComponent")
    a un GameplayEffect. Solo l'aggiunta: configurare tag/condizioni al suo
    interno non è coperto, usa `ue_exec_python` o l'editor."""
    return await run(f"result = mcp_ge_add_component({lit(ge_path)}, {lit(component_class)})")


@mcp.tool()
async def ue_ge_info(ge_path: str) -> dict:
    """Duration policy, periodo, modifier (attributo/operazione/valore) e
    GameplayEffectComponent di un GameplayEffect esistente."""
    return await run(f"result = mcp_ge_info({lit(ge_path)})")


# ======================================================================= landscape
#
# **Creare** un landscape da Python non si può, verificato dal vivo su UE 5.8:
# spawnare `Landscape` dà un `LandscapePlaceholder` vuoto, e le classi che lo
# creano davvero (`LandscapeSubsystem`, `LandscapeEditorObject`,
# `ActorFactoryLandscape`) non sono esposte al Python del motore. Il terreno
# va creato una volta con Landscape Mode nell'editor: da lì in poi heightmap,
# weightmap, materiale e grass si guidano da qui.


@mcp.tool()
async def ue_landscape_list() -> dict:
    """I landscape presenti nel livello corrente.

    Se la lista è vuota il livello non ha terreni, e nessun altro tool di
    questa famiglia ha su cosa lavorare: aggiungine uno dall'editor con
    Landscape Mode (Python non può crearlo)."""
    return await run("result = mcp_landscape_list()")


@mcp.tool()
async def ue_landscape_info(label: str | None = None) -> dict:
    """Componenti, materiale, target layer di pittura ed edit layer di un
    landscape. Con un solo landscape nel livello `label` si può omettere."""
    return await run(f"result = mcp_landscape_info({lit(label)})")


@mcp.tool()
async def ue_landscape_import_heightmap(
    image_path: str,
    label: str | None = None,
    rt_format: str = "RGBA8",
    from_rg_channel: bool = False,
) -> dict:
    """Sovrascrive l'heightmap di un landscape con un'immagine dal disco.

    L'immagine viene importata come texture e disegnata in un render target
    temporaneo, che è l'unica forma in cui Unreal accetta un heightmap da
    script. Sovrascrive il terreno esistente: non è annullabile oltre l'undo
    dell'editor.

    Args:
        image_path: file locale (PNG, EXR, TGA…). Va portato alla risoluzione
            del landscape prima: il render target non lo riscala.
        label: quale landscape, se il livello ne ha più di uno.
        rt_format: "RGBA8" (8 bit, 256 livelli di altezza) | "RGBA16f" |
            "RGBA32f". Per un heightmap a 16 bit serve un formato float.
        from_rg_channel: solo per i formati float — legge l'altezza dai canali
            R e G invece che dal solo R, che è come Unreal codifica i 16 bit.
    """
    return await run(
        "result = mcp_landscape_import_heightmap("
        f"{lit(image_path)}, {lit(label)}, {lit(rt_format)}, {bool(from_rg_channel)})"
    )


@mcp.tool()
async def ue_landscape_import_weightmap(
    layer_name: str, image_path: str, label: str | None = None, rt_format: str = "RGBA8"
) -> dict:
    """Dipinge un layer del landscape da un'immagine in scala di grigi
    (bianco = layer al massimo, nero = assente).

    Il layer deve già esistere: i target layer nascono dal materiale del
    landscape, `ue_landscape_info` li elenca.
    """
    return await run(
        "result = mcp_landscape_import_weightmap("
        f"{lit(layer_name)}, {lit(image_path)}, {lit(label)}, {lit(rt_format)})"
    )


@mcp.tool()
async def ue_landscape_export_heightmap(
    output_dir: str,
    file_name: str,
    label: str | None = None,
    resolution: int = 1024,
    rt_format: str = "RGBA8",
    into_rg_channel: bool = False,
) -> dict:
    """Esporta l'heightmap del landscape come immagine sul disco.

    Il formato del file lo decide il render target: RGBA8 esce in PNG, i
    formati float in HDR.

    Args:
        output_dir: cartella locale di destinazione.
        file_name: nome del file, estensione compresa.
        label: quale landscape, se il livello ne ha più di uno.
        resolution: lato del render target in pixel (quadrato).
        rt_format: "RGBA8" | "RGBA16f" | "RGBA32f".
        into_rg_channel: comprime i 16 bit di altezza nei canali R e G.
    """
    return await run(
        "result = mcp_landscape_export_heightmap("
        f"{lit(output_dir)}, {lit(file_name)}, {lit(label)}, {int(resolution)}, "
        f"{lit(rt_format)}, {bool(into_rg_channel)})"
    )


@mcp.tool()
async def ue_landscape_set_material(material_path: str, label: str | None = None) -> dict:
    """Assegna il materiale al landscape. È il materiale che definisce quali
    target layer si possono dipingere: cambiarlo cambia la lista."""
    return await run(f"result = mcp_landscape_set_material({lit(material_path)}, {lit(label)})")


@mcp.tool()
async def ue_landscape_set_grass(enabled: bool = True, label: str | None = None) -> dict:
    """Accende o spegne il grass system del landscape (l'erba procedurale
    generata dal materiale)."""
    return await run(f"result = mcp_landscape_set_grass({bool(enabled)}, {lit(label)})")


# ============================================================================ PCG
#
# La sorpresa della roadmap: a differenza di Blueprint, UMG e Niagara, il
# grafo PCG è pienamente scriptabile — nodi, archi, posizioni e proprietà,
# verificati dal vivo su UE 5.8 costruendo Input → SurfaceSampler →
# StaticMeshSpawner, salvando e rileggendo l'asset da zero. Il motivo è lo
# stesso dei Behavior Tree: è un grafo di dati veri, non un `EdGraph` di nodi
# K2 con il contenuto in una proprietà protetta.
#
# Il plugin PCG dev'essere abilitato nel progetto (`ue_project_set_plugins`).


@mcp.tool()
async def ue_create_pcg_graph(package_path: str, name: str) -> dict:
    """Crea un asset PCGGraph vuoto — con i suoi nodi Input e Output già dentro.

    Args:
        package_path: es. "/Game/MyGame/PCG".
        name: es. "PCG_Foresta".
    """
    return await run(f"result = mcp_create_pcg_graph({lit(package_path)}, {lit(name)})")


@mcp.tool()
async def ue_pcg_add_node(
    graph_path: str, settings_class: str, position: dict | list | None = None
) -> dict:
    """Aggiunge un nodo al grafo PCG e restituisce i suoi pin.

    In PCG il tipo di un nodo *è* la sua classe di settings: un SurfaceSampler
    è un nodo con `PCGSurfaceSamplerSettings`, uno spawner di mesh ha
    `PCGStaticMeshSpawnerSettings`. La convenzione è sempre
    `PCG<Nome>Settings`.

    Args:
        graph_path: path dell'asset PCGGraph.
        settings_class: es. "PCGSurfaceSamplerSettings", "PCGStaticMeshSpawnerSettings",
            "PCGCreatePointsGridSettings", "PCGDensityFilterSettings".
        position: posizione nell'editor del grafo, {"x": .., "y": ..} o [x, y].
            Serve solo alla leggibilità per chi apre il grafo a mano.
    """
    return await run(
        f"result = mcp_pcg_add_node({lit(graph_path)}, {lit(settings_class)}, {lit(position)})"
    )


@mcp.tool()
async def ue_pcg_connect(
    graph_path: str, from_node: str, from_pin: str, to_node: str, to_pin: str
) -> dict:
    """Collega l'uscita di un nodo PCG all'ingresso di un altro.

    `from_node` e `to_node` accettano il nome del nodo (come lo restituisce
    `ue_pcg_add_node`) oppure gli alias "input" e "output" per i due nodi che
    ogni grafo ha già. I nomi dei pin sono quelli elencati da
    `ue_pcg_add_node` / `ue_pcg_graph_info`, spazi compresi ("Bounding Shape").
    """
    return await run(
        "result = mcp_pcg_connect("
        f"{lit(graph_path)}, {lit(from_node)}, {lit(from_pin)}, {lit(to_node)}, {lit(to_pin)})"
    )


@mcp.tool()
async def ue_pcg_disconnect(
    graph_path: str, from_node: str, from_pin: str, to_node: str, to_pin: str
) -> dict:
    """Rimuove un collegamento fra due nodi del grafo PCG."""
    return await run(
        "result = mcp_pcg_disconnect("
        f"{lit(graph_path)}, {lit(from_node)}, {lit(from_pin)}, {lit(to_node)}, {lit(to_pin)})"
    )


@mcp.tool()
async def ue_pcg_remove_node(graph_path: str, node: str) -> dict:
    """Toglie un nodo dal grafo PCG, con tutti i suoi collegamenti."""
    return await run(f"result = mcp_pcg_remove_node({lit(graph_path)}, {lit(node)})")


@mcp.tool()
async def ue_pcg_set_node_property(
    graph_path: str, node: str, property_name: str, value: object
) -> dict:
    """Imposta una proprietà sulle settings di un nodo PCG.

    Args:
        graph_path: path del grafo.
        node: nome del nodo.
        property_name: es. "points_per_squared_meter" su un SurfaceSampler,
            "seed" su quasi tutti i nodi.
        value: valore JSON; i path "/Game/..." vengono caricati come asset.
    """
    return await run(
        "result = mcp_pcg_set_node_property("
        f"{lit(graph_path)}, {lit(node)}, {lit(property_name)}, {lit(value)})"
    )


@mcp.tool()
async def ue_pcg_graph_info(graph_path: str) -> dict:
    """Nodi (nome, classe di settings, pin, posizione) e archi di un grafo PCG.

    È il modo di sapere come si chiamano i pin prima di collegarli."""
    return await run(f"result = mcp_pcg_graph_info({lit(graph_path)})")


@mcp.tool()
async def ue_pcg_spawn_volume(
    graph_path: str,
    label: str | None = None,
    location: dict | list | None = None,
    size: dict | list | None = None,
) -> dict:
    """Piazza un PCGVolume nel livello con il grafo già collegato.

    Il volume è il dominio in cui il grafo lavora: senza, il grafo non ha
    niente su cui generare.

    Args:
        graph_path: grafo da collegare.
        label: nome dell'attore nel livello.
        location: centro del volume in cm. Attenzione al centro del mondo: se
            il livello è costruito lontano da [0,0,0], lì il volume è fuori
            campo — leggi prima un attore di riferimento con `ue_list_actors`.
        size: dimensioni in cm, {"x":..,"y":..,"z":..}. Default 200×200×200.
    """
    return await run(
        "result = mcp_pcg_spawn_volume("
        f"{lit(graph_path)}, {lit(label)}, {lit(location)}, {lit(size)})"
    )


@mcp.tool()
async def ue_pcg_generate(label: str, force: bool = True) -> dict:
    """Fa rigenerare il PCG di un attore (un PCGVolume o qualunque attore con
    un PCGComponent). Da chiamare dopo aver modificato il grafo."""
    return await run(f"result = mcp_pcg_generate({lit(label)}, {bool(force)})")


@mcp.tool()
async def ue_pcg_cleanup(label: str, remove_components: bool = True) -> dict:
    """Cancella quello che il PCG ha generato su un attore, lasciando il grafo
    e il volume al loro posto."""
    return await run(f"result = mcp_pcg_cleanup({lit(label)}, {bool(remove_components)})")


# ======================================================================= FOLIAGE
#
# Il gap più citato nel confronto con db-lyon/ue-mcp, e si è rivelato
# interamente scriptabile — ma non da `EditorFoliageLibrary`, che su UE 5.8 non
# esiste proprio. Si passa da `InstancedFoliageActor.add_instances` e dai
# `FoliageInstancedStaticMeshComponent` del livello.
#
# Attenzione a `FoliageStatistics`: è la libreria che *sembra* fatta per
# contare le istanze, e nel mondo dell'editor risponde sempre 0 (verificato dal
# vivo su un box con 5 istanze dentro). `ue_foliage_query` non la usa.


@mcp.tool()
async def ue_create_foliage_type(
    package_path: str, name: str, mesh_path: str, properties: dict | None = None
) -> dict:
    """Crea un FoliageType a partire da una static mesh.

    Il FoliageType è la "specie": dice quale mesh piazzare e con quali regole
    (densità, scala casuale, allineamento alla normale, collisione). Le istanze
    si piazzano poi con `ue_foliage_add_instances` o `ue_foliage_scatter`.

    Args:
        package_path: es. "/Game/MyGame/Foliage".
        name: es. "FT_Erba".
        mesh_path: la static mesh, es. "/Game/Meshes/SM_Erba".
        properties: proprietà iniziali, es. {"density": 300, "random_yaw": True}.
    """
    return await run(
        "result = mcp_create_foliage_type("
        f"{lit(package_path)}, {lit(name)}, {lit(mesh_path)}, {lit(properties)})"
    )


@mcp.tool()
async def ue_set_foliage_property(
    foliage_type_path: str, property_name: str, value: object
) -> dict:
    """Scrive una proprietà su un FoliageType e rilegge il valore risultante.

    Args:
        foliage_type_path: path dell'asset FoliageType.
        property_name: es. "density", "radius", "random_yaw", "align_to_normal",
            "scale_x", "collision_with_world", "cull_distance".
        value: valore JSON; i path "/Game/..." vengono caricati come asset.
    """
    return await run(
        "result = mcp_set_foliage_property("
        f"{lit(foliage_type_path)}, {lit(property_name)}, {lit(value)})"
    )


@mcp.tool()
async def ue_foliage_add_instances(foliage_type_path: str, transforms: list) -> dict:
    """Piazza istanze di foliage alle trasformate date, nel livello corrente.

    Args:
        foliage_type_path: path dell'asset FoliageType.
        transforms: lista di {"location": {...}, "rotation": {...}, "scale": {...}}
            — oppure di sole posizioni ([x,y,z] o {"x":..}) quando rotazione e
            scala non interessano. Le posizioni sono in cm: se il livello è
            costruito lontano dall'origine, leggi prima un attore di riferimento
            con `ue_list_actors`.
    """
    return await run(
        f"result = mcp_foliage_add_instances({lit(foliage_type_path)}, {lit(transforms)})"
    )


@mcp.tool()
async def ue_foliage_scatter(
    foliage_type_path: str,
    center: dict | list,
    radius: float,
    count: int,
    seed: int | None = None,
    align_to_ground: bool = True,
    z_offset: float = 0.0,
) -> dict:
    """Sparge N istanze a caso in un cerchio, appoggiandole al terreno.

    È il tool da usare per riempire una zona: `ue_foliage_add_instances` vuole
    ogni trasformata scritta a mano.

    Args:
        foliage_type_path: path dell'asset FoliageType.
        center: centro del cerchio in cm.
        radius: raggio in cm.
        count: quante istanze.
        seed: seme del generatore — passalo per ottenere due volte lo stesso
            risultato.
        align_to_ground: appoggia ogni istanza a terra con un line trace
            dall'alto. Senza, restano tutte alla quota del centro.
        z_offset: alza (o abbassa) di tanto ogni istanza dopo l'appoggio.
    """
    return await run(
        "result = mcp_foliage_scatter("
        f"{lit(foliage_type_path)}, {lit(center)}, {float(radius)}, {int(count)}, "
        f"{lit(seed)}, {bool(align_to_ground)}, {float(z_offset)})"
    )


@mcp.tool()
async def ue_foliage_list() -> dict:
    """Il foliage piazzato nel livello: mesh, componente e numero di istanze.

    Elenca quello che c'è davvero nel livello, non i FoliageType come asset
    (per quelli usa `ue_list_assets`)."""
    return await run("result = mcp_foliage_list()")


@mcp.tool()
async def ue_foliage_query(
    foliage_type_path: str, center: dict | list, radius: float, limit: int = 100
) -> dict:
    """Le istanze di un FoliageType dentro una sfera, con le loro trasformate.

    Args:
        foliage_type_path: path dell'asset FoliageType.
        center: centro della sfera in cm.
        radius: raggio in cm.
        limit: quante trasformate restituire al massimo (il conteggio totale è
            sempre esatto).
    """
    return await run(
        "result = mcp_foliage_query("
        f"{lit(foliage_type_path)}, {lit(center)}, {float(radius)}, {int(limit)})"
    )


@mcp.tool()
async def ue_foliage_remove(
    foliage_type_path: str,
    center: dict | list | None = None,
    radius: float | None = None,
) -> dict:
    """Toglie istanze di foliage: tutte, o solo quelle dentro una sfera.

    Senza `center` e `radius` cancella tutte le istanze di quel FoliageType nel
    livello."""
    return await run(
        "result = mcp_foliage_remove("
        f"{lit(foliage_type_path)}, {lit(center)}, {lit(radius)})"
    )


@mcp.tool()
async def ue_create_foliage_spawner(
    package_path: str,
    name: str,
    foliage_types: list[str] | None = None,
    tile_size: float | None = None,
) -> dict:
    """Crea un ProceduralFoliageSpawner con dentro i suoi FoliageType.

    Lo spawner è la ricetta (quali specie, con quale competizione fra loro); il
    volume creato da `ue_foliage_spawn_volume` è dove viene applicata.

    Args:
        package_path: es. "/Game/MyGame/Foliage".
        name: es. "PFS_Bosco".
        foliage_types: path dei FoliageType da includere.
        tile_size: lato della tile di simulazione in cm (default 10000).
    """
    return await run(
        "result = mcp_create_foliage_spawner("
        f"{lit(package_path)}, {lit(name)}, {lit(foliage_types)}, {lit(tile_size)})"
    )


@mcp.tool()
async def ue_foliage_spawn_volume(
    spawner_path: str,
    label: str | None = None,
    location: dict | list | None = None,
    size: dict | list | None = None,
) -> dict:
    """Piazza un ProceduralFoliageVolume con lo spawner già collegato.

    Args:
        spawner_path: path del ProceduralFoliageSpawner.
        label: nome dell'attore nel livello.
        location: centro del volume in cm (occhio all'origine del mondo).
        size: dimensioni in cm. Default 200×200×200.
    """
    return await run(
        "result = mcp_foliage_spawn_volume("
        f"{lit(spawner_path)}, {lit(label)}, {lit(location)}, {lit(size)})"
    )


@mcp.tool()
async def ue_foliage_simulate(label: str, clear: bool = False) -> dict:
    """Fa simulare il foliage procedurale di un volume — o lo azzera con
    `clear=True`. Da chiamare dopo aver cambiato lo spawner."""
    return await run(f"result = mcp_foliage_simulate({lit(label)}, {bool(clear)})")


# ===================================================================== SEQUENCER
#
# Fino alla 0.9.0 il sequencer c'era solo in uscita: `ue_render_sequence`
# renderizza una sequenza già fatta. Questi tool la costruiscono.
#
# Due cose da sapere prima di usarli, entrambe trovate dal vivo su UE 5.8:
# i nomi dei canali hanno un suffisso numerico instabile (si indica
# "Location.Z", non "Location.Z_3"), e i nomi visualizzati di track e binding
# sono localizzati — per questo le track si indirizzano per tipo o per indice.


@mcp.tool()
async def ue_create_level_sequence(
    package_path: str, name: str, fps: int | None = None, length_frames: int | None = None
) -> dict:
    """Crea una Level Sequence vuota.

    Args:
        package_path: es. "/Game/MyGame/Cinematics".
        name: es. "LS_Intro".
        fps: frame rate di visualizzazione (default 30).
        length_frames: durata in frame; imposta il range di playback da 0.
    """
    return await run(
        "result = mcp_create_level_sequence("
        f"{lit(package_path)}, {lit(name)}, {lit(fps)}, {lit(length_frames)})"
    )


@mcp.tool()
async def ue_sequence_info(sequence_path: str) -> dict:
    """Binding, track, sezioni e canali di una Level Sequence.

    Chiamalo prima di mettere chiavi: è il modo di sapere come si chiamano i
    canali e quali indici usare per track e sezioni."""
    return await run(f"result = mcp_sequence_info({lit(sequence_path)})")


@mcp.tool()
async def ue_sequence_add_actor(
    sequence_path: str, label: str, spawnable: bool = False
) -> dict:
    """Aggiunge un attore del livello alla sequenza.

    Args:
        sequence_path: path della Level Sequence.
        label: label dell'attore nel livello.
        spawnable: se True la sequenza si porta dietro una copia dell'attore e
            la crea e distrugge da sé (cinematica autonoma); se False anima
            l'attore che è già nel livello.
    """
    return await run(
        f"result = mcp_sequence_add_actor({lit(sequence_path)}, {lit(label)}, {bool(spawnable)})"
    )


@mcp.tool()
async def ue_sequence_add_track(
    sequence_path: str,
    binding: str,
    track_type: str,
    start: int | None = None,
    end: int | None = None,
) -> dict:
    """Aggiunge una track a un binding, con la sua prima sezione già dentro.

    Args:
        sequence_path: path della Level Sequence.
        binding: nome del binding (di norma la label dell'attore) o il suo indice.
        track_type: alias comodo — "transform", "visibility", "audio",
            "animation", "camera_cut", "event", "fade" — oppure il nome esatto
            della classe (es. "MovieSceneFloatTrack").
        start: primo frame della sezione (default: inizio del playback).
        end: ultimo frame della sezione (default: fine del playback).
    """
    return await run(
        "result = mcp_sequence_add_track("
        f"{lit(sequence_path)}, {lit(binding)}, {lit(track_type)}, {lit(start)}, {lit(end)})"
    )


@mcp.tool()
async def ue_sequence_add_key(
    sequence_path: str,
    binding: str,
    channel: str,
    frame: int,
    value: object,
    track: int | None = None,
    track_type: str | None = None,
    section: int = 0,
    interpolation: str | None = None,
) -> dict:
    """Mette una chiave su un canale e rilegge tutte le chiavi del canale.

    Args:
        sequence_path: path della Level Sequence.
        binding: nome o indice del binding.
        channel: nome del canale **senza suffisso numerico** — "Location.Z",
            "Rotation.Y", "Scale.X". Il suffisso che Unreal appiccica
            ("Location.Z_3") cambia da una creazione all'altra: elencali con
            `ue_sequence_info`.
        frame: numero di frame.
        value: valore (float, int o bool a seconda del canale).
        track: indice della track nel binding. Se il binding ne ha una sola,
            si può omettere.
        track_type: in alternativa a `track`, il tipo di track da cercare.
        section: indice della sezione nella track (default 0).
        interpolation: AUTO, USER, BREAK, LINEAR, CONSTANT.
    """
    return await run(
        "result = mcp_sequence_add_key("
        f"{lit(sequence_path)}, {lit(binding)}, {lit(channel)}, {int(frame)}, {lit(value)}, "
        f"{lit(track)}, {lit(track_type)}, {int(section)}, {lit(interpolation)})"
    )


@mcp.tool()
async def ue_sequence_set_range(
    sequence_path: str,
    start: int | None = None,
    end: int | None = None,
    fps: int | None = None,
) -> dict:
    """Cambia il range di playback e/o il frame rate della sequenza."""
    return await run(
        "result = mcp_sequence_set_range("
        f"{lit(sequence_path)}, {lit(start)}, {lit(end)}, {lit(fps)})"
    )


@mcp.tool()
async def ue_sequence_remove(
    sequence_path: str,
    binding: str,
    track: int | None = None,
    track_type: str | None = None,
) -> dict:
    """Toglie una track da un binding — o l'intero binding, se non indichi
    né `track` né `track_type`."""
    return await run(
        "result = mcp_sequence_remove("
        f"{lit(sequence_path)}, {lit(binding)}, {lit(track)}, {lit(track_type)})"
    )


@mcp.tool()
async def ue_sequence_open(sequence_path: str, close: bool = False) -> dict:
    """Apre la sequenza nell'editor del Sequencer (o la chiude con `close=True`).

    I tool scrivono sull'asset e basta: questo è il modo di vedere il risultato
    senza cercare l'asset a mano nel Content Browser."""
    return await run(f"result = mcp_sequence_open({lit(sequence_path)}, {bool(close)})")


# ========================================================================== FLOW
#
# Un flow è una lista di chiamate a tool descritta in YAML (o JSON) ed eseguita
# in una sola chiamata MCP. Vive interamente lato server: non tocca l'editor se
# non attraverso i tool che invoca, e `ue_flow_run(dry_run=True)` non lo tocca
# affatto.
#
# Il motivo è il contesto, non la velocità: una scena si costruisce quasi
# sempre con la stessa sequenza di dieci o venti chiamate, e farla passare dal
# modello un passo alla volta gli lascia in memoria diciannove risposte JSON
# che non gli servono più.


def _tool_di_flow(nome: str):
    """Risolve un nome di tool alla funzione del modulo, con un errore utile.

    I tool sono funzioni a livello di modulo con lo stesso nome del tool: la
    risoluzione passa da lì e non dal registry di FastMCP, che cambia forma fra
    le versioni. Il filtro esclude tutto ciò che non è un tool per evitare che
    un flow chiami `run`, `lit` o qualunque altro interno.
    """
    candidato = globals().get(nome)
    disponibili = {
        chiave
        for chiave, valore in globals().items()
        if chiave.startswith(("ue_", "preset_")) and callable(valore)
    }
    if nome not in disponibili or candidato is None:
        vicini = sorted(t for t in disponibili if nome.split("_")[-1] in t)[:5]
        raise RuntimeError(
            f"Il flow chiama un tool che non esiste: '{nome}'."
            + (f" Forse intendevi: {', '.join(vicini)}." if vicini else "")
        )
    if nome == "ue_flow_run":
        raise RuntimeError("Un flow non può chiamare se stesso.")
    return candidato


@mcp.tool()
async def ue_flow_run(
    flow: str,
    variables: dict | None = None,
    dry_run: bool = False,
    stop_on_error: bool = True,
) -> dict:
    """Esegue una sequenza di chiamate a tool descritta in YAML (o JSON).

    Serve quando la stessa scena si costruisce con dieci o venti chiamate
    sempre uguali: il flow le descrive una volta e le esegue in un colpo solo,
    restituendo il riassunto invece di venti risposte intere.

    Forma di un flow::

        variables:
          base: {x: 0, y: 0, z: 100}
        steps:
          - tool: ue_spawn_actor
            args: {class_name: StaticMeshActor, location: "${base}", label: Cubo}
            save: cubo
          - tool: ue_set_actor_transform
            args: {label: "${cubo.label}", scale: [2, 2, 2]}
          - tool: ue_screenshot
            when: {exists: cubo.label}

    Ogni passo accetta `tool`, `args`, `save` (nome della variabile in cui
    mettere il risultato), `when` (booleano, `${riferimento}`, o
    `{equals: [a,b]}` / `{not_equals: [a,b]}` / `{exists: percorso}`),
    `continue_on_error` e `name`.

    Nei valori, `${nome}` e `${nome.chiave.0}` riprendono quello che un passo
    precedente ha salvato. Una stringa fatta solo di riferimento conserva il
    tipo del valore (un dict resta un dict); dentro una frase viene interpolata
    come testo.

    Non ci sono cicli né espressioni, ed è voluto: la logica sta in chi scrive
    il flow, non nel flow.

    Args:
        flow: il testo YAML/JSON, oppure il path di un file .yaml/.json.
        variables: variabili iniziali, che si sommano a quelle del flow.
        dry_run: valida forma, nomi dei tool e riferimenti senza eseguire
            niente. Da usare sempre la prima volta che un flow gira.
        stop_on_error: fermarsi al primo passo fallito. Con False prosegue e
            riporta gli errori nel riassunto (un singolo passo può comunque
            dichiarare `continue_on_error: true`).
    """
    try:
        definizione = flow_engine.carica_flow(flow)
        passi = flow_engine.normalizza_passi(definizione)
    except flow_engine.FlowError as exc:
        raise RuntimeError(str(exc)) from exc

    contesto: dict[str, Any] = dict(definizione.get("variables") or {})
    contesto.update(variables or {})

    esiti: list[dict] = []
    falliti = 0

    for indice, passo in enumerate(passi):
        voce: dict[str, Any] = {"step": indice, "name": passo["name"], "tool": passo["tool"]}
        try:
            funzione = _tool_di_flow(passo["tool"])

            if not flow_engine.condizione_vera(passo["when"], contesto):
                voce["status"] = "skipped"
                esiti.append(voce)
                continue

            argomenti = flow_engine.espandi(passo["args"], contesto)

            if dry_run:
                voce["status"] = "ok (dry run)"
                voce["args"] = argomenti
                esiti.append(voce)
                # In dry run non c'è un risultato vero da salvare: si mette un
                # segnaposto, altrimenti ogni ${riferimento} dei passi
                # successivi fallirebbe e il dry run direbbe che il flow è
                # rotto quando non lo è.
                if passo["save"]:
                    contesto[str(passo["save"])] = flow_engine.SegnapostoDryRun()
                continue

            risultato = await funzione(**argomenti)
            if passo["save"]:
                contesto[str(passo["save"])] = risultato
            voce["status"] = "ok"
            voce["result"] = flow_engine.riepiloga(risultato)

        except Exception as exc:  # noqa: BLE001 - qualunque errore di un passo è dato del flow
            falliti += 1
            voce["status"] = "error"
            voce["error"] = str(exc)[:600]
            esiti.append(voce)
            if stop_on_error and not passo["continue_on_error"]:
                return {
                    "dry_run": bool(dry_run),
                    "steps": len(passi),
                    "executed": len(esiti),
                    "failed": falliti,
                    "stopped_at": indice,
                    "results": esiti,
                    "variables": sorted(contesto),
                }
            continue

        esiti.append(voce)

    return {
        "dry_run": bool(dry_run),
        "steps": len(passi),
        "executed": len(esiti),
        "failed": falliti,
        "results": esiti,
        "variables": sorted(contesto),
    }


# ================================================================== resources
#
# Le resource costano meno di una tool call: il client può tenerle aggiornate
# da sé e allegarle al contesto, senza che il modello spenda un turno per
# chiedere "com'è messo l'editor adesso".


def _json(valore: Any) -> str:
    return json.dumps(valore, indent=2, ensure_ascii=False, default=str)


async def _sicuro(coroutine) -> str:
    """Esegue una lettura, restituendo l'errore come contenuto della resource.

    Una resource che solleva sparisce dal client; una che risponde "editor
    chiuso" resta leggibile e dice al modello cosa fare.
    """
    try:
        return _json(await coroutine)
    except Exception as exc:  # noqa: BLE001
        return _json({"available": False, "reason": str(exc)})


@mcp.resource(
    "unreal://status",
    name="Stato dell'editor Unreal",
    description="Versione motore, progetto, livello corrente, numero di attori e capacità rilevate.",
    mime_type="application/json",
)
async def resource_status() -> str:
    return await _sicuro(run("result = mcp_project_status()"))


@mcp.resource(
    "unreal://log",
    name="Log di Unreal",
    description="Ultime 200 righe del log dell'editor.",
    mime_type="application/json",
)
async def resource_log() -> str:
    return await _sicuro(run("result = mcp_tail_log(200, False)"))


@mcp.resource(
    "unreal://actors",
    name="Attori del livello",
    description="Gli attori presenti nel livello attualmente aperto.",
    mime_type="application/json",
)
async def resource_actors() -> str:
    return await _sicuro(run("result = mcp_find_actors(None, None)"))


# Un template con parametro non funzionerebbe: FastMCP 1.x compila i segmenti
# come `[^/]+`, quindi "unreal://assets/Game/MyGame" non farebbe match. Per i
# sottopercorsi c'è ue_list_assets.
@mcp.resource(
    "unreal://assets",
    name="Content Browser",
    description="Tutti gli asset sotto /Game del progetto aperto.",
    mime_type="application/json",
)
async def resource_assets() -> str:
    return await _sicuro(run("result = mcp_list_assets('/Game', True, None)"))


@mcp.resource(
    "unreal://engines",
    name="Motori installati",
    description="Le installazioni di Unreal Engine trovate su questa macchina. Non richiede l'editor aperto.",
    mime_type="application/json",
)
async def resource_engines() -> str:
    try:
        return _json(local.find_engines())
    except Exception as exc:  # noqa: BLE001
        return _json({"available": False, "reason": str(exc)})


# ============================================================= local extensions

try:  # pragma: no cover - solo se presente
    from . import local_tools  # noqa: F401  (tool extra non versionati)
except ImportError:
    pass


def main() -> None:
    """Entry point. Stdio di default, HTTP con --http o UE_MCP_HTTP=1.

    Lo stdio copre tutti i client che lanciano il server in locale, pannello
    viewport compreso: le MCP App sono indipendenti dal trasporto. L'HTTP serve
    solo a claude.ai sul web, che girando sui server di Anthropic non può
    raggiungere questa macchina se non attraverso un URL pubblico.

    Attenzione a cosa si espone: questo server offre `ue_exec_python`, cioè
    esecuzione di codice arbitrario dentro l'editor. Il bind resta su 127.0.0.1
    apposta — un tunnel lo pubblica comunque, quindi tienilo su per il tempo
    della prova e non condividere l'URL.
    """
    # Nomi distinti da UE_MCP_TRANSPORT e UE_MCP_PORT: quelli descrivono già
    # il canale verso l'editor e la porta del Remote Control, e riusarli qui
    # significherebbe che chi imposta la porta del bridge sposta per sbaglio
    # quella su cui ascolta il server.
    scelto = (
        "streamable-http"
        if "--http" in sys.argv or os.environ.get("UE_MCP_HTTP") == "1"
        else "stdio"
    )
    if scelto == "streamable-http":
        mcp.settings.port = int(os.environ.get("UE_MCP_HTTP_PORT", mcp.settings.port))

        # L'SDK rifiuta con "Invalid Host header" ogni richiesta il cui Host
        # non sia in lista: è protezione anti DNS-rebinding, pensata per un
        # server in ascolto su localhost che un browser potrebbe attaccare.
        # Dietro un tunnel l'Host è il dominio pubblico, che non si conosce
        # prima di averlo aperto — quindi qui si accetta tutto. Non è un buco
        # in più: chi arriva fin qui ha già l'URL del tunnel, e il tunnel è la
        # cosa che va tenuta privata.
        ammessi = os.environ.get("UE_MCP_ALLOWED_HOSTS", "*")
        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=ammessi != "*",
            allowed_hosts=[] if ammessi == "*" else ammessi.split(","),
            allowed_origins=[] if ammessi == "*" else ammessi.split(","),
        )

    try:
        mcp.run(transport=scelto)
    finally:
        # Il client httpx tiene aperta una connessione keep-alive verso
        # l'editor: senza questo resta appesa alla chiusura del server.
        with contextlib.suppress(Exception):
            asyncio.run(_bridge.aclose())


if __name__ == "__main__":
    main()

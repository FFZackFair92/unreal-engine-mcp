"""Server MCP per Unreal Engine 5.

Ogni tool traduce la richiesta in uno snippet Python eseguito dentro l'editor
tramite la Remote Control API. Vedi README.md per l'attivazione dei plugin.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import Context, FastMCP, Image

from . import assets, local
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
        "compile C++, package the game, download free assets "
        "(ue_engine_*, ue_project_*, ue_editor_*, ue_build_*, ue_package_*, preset_*); "
        "(2) EDITOR — drive the running editor over the Remote Control API (every other tool). "
        "Cold start: ue_engine_list -> ue_project_create -> ue_editor_open -> ue_status. "
        "On a running editor, always call ue_status first. "
        "Asset paths follow the Unreal convention (/Game/...); positions are in centimetres "
        "(1 unit = 1 cm) with Z up. Compiling C++ needs the editor closed (ue_build_start), "
        "unless the change only touches function bodies (ue_live_compile). "
        "Blueprint node graphs cannot be scripted: put logic in a C++ parent class "
        "(ue_cpp_class_create -> build -> ue_reparent_blueprint). Material graphs, on the "
        "other hand, are fully scriptable (ue_create_material). "
        "Do not assume the action is at the world origin: real levels are often built "
        "thousands of units away from [0,0,0], so an actor spawned there can be "
        "off-screen and invisible. Anchor new actors to what is already in the scene — "
        "read a reference actor with ue_list_actors, or the camera with ue_get_camera, "
        "and place relative to that. "
        "Verify visually: ue_focus_actor on what you touched, then ue_screenshot, which "
        "returns the viewport as an image — look at it before reporting success. "
        "Use ue_spawn_many when placing more than a few actors."
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


def local_call(func, *args, **kwargs):
    """Esegue un'operazione locale traducendo LocalError/AssetError in errori MCP."""
    try:
        return func(*args, **kwargs)
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
    stato = leggi_stato()
    await _progress(ctx, 0, wait_seconds, "%s: avviato" % etichetta)

    while stato.get("running") and asyncio.get_event_loop().time() < scadenza:
        await asyncio.sleep(min(5.0, max(1.0, wait_seconds / 60)))
        stato = leggi_stato()
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
    engines = local_call(local.find_engines)
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
    engine = local_call(local.resolve_engine, engine_version, engine_root)
    return {"engine": engine.version, "templates": local_call(local.list_templates, engine)}


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
    return local_call(
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
    projects = local_call(local.find_projects, directory, max_depth)
    return {"count": len(projects), "projects": projects}


@mcp.tool()
async def ue_project_info(uproject: str) -> dict:
    """Legge un .uproject: versione motore associata, plugin attivi e se il bridge è pronto."""
    return local_call(local.project_info, uproject)


@mcp.tool()
async def ue_project_set_plugins(
    uproject: str, enable: list[str], disable: list[str] | None = None
) -> dict:
    """Abilita/disabilita plugin scrivendo nel .uproject (utile su progetti esistenti).

    Per usare il bridge servono almeno `PythonScriptPlugin` e `RemoteControl`.
    L'editor va riavviato se era già aperto.
    """
    return local_call(local.set_project_plugins, uproject, enable, disable)


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
    launched = local_call(
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
    return local_call(
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
    return local_call(
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
    def leggi() -> dict:
        return local_call(local.build_status, tail_lines, uproject)

    if wait_seconds > 0:
        return await _attendi_job(leggi, wait_seconds, ctx, "compilazione")
    return leggi()


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
    return local_call(
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
    def leggi() -> dict:
        return local_call(local.package_status, tail_lines, uproject)

    if wait_seconds > 0:
        return await _attendi_job(leggi, wait_seconds, ctx, "packaging")
    return leggi()


@mcp.tool()
async def ue_editor_status() -> dict:
    """Stato del processo editor avviato da questo MCP e del bridge Remote Control."""
    process = local_call(local.editor_status)
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
            return {"closed": True, "mode": "clean", "saved": save_all, **local_call(local.editor_status)}
        except (UnrealBridgeError, RuntimeError) as exc:
            fallback_reason = str(exc)
    else:
        fallback_reason = "force=True"

    killed = local_call(local.kill_editor)
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
    files = local_call(assets.list_library, subfolder, extensions)
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
        result["extracted"] = local_call(assets.extract_archive, downloaded["file"])
    return result


@mcp.tool()
async def preset_extract_archive(archive: str, destination: str | None = None) -> dict:
    """Estrae un archivio zip/tar già presente sul disco (i .rar non sono supportati)."""
    return local_call(assets.extract_archive, archive, destination)


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
async def preset_fab_list_vault() -> dict:
    """Elenca il contenuto Unreal acquistato sull'account Epic (Fab/Marketplace).

    Non esiste un'API pubblica: serve il client community `legendary`
    (`pip install legendary-gl` + `legendary auth`). Senza di esso il tool
    spiega come procedere dall'Epic Games Launcher.
    """
    return local_call(assets.fab_list_vault)


@mcp.tool()
async def preset_fab_download(app_name: str, destination: str | None = None) -> dict:
    """Scarica un asset del vault Epic tramite `legendary`.

    Args:
        app_name: identificativo restituito da preset_fab_list_vault.
    """
    return local_call(assets.fab_download, app_name, destination)


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

    percorso = (esito or {}).get("file") if isinstance(esito, dict) else None
    if not return_image or not percorso:
        return esito

    file = Path(percorso)
    # L'editor potrebbe essere su un'altra macchina (UE_MCP_HOST): in quel caso
    # il path esiste per lui e non per noi, e c'è solo da dirlo.
    if not file.is_file():
        esito["image"] = None
        esito["image_note"] = (
            "PNG scritto dall'editor ma non leggibile da qui: probabilmente "
            "l'editor è su un'altra macchina. Il percorso resta valido per lui."
        )
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
async def ue_start_pie() -> dict:
    """Avvia il Play In Editor con le impostazioni correnti."""
    return await run("result = mcp_start_pie()")


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
    """Entry point stdio."""
    try:
        mcp.run()
    finally:
        # Il client httpx tiene aperta una connessione keep-alive verso
        # l'editor: senza questo resta appesa alla chiusura del server.
        with contextlib.suppress(Exception):
            asyncio.run(_bridge.aclose())


if __name__ == "__main__":
    main()

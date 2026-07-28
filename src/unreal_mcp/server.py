"""Server MCP per Unreal Engine 5.

Ogni tool traduce la richiesta in uno snippet Python eseguito dentro l'editor
tramite la Remote Control API. Vedi README.md per l'attivazione dei plugin.
"""

from __future__ import annotations

import asyncio
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import assets, local
from .bridge import (
    BridgeConfig,
    UnrealBridge,
    UnrealBridgeError,
    UnrealNotConnected,
    UnrealPythonError,
)

mcp = FastMCP(
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
        "unless the change only touches function bodies (ue_live_compile)."
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
async def ue_engine_templates(engine_version: str | None = None) -> dict:
    """Elenca i template ufficiali disponibili nell'installazione (TP_Blank, TP_ThirdPerson, ...)."""
    engine = local_call(local.resolve_engine, engine_version)
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
    """
    return local_call(
        local.create_project,
        name=name,
        directory=directory,
        engine_version=engine_version,
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
    wait_seconds: int = 240,
    extra_args: list[str] | None = None,
) -> dict:
    """Apre un progetto nell'editor e attende che il bridge Remote Control risponda.

    Args:
        uproject: percorso del file .uproject.
        engine_version: forza una versione del motore diversa da quella associata.
        wait_seconds: quanto attendere l'apertura (il primo avvio compila gli shader
            e può richiedere diversi minuti; con 0 non attende).
        extra_args: argomenti aggiuntivi per la riga di comando dell'editor.
    """
    launched = local_call(local.launch_editor, uproject, engine_version, extra_args)

    if wait_seconds <= 0:
        return {**launched, "bridge_ready": False, "nota": "avvio non atteso"}

    deadline = asyncio.get_event_loop().time() + wait_seconds
    while asyncio.get_event_loop().time() < deadline:
        try:
            await _bridge.info()
            status = await run("result = mcp_project_status()")
            return {**launched, "bridge_ready": True, "status": status}
        except (UnrealNotConnected, UnrealBridgeError, RuntimeError):
            await asyncio.sleep(3)

    return {
        **launched,
        "bridge_ready": False,
        "nota": (
            "Editor avviato ma il bridge non ha risposto entro %d s. Al primo avvio la "
            "compilazione degli shader è lunga: riprova con ue_status fra qualche minuto."
            % wait_seconds
        ),
    }


@mcp.tool()
async def ue_build_start(
    uproject: str,
    engine_version: str | None = None,
    target: str | None = None,
    configuration: str = "Development",
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
    """
    return local_call(local.start_build, uproject, engine_version, target, "Win64", configuration)


@mcp.tool()
async def ue_build_status(tail_lines: int = 30) -> dict:
    """Stato della compilazione avviata con ue_build_start: in corso, errori, coda del log."""
    return local_call(local.build_status, tail_lines)


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
    """
    return local_call(
        local.start_package,
        uproject,
        engine_version,
        configuration,
        "Win64",
        maps,
        output_dir,
        dedicated_server,
    )


@mcp.tool()
async def ue_package_status(tail_lines: int = 30) -> dict:
    """Stato del packaging avviato con ue_package_start.

    Riporta la fase corrente (Cook, Stage, Package, Archive), gli errori e,
    a fine corsa, il percorso dell'eseguibile prodotto.
    """
    return local_call(local.package_status, tail_lines)


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
            return {"closed": True, "mode": "pulita", "saved": save_all, **local_call(local.editor_status)}
        except (UnrealBridgeError, RuntimeError) as exc:
            fallback_reason = str(exc)
    else:
        fallback_reason = "force=True"

    killed = local_call(local.kill_editor)
    return {"closed": killed.get("killed", False), "mode": "processo terminato", "motivo": fallback_reason, **killed}


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
    progetto aperto, livello corrente e numero di attori. Da usare per prima."""
    return await run("result = mcp_project_status()")


@mcp.tool()
async def ue_read_log(lines: int = 80, only_errors: bool = False) -> dict:
    """Legge la coda del log di Unreal (equivalente di read_console in Unity).

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
        files: percorsi assoluti sul disco (es. ["C:/tmp/four_corners_export/Arena_Tetto.glb"]).
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
async def ue_set_actor_transform(
    label: str,
    location: list[float] | None = None,
    rotation: list[float] | None = None,
    scale: list[float] | None = None,
) -> dict:
    """Modifica posizione/rotazione/scala di un attore identificato dalla sua label."""
    return await run(
        f"""
actor = mcp_actor_by_label({lit(label)})
if actor is None:
    raise ValueError("Nessun attore con label {label!r} nel livello corrente.")
if {lit(location)} is not None:
    actor.set_actor_location(mcp_to_vector({lit(location)}), False, False)
if {lit(rotation)} is not None:
    actor.set_actor_rotation(mcp_to_rotator({lit(rotation)}), False)
if {lit(scale)} is not None:
    actor.set_actor_scale3d(mcp_to_vector({lit(scale)}, (1.0, 1.0, 1.0)))
result = mcp_actor_info(actor)
"""
    )


@mcp.tool()
async def ue_delete_actor(label: str) -> dict:
    """Elimina dal livello corrente l'attore con la label indicata."""
    return await run(
        f"""
actor = mcp_actor_by_label({lit(label)})
if actor is None:
    raise ValueError("Nessun attore con label {label!r}.")
mcp_actor_subsystem().destroy_actor(actor)
result = {{"deleted": {lit(label)}}}
"""
    )


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


# ================================================================== networking


@mcp.tool()
async def ue_set_replication(
    blueprint_path: str,
    replicates: bool = True,
    replicate_movement: bool = True,
    always_relevant: bool = False,
) -> dict:
    """Configura la replication di un Blueprint (equivalente di NetworkObject in Unity)."""
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


# ============================================================= local extensions

try:  # pragma: no cover - solo se presente
    from . import local_tools  # noqa: F401  (tool extra non versionati)
except ImportError:
    pass


def main() -> None:
    """Entry point stdio."""
    mcp.run()


if __name__ == "__main__":
    main()

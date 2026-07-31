"""Helper eseguiti DENTRO l'editor Unreal.

Questo file non viene mai importato dal server MCP: viene letto come testo e
anteposto ad ogni snippet inviato a ``ExecutePythonCommandEx``. Per questo può
usare liberamente il modulo ``unreal``, disponibile solo nell'editor.
"""

import json
import os
import re

import unreal

# ---------------------------------------------------------------- subsystems


def mcp_actor_subsystem():
    return unreal.get_editor_subsystem(unreal.EditorActorSubsystem)


def mcp_level_subsystem():
    return unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)


def mcp_asset_tools():
    return unreal.AssetToolsHelpers.get_asset_tools()


def mcp_asset_lib():
    return unreal.EditorAssetLibrary


# -------------------------------------------------------------- transazioni


class _McpNullTransaction:
    """Fallback quando ScopedEditorTransaction non è disponibile."""

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def mcp_transaction(description):
    """Raggruppa le modifiche in una transazione annullabile con Ctrl+Z.

    Senza questo, ogni modifica fatta dall'agente è irreversibile per chi sta
    guardando l'editor: `ScopedEditorTransaction` la registra nell'undo stack
    come una singola voce con un nome leggibile.
    """
    if hasattr(unreal, "ScopedEditorTransaction"):
        return unreal.ScopedEditorTransaction(str(description))
    return _McpNullTransaction()


# --------------------------------------------------------- versione e capacità


def mcp_engine_minor():
    """Minor version del motore (es. 8 per 5.8), o None se non riconoscibile."""
    try:
        parts = str(unreal.SystemLibrary.get_engine_version()).split(".")
        if parts[0] == "5":
            return int(parts[1])
    except Exception:  # noqa: BLE001, S110 - versione non riconoscibile: si degrada a None
        pass
    return None


def mcp_capabilities():
    """Cosa sa fare questa versione del motore, rilevato a runtime.

    Meglio del confronto di versione: vale anche per build custom del motore.
    """
    library = unreal.BlueprintEditorLibrary
    return {
        # add_member_variable e i setter di replication esistono da UE 5.4.
        "blueprint_variables": hasattr(library, "add_member_variable"),
        "blueprint_variable_replication": hasattr(library, "set_blueprint_variable_replication"),
        # SubobjectDataSubsystem esiste da UE 5.0.
        "blueprint_components": hasattr(unreal, "SubobjectDataSubsystem"),
        # MetaSounds richiede il plugin abilitato.
        "metasounds": any(
            hasattr(unreal, c) for c in ("MetaSoundSourceFactory", "MetasoundSourceFactory")
        ),
    }


# ------------------------------------------------------------ serializzazione


def mcp_vec(v):
    if v is None:
        return None
    return {"x": float(v.x), "y": float(v.y), "z": float(v.z)}


def mcp_rot(r):
    if r is None:
        return None
    return {"pitch": float(r.pitch), "yaw": float(r.yaw), "roll": float(r.roll)}


def mcp_to_vector(value, default=(0.0, 0.0, 0.0)):
    if value is None:
        value = default
    if isinstance(value, dict):
        return unreal.Vector(
            float(value.get("x", 0.0)), float(value.get("y", 0.0)), float(value.get("z", 0.0))
        )
    return unreal.Vector(float(value[0]), float(value[1]), float(value[2]))


def mcp_to_rotator(value):
    if value is None:
        value = (0.0, 0.0, 0.0)
    if isinstance(value, dict):
        return unreal.Rotator(
            float(value.get("roll", 0.0)),
            float(value.get("pitch", 0.0)),
            float(value.get("yaw", 0.0)),
        )
    # convenzione lista: [pitch, yaw, roll]
    return unreal.Rotator(float(value[2]), float(value[0]), float(value[1]))


def mcp_actor_info(actor):
    if actor is None:
        return None
    try:
        label = actor.get_actor_label()
    except Exception:  # noqa: BLE001
        label = ""
    return {
        "label": label,
        "name": str(actor.get_name()),
        "class": str(actor.get_class().get_name()),
        "path": str(actor.get_path_name()),
        "location": mcp_vec(actor.get_actor_location()),
        "rotation": mcp_rot(actor.get_actor_rotation()),
    }


# --------------------------------------------------------------- risoluzione


def mcp_resolve_class(ref):
    """Accetta 'StaticMeshActor', '/Script/Engine.StaticMeshActor',
    '/Game/Path/BP_Foo' o '/Game/Path/BP_Foo.BP_Foo_C'."""
    if ref is None or ref == "":
        raise ValueError("Nome classe vuoto.")
    if isinstance(ref, type) or hasattr(ref, "static_class"):
        return ref

    if not ref.startswith("/"):
        if hasattr(unreal, ref):
            return getattr(unreal, ref)
        for module_path in ("/Script/Engine.", "/Script/CoreUObject.", "/Script/AIModule."):
            found = unreal.load_class(None, module_path + ref)
            if found:
                return found
        raise ValueError(
            "Classe '%s' non trovata. Usa il nome esatto (es. 'StaticMeshActor') "
            "o un path completo (es. '/Game/MyGame/BP_Player')." % ref
        )

    asset_path = ref[:-2] if ref.endswith("_C") else ref
    asset = mcp_asset_lib().load_asset(asset_path) if mcp_asset_lib().does_asset_exist(asset_path) else None
    if asset is not None and isinstance(asset, unreal.Blueprint):
        return asset.generated_class()
    found = unreal.load_class(None, ref)
    if found:
        return found
    raise ValueError("Impossibile risolvere la classe '%s'." % ref)


def mcp_resolve_struct(name):
    table = {
        "vector": unreal.Vector.static_struct(),
        "rotator": unreal.Rotator.static_struct(),
        "transform": unreal.Transform.static_struct(),
        "linearcolor": unreal.LinearColor.static_struct(),
    }
    key = str(name).lower()
    if key in table:
        return table[key]
    if hasattr(unreal, name):
        return getattr(unreal, name).static_struct()
    raise ValueError("Struct '%s' non riconosciuta." % name)


# ---------------------------------------------------------------- asset/level


def mcp_import_assets(files, destination, replace_existing=True, import_as_skeletal=False):
    tasks = []
    for path in files:
        task = unreal.AssetImportTask()
        task.set_editor_property("filename", path)
        task.set_editor_property("destination_path", destination)
        task.set_editor_property("automated", True)
        task.set_editor_property("save", True)
        task.set_editor_property("replace_existing", bool(replace_existing))
        if import_as_skeletal and path.lower().endswith(".fbx"):
            options = unreal.FbxImportUI()
            options.set_editor_property("import_mesh", True)
            options.set_editor_property("import_as_skeletal", True)
            options.set_editor_property("import_animations", True)
            task.set_editor_property("options", options)
        tasks.append(task)

    mcp_asset_tools().import_asset_tasks(tasks)

    out = []
    for task, path in zip(tasks, files, strict=False):
        imported = [str(p) for p in (task.get_editor_property("imported_object_paths") or [])]
        entry = {"file": path, "imported": imported, "count": len(imported)}
        if not imported and path.lower().endswith((".glb", ".gltf")):
            entry["hint"] = (
                "Nessun asset importato. Su UE < 5.3 l'import glTF non passa da "
                "Interchange: abilita il plugin 'glTF Importer' (Edit > Plugins) "
                "e riprova, oppure converti il file in FBX."
            )
        out.append(entry)
    return out


def mcp_list_assets(path, recursive=True, class_filter=None):
    assets = mcp_asset_lib().list_assets(path, recursive=recursive, include_folder=False)
    out = []
    for asset_path in assets:
        data = mcp_asset_lib().find_asset_data(asset_path)
        class_name = str(data.asset_class_path.asset_name) if hasattr(data, "asset_class_path") else ""
        if class_filter and class_filter.lower() not in class_name.lower():
            continue
        out.append({"path": str(asset_path), "class": class_name})
    return out


# ------------------------------------------------------- comandi di console


def mcp_console_command(command, wait_seconds=1.0):
    """Esegue un comando della console dell'editor e restituisce ciò che stampa.

    Il valore non torna mai come risultato: i comandi di console scrivono nel
    log e basta. Qui si misura la lunghezza del log prima, si esegue, si aspetta
    un attimo che il motore abbia scritto, e si restituisce solo la coda nuova —
    altrimenti chi chiama riceve "fatto" e nessuna informazione.

    Passa da `unreal.SystemLibrary.execute_console_command`, cioè dall'interprete
    Python interno: **non** dal gate `bAllowConsoleCommandRemoteExecution` della
    Remote Control API, che resta spento. Vale la stessa avvertenza di
    mcp_exec_python: da qui si può fare tutto quello che si fa dalla console,
    `quit` compreso.
    """
    percorso = mcp_log_path()
    prima = 0
    if percorso and os.path.isfile(percorso):
        prima = os.path.getsize(percorso)

    mondo = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    unreal.SystemLibrary.execute_console_command(mondo, str(command))

    _mcp_time().sleep(max(0.0, float(wait_seconds)))

    nuove = []
    if percorso and os.path.isfile(percorso):
        with open(percorso, encoding="utf-8", errors="replace") as handle:
            handle.seek(prima)
            nuove = [riga.rstrip("\n") for riga in handle.readlines()]

    return {
        "command": str(command),
        "log_lines": nuove[-80:],
        "log_line_count": len(nuove),
        "note": None
        if nuove
        else "nessun output nel log: molti comandi non stampano nulla, e alcuni "
        "hanno effetto solo in Play In Editor",
    }


# ------------------------------------------------------- camera della viewport


def _mcp_forward(rotazione):
    """Vettore in avanti di un Rotator.

    `Rotator.get_forward_vector()` non esiste su tutte le versioni: dove manca,
    la stessa cosa la fa MathLibrary.
    """
    if hasattr(rotazione, "get_forward_vector"):
        return rotazione.get_forward_vector()
    return unreal.MathLibrary.get_forward_vector(rotazione)


def _mcp_viewport():
    """Sottosistema della viewport di livello, dove vive la camera dell'editor."""
    return unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)


def mcp_get_camera():
    """Posizione e orientamento della camera della viewport.

    Serve più di quanto sembri: i livelli veri sono spesso costruiti a migliaia
    di unità dall'origine, quindi sapere dov'è la camera è il modo di piazzare
    cose dove qualcuno le guarderà.
    """
    posizione, rotazione = _mcp_viewport().get_level_viewport_camera_info()
    return {"location": mcp_vec(posizione), "rotation": mcp_rot(rotazione)}


def mcp_set_camera(location=None, rotation=None):
    """Sposta la camera della viewport."""
    attuale_pos, attuale_rot = _mcp_viewport().get_level_viewport_camera_info()
    posizione = mcp_to_vector(location) if location is not None else attuale_pos
    rotazione = mcp_to_rotator(rotation) if rotation is not None else attuale_rot
    _mcp_viewport().set_level_viewport_camera_info(posizione, rotazione)
    return {"location": mcp_vec(posizione), "rotation": mcp_rot(rotazione)}


def mcp_focus_actor(label=None, distance=None):
    """Inquadra un attore (o tutta la selezione) come farebbe il tasto F.

    È il complemento di mcp_screenshot: senza, l'agente fotografa qualunque
    cosa la camera stesse guardando, che di solito non è quello che ha appena
    costruito.
    """
    subsystem = mcp_actor_subsystem()
    if label is not None:
        attore = mcp_require_actor(label)
        subsystem.set_selected_level_actors([attore])
        bersagli = [attore]
    else:
        bersagli = list(subsystem.get_selected_level_actors())
        if not bersagli:
            raise ValueError(
                "Nessun attore selezionato e nessuna label indicata: non so cosa inquadrare."
            )

    # Si arretra dal bersaglio lungo la direzione di vista corrente: mantenere
    # l'angolo invece di ricalcolarlo dà un'inquadratura prevedibile, e
    # l'agente può orientarla prima con mcp_set_camera.
    posizione = bersagli[0].get_actor_location()
    _, rotazione = _mcp_viewport().get_level_viewport_camera_info()
    if distance is None:
        distance = 500.0
    direzione = _mcp_forward(rotazione)
    camera = unreal.Vector(
        posizione.x - direzione.x * float(distance),
        posizione.y - direzione.y * float(distance),
        posizione.z - direzione.z * float(distance),
    )
    _mcp_viewport().set_level_viewport_camera_info(camera, rotazione)
    return {
        "focused": [a.get_actor_label() for a in bersagli],
        "camera": {"location": mcp_vec(camera), "rotation": mcp_rot(rotazione)},
        "distance": float(distance),
    }


# ------------------------------------------------- gestione asset (CRUD)


def _mcp_normalizza_path_asset(path):
    """Path del Content Browser, senza il suffisso `.NomeOggetto` finale."""
    testo = str(path).rstrip("/")
    if not testo.startswith("/"):
        raise ValueError(
            "I path degli asset iniziano con /Game (o /Engine): ricevuto %r" % path
        )
    nome = testo.rsplit("/", 1)[-1]
    if "." in nome:
        testo = testo[: testo.rfind(".")]
    return testo


def mcp_level_users_of(asset_path):
    """Attori del livello corrente che sono istanze dell'asset indicato.

    `find_package_referencers_for_asset` guarda solo i riferimenti **su disco**.
    Mentre un agente costruisce, il livello è aperto e modificato ma non
    salvato: gli attori appena spawnati da un Blueprint non compaiono da
    nessuna parte, la protezione contro le cancellazioni pericolose dà via
    libera, e con l'asset spariscono anche loro. È esattamente il momento in cui
    quella protezione dovrebbe funzionare.
    """
    percorso = _mcp_normalizza_path_asset(asset_path)
    usati = []
    for attore in mcp_actor_subsystem().get_all_level_actors():
        try:
            nome_classe = str(attore.get_class().get_path_name())
        except Exception:  # noqa: BLE001
            nome_classe = ""  # attore in uno stato strano: non lo si può attribuire
        # Un Blueprint genera <path>.<Nome>_C: basta il prefisso del pacchetto.
        if nome_classe.startswith(percorso + "."):
            usati.append(attore.get_actor_label())
    return usati


def mcp_delete_asset(path, force=False):
    """Elimina un asset o una cartella dal Content Browser.

    Senza questo tool l'unico modo di rimediare a un import sbagliato è aprire
    l'editor a mano: l'agente può creare ma non ripulire.

    `force=False` rifiuta la cancellazione se qualcosa referenzia l'asset —
    cancellarlo comunque lascia riferimenti rotti nei livelli e nei Blueprint.
    """
    percorso = _mcp_normalizza_path_asset(path)
    libreria = mcp_asset_lib()

    cartella = libreria.does_directory_exist(percorso)
    if not cartella and not libreria.does_asset_exist(percorso):
        raise ValueError("Nessun asset né cartella a %s" % percorso)

    referenti = []
    if not cartella:
        try:
            referenti = [str(r) for r in libreria.find_package_referencers_for_asset(percorso, False)]
        except Exception:  # noqa: BLE001
            referenti = []
        nel_livello = mcp_level_users_of(percorso)
        if nel_livello:
            referenti = referenti + ["(nel livello) %s" % etichetta for etichetta in nel_livello]
        if referenti and not force:
            raise ValueError(
                "%s è referenziato da %d elementi (%s). Cancellarlo lascerebbe "
                "riferimenti rotti, e gli attori che lo istanziano nel livello "
                "aperto sparirebbero insieme a lui: usa force=True se è quello "
                "che vuoi." % (percorso, len(referenti), ", ".join(referenti[:5]))
            )

    with mcp_transaction("MCP: elimina %s" % percorso):
        if cartella:
            ok = libreria.delete_directory(percorso)
        else:
            ok = libreria.delete_asset(percorso)

    return {
        "path": percorso,
        "deleted": bool(ok),
        "was_directory": bool(cartella),
        "referencers": referenti,
    }


def mcp_rename_asset(path, new_path):
    """Sposta o rinomina un asset, aggiornando i riferimenti."""
    origine = _mcp_normalizza_path_asset(path)
    destinazione = _mcp_normalizza_path_asset(new_path)
    libreria = mcp_asset_lib()

    if not libreria.does_asset_exist(origine):
        raise ValueError("Nessun asset a %s" % origine)
    if libreria.does_asset_exist(destinazione):
        raise ValueError("Esiste già un asset a %s" % destinazione)

    with mcp_transaction("MCP: rinomina %s" % origine):
        ok = libreria.rename_asset(origine, destinazione)
    return {"from": origine, "to": destinazione, "renamed": bool(ok)}


def mcp_duplicate_asset(path, new_path):
    """Duplica un asset: la via rapida per una variante da modificare."""
    origine = _mcp_normalizza_path_asset(path)
    destinazione = _mcp_normalizza_path_asset(new_path)
    libreria = mcp_asset_lib()

    if not libreria.does_asset_exist(origine):
        raise ValueError("Nessun asset a %s" % origine)
    if libreria.does_asset_exist(destinazione):
        raise ValueError("Esiste già un asset a %s" % destinazione)

    with mcp_transaction("MCP: duplica %s" % origine):
        copia = libreria.duplicate_asset(origine, destinazione)
    return {"from": origine, "to": destinazione, "duplicated": copia is not None}


def mcp_make_folder(path):
    """Crea una cartella nel Content Browser (idempotente)."""
    percorso = _mcp_normalizza_path_asset(path)
    libreria = mcp_asset_lib()
    esisteva = libreria.does_directory_exist(percorso)
    ok = True if esisteva else libreria.make_directory(percorso)
    return {"path": percorso, "created": bool(ok) and not esisteva, "existed": bool(esisteva)}


# ------------------------------------------------------- gerarchia di attori


#: Regole di attach esposte dai tool, mappate sull'enum di Unreal.
_MCP_ATTACH_RULES = ("KEEP_RELATIVE", "KEEP_WORLD", "SNAP_TO_TARGET")


def _mcp_attach_rule(nome):
    chiave = str(nome or "KEEP_WORLD").upper()
    if chiave not in _MCP_ATTACH_RULES:
        raise ValueError(
            "attach_rule deve essere uno di %s, ricevuto %r"
            % (", ".join(_MCP_ATTACH_RULES), nome)
        )
    return getattr(unreal.AttachmentRule, chiave)


def mcp_attach_actor(child_label, parent_label, socket=None, attach_rule="KEEP_WORLD"):
    """Aggancia un attore a un altro: muovendo il padre si muove il figlio.

    È il modo in cui si compone una scena — le ruote a un veicolo, le luci a un
    lampione — e senza di esso l'agente può solo posizionare oggetti slegati
    che poi si spostano uno per uno.
    """
    figlio = mcp_require_actor(child_label)
    padre = mcp_require_actor(parent_label)
    if figlio is padre:
        raise ValueError("Un attore non può essere agganciato a se stesso: %s" % child_label)

    regola = _mcp_attach_rule(attach_rule)
    with mcp_transaction("MCP: aggancia %s a %s" % (child_label, parent_label)):
        figlio.attach_to_actor(
            padre,
            str(socket or ""),
            location_rule=regola,
            rotation_rule=regola,
            scale_rule=regola,
            weld_simulated_bodies=False,
        )

    return {
        "child": child_label,
        "parent": padre.get_actor_label(),
        "socket": socket,
        "attach_rule": str(attach_rule).upper(),
        "attached": figlio.get_attach_parent_actor() is not None,
    }


def mcp_detach_actor(label, keep_world=True):
    """Sgancia un attore dal suo padre."""
    attore = mcp_require_actor(label)
    padre = attore.get_attach_parent_actor()
    if padre is None:
        return {"label": label, "detached": False, "reason": "non era agganciato"}

    regola = unreal.DetachmentRule.KEEP_WORLD if keep_world else unreal.DetachmentRule.KEEP_RELATIVE
    with mcp_transaction("MCP: sgancia %s" % label):
        attore.detach_from_actor(
            location_rule=regola, rotation_rule=regola, scale_rule=regola
        )
    return {"label": label, "was_attached_to": padre.get_actor_label(), "detached": True}


def mcp_actor_hierarchy(label=None):
    """Albero padre/figli degli attori del livello."""
    if label is not None:
        radici = [mcp_require_actor(label)]
    else:
        radici = [
            a
            for a in mcp_actor_subsystem().get_all_level_actors()
            if a.get_attach_parent_actor() is None
        ]

    def ramo(attore):
        figli = list(attore.get_attached_actors() or [])
        return {
            "label": attore.get_actor_label(),
            "class": str(attore.get_class().get_name()),
            "children": [ramo(f) for f in figli],
        }

    return [ramo(a) for a in radici]


def mcp_spawn_one(class_or_asset, location=None, rotation=None, scale=None, label=None):
    """Spawn di un singolo attore, senza aprire una transazione propria."""
    loc = mcp_to_vector(location)
    rot = mcp_to_rotator(rotation)
    subsystem = mcp_actor_subsystem()

    actor = None
    if isinstance(class_or_asset, str) and class_or_asset.startswith("/Game/"):
        asset_path = class_or_asset[:-2] if class_or_asset.endswith("_C") else class_or_asset
        asset = mcp_asset_lib().load_asset(asset_path) if mcp_asset_lib().does_asset_exist(asset_path) else None
        if asset is not None and not isinstance(asset, unreal.Blueprint):
            actor = subsystem.spawn_actor_from_object(asset, loc, rot)
    if actor is None:
        actor = subsystem.spawn_actor_from_class(mcp_resolve_class(class_or_asset), loc, rot)

    if actor is None:
        raise RuntimeError("Spawn fallito per '%s'." % class_or_asset)
    if scale is not None:
        actor.set_actor_scale3d(mcp_to_vector(scale, (1.0, 1.0, 1.0)))
    if label:
        actor.set_actor_label(label)
    return mcp_actor_info(actor)


def mcp_spawn(class_or_asset, location=None, rotation=None, scale=None, label=None):
    with mcp_transaction("MCP: spawn %s" % class_or_asset):
        return mcp_spawn_one(class_or_asset, location, rotation, scale, label)


def mcp_spawn_many(items, transaction_label="MCP: spawn multiplo"):
    """Spawna molti attori in un solo round-trip e in una sola transazione.

    Costruire un livello un attore per chiamata significa un round-trip HTTP
    ciascuno; qui la lista arriva tutta insieme. Un fallimento su un elemento
    non ferma gli altri: viene riportato nel risultato.

    Ogni elemento è un dict con: class_ref (obbligatorio), location, rotation,
    scale, label.
    """
    spawned = []
    failed = []
    with mcp_transaction(transaction_label):
        for index, item in enumerate(items):
            if not isinstance(item, dict) or not item.get("class_ref"):
                failed.append({"index": index, "error": "manca 'class_ref'", "item": item})
                continue
            try:
                spawned.append(
                    mcp_spawn_one(
                        item["class_ref"],
                        item.get("location"),
                        item.get("rotation"),
                        item.get("scale"),
                        item.get("label"),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                failed.append(
                    {"index": index, "error": "%s: %s" % (type(exc).__name__, exc), "item": item}
                )
    return {
        "requested": len(items),
        "spawned": len(spawned),
        "actors": spawned,
        "failed": failed,
    }


def mcp_find_actors(name_contains=None, class_contains=None):
    out = []
    for actor in mcp_actor_subsystem().get_all_level_actors():
        info = mcp_actor_info(actor)
        if name_contains and name_contains.lower() not in (info["label"] + info["name"]).lower():
            continue
        if class_contains and class_contains.lower() not in info["class"].lower():
            continue
        out.append(info)
    return out


def mcp_actor_by_label(label):
    for actor in mcp_actor_subsystem().get_all_level_actors():
        if actor.get_actor_label() == label:
            return actor
    return None


def mcp_require_actor(label):
    actor = mcp_actor_by_label(label)
    if actor is None:
        etichette = [a.get_actor_label() for a in mcp_actor_subsystem().get_all_level_actors()]
        raise ValueError(
            "Nessun attore con label '%s' nel livello corrente. Presenti: %s"
            % (label, ", ".join(sorted(etichette)[:20]) or "nessuno")
        )
    return actor


def mcp_set_transform(label, location=None, rotation=None, scale=None):
    """Sposta/ruota/scala un attore, in una transazione annullabile."""
    actor = mcp_require_actor(label)
    with mcp_transaction("MCP: trasforma %s" % label):
        if location is not None:
            actor.set_actor_location(mcp_to_vector(location), False, False)
        if rotation is not None:
            actor.set_actor_rotation(mcp_to_rotator(rotation), False)
        if scale is not None:
            actor.set_actor_scale3d(mcp_to_vector(scale, (1.0, 1.0, 1.0)))
    return mcp_actor_info(actor)


def mcp_delete_actor(label):
    actor = mcp_require_actor(label)
    with mcp_transaction("MCP: elimina %s" % label):
        mcp_actor_subsystem().destroy_actor(actor)
    return {"deleted": label}


def mcp_coerce_value(value):
    """Converte i valori JSON nei tipi Unreal attesi dalle proprietà.

    Il ponte MCP trasporta solo JSON: un Vector arriva come dict e un asset
    come stringa di path. Senza questa conversione `set_editor_property`
    rifiuta il valore.
    """
    if isinstance(value, dict):
        chiavi = {k.lower() for k in value}
        if {"x", "y", "z"} <= chiavi:
            return mcp_to_vector(value)
        if {"pitch", "yaw", "roll"} <= chiavi:
            return mcp_to_rotator(value)
        if {"r", "g", "b"} <= chiavi:
            return unreal.LinearColor(
                float(value.get("r", 0.0)),
                float(value.get("g", 0.0)),
                float(value.get("b", 0.0)),
                float(value.get("a", 1.0)),
            )
        return value
    if isinstance(value, str) and value.startswith(("/Game/", "/Engine/")):
        if mcp_asset_lib().does_asset_exist(value):
            return mcp_asset_lib().load_asset(value)
    return value


def mcp_actor_components(actor):
    """Componenti dell'attore, con nome e classe."""
    try:
        componenti = actor.get_components_by_class(unreal.ActorComponent)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for componente in componenti or []:
        try:
            out.append(
                {
                    "name": str(componente.get_name()),
                    "class": str(componente.get_class().get_name()),
                    "object": componente,
                }
            )
        except Exception:  # noqa: BLE001, S112 - un componente illeggibile non deve nascondere gli altri
            continue
    return out


def mcp_find_component(actor, riferimento):
    """Trova un componente per nome o per nome di classe (match parziale)."""
    componenti = mcp_actor_components(actor)
    cercato = str(riferimento).lower()
    for componente in componenti:
        if componente["name"].lower() == cercato:
            return componente["object"]
    for componente in componenti:
        if cercato in componente["class"].lower() or cercato in componente["name"].lower():
            return componente["object"]
    disponibili = ", ".join("%s (%s)" % (c["name"], c["class"]) for c in componenti)
    raise ValueError(
        "Nessun componente '%s' su '%s'. Disponibili: %s"
        % (riferimento, actor.get_actor_label(), disponibili or "nessuno")
    )


def mcp_set_actor_property(label, properties, component=None):
    """Imposta proprietà su un attore già piazzato (o su un suo componente).

    Serve per tutto ciò che non sono i Class Defaults di un Blueprint: la mesh
    di uno StaticMeshActor, l'intensità di una luce, il raggio di un trigger.
    """
    actor = mcp_require_actor(label)
    target = actor if component is None else mcp_find_component(actor, component)

    applied = {}
    failed = {}
    with mcp_transaction("MCP: proprietà di %s" % label):
        for chiave, valore in (properties or {}).items():
            try:
                target.set_editor_property(chiave, mcp_coerce_value(valore))
                applied[chiave] = valore
            except Exception as exc:  # noqa: BLE001
                failed[chiave] = "%s: %s" % (type(exc).__name__, exc)

    return {
        "actor": label,
        "component": component,
        "applied": applied,
        "failed": failed,
        "info": mcp_actor_info(actor),
    }


def mcp_actor_component_list(label):
    """Elenca i componenti di un attore piazzato, per sapere cosa si può impostare."""
    actor = mcp_require_actor(label)
    return {
        "actor": label,
        "components": [
            {"name": c["name"], "class": c["class"]} for c in mcp_actor_components(actor)
        ],
    }


# ----------------------------------------------------------------- blueprint


def mcp_create_blueprint(package_path, name, parent_class="Actor"):
    full = "%s/%s" % (package_path.rstrip("/"), name)
    if mcp_asset_lib().does_asset_exist(full):
        return {"path": full, "created": False, "reason": "esiste già"}

    factory = unreal.BlueprintFactory()
    factory.set_editor_property("parent_class", mcp_resolve_class(parent_class))
    asset = mcp_asset_tools().create_asset(name, package_path, unreal.Blueprint, factory)
    if asset is None:
        raise RuntimeError("Creazione Blueprint '%s' fallita." % full)
    mcp_asset_lib().save_asset(full)
    return {"path": full, "created": True, "parent": str(parent_class)}


def mcp_load_blueprint(path):
    asset = mcp_asset_lib().load_asset(path)
    if asset is None:
        raise ValueError("Blueprint '%s' non trovato." % path)
    if not isinstance(asset, unreal.Blueprint):
        raise ValueError("'%s' non è un Blueprint (è %s)." % (path, asset.get_class().get_name()))
    return asset


def mcp_compile_blueprint(path):
    blueprint = mcp_load_blueprint(path)
    unreal.BlueprintEditorLibrary.compile_blueprint(blueprint)
    mcp_asset_lib().save_asset(path)
    return {"path": path, "compiled": True}


def mcp_reparent_blueprint(blueprint_path, new_parent, remove_unused_variables=False):
    """Riassegna il parent di un Blueprint, tipicamente a una classe C++.

    È il modo per dare logica a un Blueprint senza poterne scrivere il grafo:
    la logica sta nella classe C++ padre, il Blueprint resta il contenitore di
    dati e componenti. Le variabili del Blueprint con lo stesso nome di una
    UPROPERTY del nuovo padre vengono assorbite; le altre sopravvivono con un
    suffisso `_0`, e `remove_unused_variables` le ripulisce.
    """
    library = unreal.BlueprintEditorLibrary
    if not hasattr(library, "reparent_blueprint"):
        raise RuntimeError(
            "BlueprintEditorLibrary.reparent_blueprint non è disponibile in questa "
            "versione del motore (%s): cambia il parent da Class Settings "
            "nell'editor del Blueprint." % unreal.SystemLibrary.get_engine_version()
        )

    blueprint = mcp_load_blueprint(blueprint_path)
    classe = mcp_resolve_class(new_parent)

    prima = [str(n) for n in (library.list_member_variable_names(blueprint) or [])]
    library.reparent_blueprint(blueprint, classe)
    library.compile_blueprint(blueprint)

    rimosse = False
    if remove_unused_variables and hasattr(library, "remove_unused_variables"):
        library.remove_unused_variables(blueprint)
        library.compile_blueprint(blueprint)
        rimosse = True

    dopo = [str(n) for n in (library.list_member_variable_names(blueprint) or [])]
    mcp_asset_lib().save_asset(blueprint_path)

    return {
        "blueprint": blueprint_path,
        "new_parent": str(new_parent),
        "variables_before": prima,
        "variables_after": dopo,
        "absorbed_by_parent": sorted(set(prima) - set(dopo)),
        "unused_removed": rimosse,
    }


def mcp_add_component(blueprint_path, component_class, name=None):
    blueprint = mcp_load_blueprint(blueprint_path)
    subsystem = unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)
    handles = subsystem.k2_gather_subobject_data_for_blueprint(blueprint)
    if not handles:
        raise RuntimeError("Nessun subobject radice in '%s'." % blueprint_path)

    params = unreal.AddNewSubobjectParams()
    params.set_editor_property("parent_handle", handles[0])
    params.set_editor_property("new_class", mcp_resolve_class(component_class))
    params.set_editor_property("blueprint_context", blueprint)

    handle, failure = subsystem.add_new_subobject(params)
    if failure and not failure.is_empty():
        raise RuntimeError("Aggiunta componente fallita: %s" % failure)
    if name:
        subsystem.rename_subobject(handle, unreal.Text(name))

    unreal.BlueprintEditorLibrary.compile_blueprint(blueprint)
    mcp_asset_lib().save_asset(blueprint_path)
    return {"blueprint": blueprint_path, "component": str(component_class), "name": name}


#: Path degli struct usabili come sub_type per le variabili Blueprint.
MCP_STRUCT_PATHS = {
    "vector": "/Script/CoreUObject.Vector",
    "rotator": "/Script/CoreUObject.Rotator",
    "transform": "/Script/CoreUObject.Transform",
    "linearcolor": "/Script/CoreUObject.LinearColor",
    "vector2d": "/Script/CoreUObject.Vector2D",
}

#: Classi comuni per le variabili di tipo object/class.
MCP_CLASS_PATHS = {
    "actor": "/Script/Engine.Actor",
    "pawn": "/Script/Engine.Pawn",
    "character": "/Script/Engine.Character",
    "playerstate": "/Script/Engine.PlayerState",
    "controller": "/Script/Engine.Controller",
    "playercontroller": "/Script/Engine.PlayerController",
    "soundbase": "/Script/Engine.SoundBase",
    "staticmesh": "/Script/Engine.StaticMesh",
}


def mcp_pin_type(type_name, sub_type=None):
    """Costruisce un EdGraphPinType.

    In UE 5.8 le proprietà di EdGraphPinType non sono esposte a Python
    (`set_editor_property` fallisce): l'unica via è `import_text` con la
    sintassi di serializzazione delle UStruct.
    """
    key = str(type_name).lower()

    if key in ("bool", "boolean"):
        text = '(PinCategory="bool")'
    elif key in ("int", "int32", "integer"):
        text = '(PinCategory="int")'
    elif key == "int64":
        text = '(PinCategory="int64")'
    elif key in ("float", "double", "real"):
        text = '(PinCategory="real",PinSubCategory="double")'
    elif key in ("string", "str"):
        text = '(PinCategory="string")'
    elif key == "name":
        text = '(PinCategory="name")'
    elif key == "text":
        text = '(PinCategory="text")'
    elif key == "byte":
        text = '(PinCategory="byte")'
    elif key == "struct":
        name = str(sub_type or "Vector")
        path = MCP_STRUCT_PATHS.get(name.lower(), name if name.startswith("/") else None)
        if path is None:
            raise ValueError(
                "Struct '%s' non riconosciuta. Usa %s oppure un path completo."
                % (name, ", ".join(sorted(MCP_STRUCT_PATHS)))
            )
        text = '(PinCategory="struct",PinSubCategoryObject="%s")' % path
    elif key in ("object", "actor", "class"):
        name = str(sub_type or "Actor")
        path = MCP_CLASS_PATHS.get(name.lower())
        if path is None:
            path = name if name.startswith("/") else None
        if path is None:
            resolved = mcp_resolve_class(name)
            path = resolved.get_path_name() if resolved else None
        if path is None:
            raise ValueError("Classe '%s' non risolvibile per una variabile." % name)
        category = "class" if key == "class" else "object"
        text = '(PinCategory="%s",PinSubCategoryObject="%s")' % (category, path)
    else:
        raise ValueError(
            "Tipo '%s' non supportato. Usa: bool, int, int64, float, string, name, text, "
            "byte, struct (con sub_type), object/class (con sub_type)." % type_name
        )

    pin = unreal.EdGraphPinType()
    if not pin.import_text(text):
        raise RuntimeError("Costruzione del tipo fallita per '%s' (%s)." % (type_name, text))
    return pin


def mcp_coerce_to_var_type(value, var_type):
    """Porta un valore JSON al tipo dichiarato della variabile Blueprint.

    Il ponte trasporta JSON e i client non sono coerenti: lo stesso `100` può
    arrivare come numero o come `"100"` a seconda di come il client serializza
    un parametro dallo schema aperto. `set_editor_property` non perdona:
    assegnare una stringa a una DoubleProperty solleva

        TypeError: Cannot nativize 'str' as 'double'

    Convertire qui, in base al tipo che è stato appena creato, costa poco ed
    evita che il tool dipenda dai capricci di serializzazione del client.
    """
    if value is None:
        return None
    tipo = str(var_type or "").lower()

    if tipo == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes", "si", "sì")
        return bool(value)

    if tipo in ("int", "int64", "byte"):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            raise ValueError(
                "default_value %r non è convertibile in un intero per una "
                "variabile di tipo %s" % (value, var_type)
            ) from None

    if tipo in ("float", "double", "real"):
        try:
            return float(value)
        except (TypeError, ValueError):
            raise ValueError(
                "default_value %r non è convertibile in un numero per una "
                "variabile di tipo %s" % (value, var_type)
            ) from None

    if tipo in ("string", "name", "text"):
        return str(value)

    # struct, object, class: ci pensa mcp_coerce_value (dict -> Vector, path -> asset)
    return mcp_coerce_value(value)


def mcp_add_variable(
    blueprint_path,
    var_name,
    var_type="float",
    sub_type=None,
    replicated=False,
    instance_editable=True,
    default_value=None,
):
    """Aggiunge una variabile membro usando l'API ufficiale di UE 5.4+.

    `BlueprintEditorLibrary.add_member_variable` è la strada supportata; la
    proprietà `new_variables` di UBlueprint non è esposta a Python.
    """
    library = unreal.BlueprintEditorLibrary
    if not hasattr(library, "add_member_variable"):
        raise RuntimeError(
            "ue_add_variable richiede UE 5.4+: BlueprintEditorLibrary.add_member_variable "
            "non esiste in questa versione del motore (%s). Su motori precedenti le "
            "variabili vanno aggiunte a mano nell'editor o dichiarate in C++."
            % unreal.SystemLibrary.get_engine_version()
        )
    blueprint = mcp_load_blueprint(blueprint_path)

    existing = [str(n) for n in (library.list_member_variable_names(blueprint) or [])]
    created = var_name not in existing
    if created:
        if not library.add_member_variable(blueprint, unreal.Name(var_name), mcp_pin_type(var_type, sub_type)):
            raise RuntimeError(
                "add_member_variable ha rifiutato '%s' di tipo '%s' su %s."
                % (var_name, var_type, blueprint_path)
            )

    if hasattr(library, "set_blueprint_variable_instance_editable"):
        library.set_blueprint_variable_instance_editable(
            blueprint, unreal.Name(var_name), bool(instance_editable)
        )
    if replicated and not hasattr(library, "set_blueprint_variable_replication"):
        raise RuntimeError(
            "La replication per-variabile via Python richiede UE 5.4+ "
            "(set_blueprint_variable_replication assente in %s)."
            % unreal.SystemLibrary.get_engine_version()
        )
    if hasattr(library, "set_blueprint_variable_replication"):
        library.set_blueprint_variable_replication(
            blueprint,
            unreal.Name(var_name),
            unreal.BlueprintVariableReplication.REPLICATED
            if replicated
            else unreal.BlueprintVariableReplication.NONE,
        )

    library.compile_blueprint(blueprint)

    applied_default = None
    if default_value is not None:
        valore = mcp_coerce_to_var_type(default_value, var_type)
        cdo = unreal.get_default_object(blueprint.generated_class())
        cdo.set_editor_property(var_name, valore)
        library.compile_blueprint(blueprint)
        applied_default = valore

    mcp_asset_lib().save_asset(blueprint_path)
    return {
        "blueprint": blueprint_path,
        "variable": var_name,
        "type": var_type,
        "replicated": bool(replicated),
        "default": applied_default,
        "created": created,
    }


def mcp_set_class_defaults(blueprint_path, properties):
    blueprint = mcp_load_blueprint(blueprint_path)
    cdo = unreal.get_default_object(blueprint.generated_class())
    applied = {}
    for key, value in properties.items():
        cdo.set_editor_property(key, value)
        applied[key] = value
    unreal.BlueprintEditorLibrary.compile_blueprint(blueprint)
    mcp_asset_lib().save_asset(blueprint_path)
    return {"blueprint": blueprint_path, "applied": applied}


# ----------------------------------------------------------------- materiali

#: Mappa nome amichevole -> proprietà di FMaterialEditingLibrary.
#: I nomi corrispondono a EMaterialProperty (senza il prefisso MP_).
MCP_MATERIAL_SLOTS = {
    "base_color": "MP_BASE_COLOR",
    "metallic": "MP_METALLIC",
    "specular": "MP_SPECULAR",
    "roughness": "MP_ROUGHNESS",
    "emissive": "MP_EMISSIVE_COLOR",
    "emissive_color": "MP_EMISSIVE_COLOR",
    "opacity": "MP_OPACITY",
    "opacity_mask": "MP_OPACITY_MASK",
    "normal": "MP_NORMAL",
    "ambient_occlusion": "MP_AMBIENT_OCCLUSION",
}

#: Suffissi tipici dei file PBR scaricati da ambientCG / Poly Haven,
#: usati per collegare automaticamente le texture ai canali giusti.
MCP_TEXTURE_HINTS = (
    ("normalgl", "normal"), ("normaldx", "normal"), ("normal", "normal"), ("_nrm", "normal"),
    ("basecolor", "base_color"), ("albedo", "base_color"), ("diffuse", "base_color"),
    ("_col", "base_color"), ("color", "base_color"),
    ("roughness", "roughness"), ("_rgh", "roughness"),
    ("metalness", "metallic"), ("metallic", "metallic"), ("_mtl", "metallic"),
    ("ambientocclusion", "ambient_occlusion"), ("_ao", "ambient_occlusion"),
    ("displacement", None), ("height", None),
)


def mcp_material_lib():
    if not hasattr(unreal, "MaterialEditingLibrary"):
        raise RuntimeError(
            "MaterialEditingLibrary non disponibile in questa versione del motore: "
            "i materiali vanno creati a mano nell'editor."
        )
    return unreal.MaterialEditingLibrary


def mcp_material_property(nome):
    slot = MCP_MATERIAL_SLOTS.get(str(nome).lower())
    if slot is None:
        raise ValueError(
            "Canale materiale '%s' non riconosciuto. Disponibili: %s"
            % (nome, ", ".join(sorted(MCP_MATERIAL_SLOTS)))
        )
    return getattr(unreal.MaterialProperty, slot)


def mcp_guess_channel(percorso_texture):
    """Deduce il canale PBR dal nome file (convenzioni ambientCG / Poly Haven)."""
    nome = str(percorso_texture).rsplit("/", 1)[-1].lower()
    for indizio, canale in MCP_TEXTURE_HINTS:
        if indizio in nome:
            return canale
    return None


def mcp_create_material(package_path, name, textures=None, scalars=None, two_sided=False):
    """Crea un materiale e vi collega le texture indicate.

    `textures` è una mappa canale -> path della texture importata; se il canale
    è `"auto"` viene dedotto dal nome del file. `scalars` imposta costanti
    (es. {"roughness": 0.4}) sui canali senza texture.

    Il grafo *materiale* è pienamente scriptabile da Python, a differenza di
    quello Blueprint: qui si costruiscono davvero i nodi e i collegamenti.
    """
    full = "%s/%s" % (package_path.rstrip("/"), name)
    library = mcp_material_lib()

    if mcp_asset_lib().does_asset_exist(full):
        return {"path": full, "created": False, "reason": "esiste già"}

    materiale = mcp_asset_tools().create_asset(
        name, package_path, unreal.Material, unreal.MaterialFactoryNew()
    )
    if materiale is None:
        raise RuntimeError("Creazione del materiale '%s' fallita." % full)

    if two_sided:
        materiale.set_editor_property("two_sided", True)

    collegate = {}
    saltate = []
    altezza = -350

    for canale, percorso in (textures or {}).items():
        effettivo = mcp_guess_channel(percorso) if str(canale).lower() == "auto" else canale
        if effettivo is None:
            saltate.append({"texture": percorso, "reason": "canale non deducibile dal nome"})
            continue
        if not mcp_asset_lib().does_asset_exist(percorso):
            saltate.append({"texture": percorso, "reason": "asset non trovato"})
            continue

        texture = mcp_asset_lib().load_asset(percorso)
        nodo = library.create_material_expression(
            materiale, unreal.MaterialExpressionTextureSampleParameter2D, -400, altezza
        )
        nodo.set_editor_property("texture", texture)
        nodo.set_editor_property("parameter_name", unreal.Name(effettivo))
        # Le normal map hanno una compressione e uno spazio colore diversi:
        # senza questo il canale normale viene interpretato come colore.
        if effettivo == "normal":
            texture.set_editor_property("srgb", False)
            nodo.set_editor_property("sampler_type", unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL)

        library.connect_material_property(nodo, "", mcp_material_property(effettivo))
        collegate[effettivo] = percorso
        altezza += 250

    for canale, valore in (scalars or {}).items():
        if canale in collegate:
            continue
        nodo = library.create_material_expression(
            materiale, unreal.MaterialExpressionScalarParameter, -400, altezza
        )
        nodo.set_editor_property("parameter_name", unreal.Name(canale))
        nodo.set_editor_property("default_value", float(valore))
        library.connect_material_property(nodo, "", mcp_material_property(canale))
        collegate[canale] = float(valore)
        altezza += 150

    library.recompile_material(materiale)
    mcp_asset_lib().save_asset(full)
    return {
        "path": full,
        "created": True,
        "connected": collegate,
        "skipped": saltate,
    }


def mcp_create_material_instance(package_path, name, parent_path, parameters=None):
    """Crea una Material Instance da un materiale padre e ne imposta i parametri.

    I parametri sono la via economica per variare un materiale: niente
    ricompilazione del grafo, e si possono cambiare a runtime.
    """
    full = "%s/%s" % (package_path.rstrip("/"), name)
    library = mcp_material_lib()

    if mcp_asset_lib().does_asset_exist(full):
        return {"path": full, "created": False, "reason": "esiste già"}

    padre = mcp_asset_lib().load_asset(parent_path)
    if padre is None:
        raise ValueError("Materiale padre '%s' non trovato." % parent_path)

    istanza = mcp_asset_tools().create_asset(
        name, package_path, unreal.MaterialInstanceConstant, unreal.MaterialInstanceConstantFactoryNew()
    )
    if istanza is None:
        raise RuntimeError("Creazione della material instance '%s' fallita." % full)

    library.set_material_instance_parent(istanza, padre)

    applicati = {}
    for chiave, valore in (parameters or {}).items():
        nome = unreal.Name(chiave)
        if isinstance(valore, str) and mcp_asset_lib().does_asset_exist(valore):
            library.set_material_instance_texture_parameter_value(
                istanza, nome, mcp_asset_lib().load_asset(valore)
            )
        elif isinstance(valore, dict):
            library.set_material_instance_vector_parameter_value(
                istanza, nome, mcp_coerce_value(valore)
            )
        elif isinstance(valore, bool):
            library.set_material_instance_static_switch_parameter_value(istanza, nome, valore)
        else:
            library.set_material_instance_scalar_parameter_value(istanza, nome, float(valore))
        applicati[chiave] = valore

    mcp_asset_lib().save_asset(full)
    return {"path": full, "created": True, "parent": parent_path, "parameters": applicati}


def mcp_assign_material(label, material_path, slot=0, component=None):
    """Assegna un materiale a un attore piazzato."""
    actor = mcp_require_actor(label)
    materiale = mcp_asset_lib().load_asset(material_path)
    if materiale is None:
        raise ValueError("Materiale '%s' non trovato." % material_path)

    target = mcp_find_component(actor, component or "MeshComponent")
    with mcp_transaction("MCP: materiale su %s" % label):
        target.set_material(int(slot), materiale)

    return {
        "actor": label,
        "material": material_path,
        "slot": int(slot),
        "component": str(target.get_name()),
    }


# ---------------------------------------------------------------- screenshot


def mcp_screenshot(filename=None, width=1280, height=720):
    """Cattura la viewport dell'editor in un file PNG e ne restituisce il path.

    È l'unico modo perché l'agente veda il risultato di quello che costruisce,
    invece di dedurlo dalle coordinate.
    """
    cartella = os.path.join(
        mcp_full_path(unreal.Paths.project_saved_dir()), "Screenshots", "MCP"
    )
    if not os.path.isdir(cartella):
        os.makedirs(cartella)

    nome = filename or ("mcp_%d.png" % int(_mcp_time().time() * 1000))
    if not nome.lower().endswith(".png"):
        nome += ".png"
    destinazione = mcp_full_path(os.path.join(cartella, nome))

    if hasattr(unreal, "AutomationLibrary"):
        unreal.AutomationLibrary.take_high_res_screenshot(
            int(width), int(height), destinazione
        )
    else:
        mondo = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
        unreal.SystemLibrary.execute_console_command(
            mondo, 'HighResShot %dx%d filename="%s"' % (int(width), int(height), destinazione)
        )

    # La cattura è asincrona: il file compare uno o due frame dopo la richiesta.
    scadenza = _mcp_time().time() + 15.0
    while _mcp_time().time() < scadenza:
        if os.path.exists(destinazione) and os.path.getsize(destinazione) > 0:
            break
        _mcp_time().sleep(0.25)

    esiste = os.path.exists(destinazione)
    return {
        "file": destinazione if esiste else None,
        "requested": destinazione,
        "width": int(width),
        "height": int(height),
        "captured": esiste,
        "note": None
        if esiste
        else (
            "Screenshot non ancora scritto su disco. In editor la cattura avviene al "
            "frame successivo: riprova, oppure controlla che la viewport sia visibile."
        ),
    }


def _mcp_time():
    import time as modulo

    return modulo


# ---------------------------------------------------------------- networking


def mcp_set_replication(blueprint_path, replicates=True, replicate_movement=True, always_relevant=False):
    blueprint = mcp_load_blueprint(blueprint_path)
    cdo = unreal.get_default_object(blueprint.generated_class())
    applied = {}
    for prop, value in (
        ("replicates", replicates),
        ("replicate_movement", replicate_movement),
        ("always_relevant", always_relevant),
    ):
        try:
            cdo.set_editor_property(prop, bool(value))
            applied[prop] = bool(value)
        except Exception as exc:  # noqa: BLE001
            applied[prop] = "non applicabile (%s)" % exc
    unreal.BlueprintEditorLibrary.compile_blueprint(blueprint)
    mcp_asset_lib().save_asset(blueprint_path)
    return {"blueprint": blueprint_path, "applied": applied}


# Networking esteso (fase 8). Verificato dal vivo su UE 5.8 il 2026-07-31:
# tutte le proprietà di rete di `AActor` (dormancy, frequenze di update,
# priorità, cull distance, relevancy) sono `get/set_editor_property` normali
# sulla CDO del Blueprint, e persistono su disco (controllato leggendo i nomi
# delle proprietà nel .uasset dopo il salvataggio). Niente muro tipo
# WidgetTree/EdGraph qui: la fase 8 è l'unica finora senza ridimensionamenti
# oltre alla 4 e alla 6.

#: Nomi accettati per `NetDormancy` → membro di `unreal.NetDormancy`.
MCP_NET_DORMANCY = {
    "awake": "DORM_AWAKE",
    "dormant_all": "DORM_DORMANT_ALL",
    "dormant_partial": "DORM_DORMANT_PARTIAL",
    "initial": "DORM_INITIAL",
    "never": "DORM_NEVER",
}

#: Proprietà di rete numeriche/booleane scrivibili sulla CDO di un Actor.
MCP_NET_PROPS = (
    "net_update_frequency",
    "min_net_update_frequency",
    "net_priority",
    "net_cull_distance_squared",
    "only_relevant_to_owner",
    "net_use_owner_relevancy",
    "net_load_on_client",
    "replicates",
    "replicate_movement",
    "always_relevant",
)


def _mcp_net_dormancy(nome):
    chiave = str(nome).lower().replace("-", "_").replace(" ", "_")
    chiave = chiave[5:] if chiave.startswith("dorm_") else chiave
    membro = MCP_NET_DORMANCY.get(chiave)
    if membro is None:
        raise ValueError(
            "dormancy '%s' sconosciuta. Validi: %s"
            % (nome, ", ".join(sorted(MCP_NET_DORMANCY)))
        )
    return getattr(unreal.NetDormancy, membro)


def mcp_net_info(blueprint_path):
    """Tutte le proprietà di rete della CDO di un Blueprint Actor."""
    blueprint = mcp_load_blueprint(blueprint_path)
    cdo = unreal.get_default_object(blueprint.generated_class())

    info = {"blueprint": blueprint_path}
    try:
        info["dormancy"] = str(cdo.get_editor_property("net_dormancy"))
    except Exception as exc:  # noqa: BLE001
        info["dormancy"] = "non leggibile (%s)" % exc
    for prop in MCP_NET_PROPS:
        try:
            valore = cdo.get_editor_property(prop)
            info[prop] = bool(valore) if isinstance(valore, bool) else float(valore)
        except Exception as exc:  # noqa: BLE001
            info[prop] = "non leggibile (%s)" % exc

    quadrato = info.get("net_cull_distance_squared")
    if isinstance(quadrato, float) and quadrato >= 0:
        info["net_cull_distance"] = quadrato**0.5

    componenti = []
    for handle in _mcp_bp_subobject_handles(blueprint)[1:]:
        template = _mcp_subobject_object(handle)
        if template is None or not hasattr(template, "replicates"):
            continue
        try:
            componenti.append(
                {
                    "name": str(template.get_name()),
                    "class": str(template.get_class().get_name()),
                    "replicates": bool(template.get_editor_property("replicates")),
                }
            )
        except Exception:  # noqa: BLE001, S112
            continue
    info["components"] = componenti
    return info


def mcp_set_net_config(
    blueprint_path,
    dormancy=None,
    net_update_frequency=None,
    min_net_update_frequency=None,
    net_priority=None,
    net_cull_distance=None,
    only_relevant_to_owner=None,
    net_use_owner_relevancy=None,
    net_load_on_client=None,
):
    """Configura dormancy, frequenze di update, priorità e relevancy di rete.

    `net_cull_distance` è in centimetri come tutto il resto del server: viene
    elevata al quadrato prima di scriverla in `NetCullDistanceSquared`, che è
    come Unreal la memorizza.
    """
    blueprint = mcp_load_blueprint(blueprint_path)
    cdo = unreal.get_default_object(blueprint.generated_class())

    applicato = {}
    with mcp_transaction("MCP: rete di %s" % blueprint_path):
        if dormancy is not None:
            cdo.set_editor_property("net_dormancy", _mcp_net_dormancy(dormancy))
            applicato["dormancy"] = str(dormancy)

        if net_cull_distance is not None:
            distanza = float(net_cull_distance)
            if distanza < 0:
                raise ValueError("net_cull_distance non può essere negativa.")
            cdo.set_editor_property("net_cull_distance_squared", distanza * distanza)
            applicato["net_cull_distance"] = distanza

        for prop, valore, conversione in (
            ("net_update_frequency", net_update_frequency, float),
            ("min_net_update_frequency", min_net_update_frequency, float),
            ("net_priority", net_priority, float),
            ("only_relevant_to_owner", only_relevant_to_owner, bool),
            ("net_use_owner_relevancy", net_use_owner_relevancy, bool),
            ("net_load_on_client", net_load_on_client, bool),
        ):
            if valore is None:
                continue
            try:
                cdo.set_editor_property(prop, conversione(valore))
                applicato[prop] = conversione(valore)
            except Exception as exc:  # noqa: BLE001
                applicato[prop] = "non applicabile (%s)" % exc

    unreal.BlueprintEditorLibrary.compile_blueprint(blueprint)
    mcp_asset_lib().save_asset(blueprint_path)
    return {"blueprint": blueprint_path, "applied": applicato, "info": mcp_net_info(blueprint_path)}


def _mcp_bp_subobject_handles(blueprint):
    subsystem = unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)
    return list(subsystem.k2_gather_subobject_data_for_blueprint(blueprint))


def _mcp_subobject_object(handle):
    """Oggetto template dietro un handle del SubobjectDataSubsystem.

    Il passaggio handle → dato → oggetto è quello che la fase 6 aveva dato
    per irraggiungibile: `SubobjectDataBlueprintFunctionLibrary.get_object`
    esiste e funziona (verificato dal vivo su UE 5.8), e apre l'accesso ai
    template dei componenti di un Blueprint — non solo alla loro replication.
    """
    subsystem = unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)
    dato = subsystem.k2_find_subobject_data_from_handle(handle)
    if dato is None:
        return None
    return unreal.SubobjectDataBlueprintFunctionLibrary.get_object(dato)


def _mcp_bp_component_template(blueprint, component_name):
    """Template di un componente di un Blueprint, cercato per nome.

    I template hanno il suffisso `_GEN_VARIABLE` nel nome: il confronto lo
    ignora, così chi chiama può usare il nome che vede nell'editor.
    """
    atteso = str(component_name).lower()
    trovati = []
    for handle in _mcp_bp_subobject_handles(blueprint)[1:]:
        template = _mcp_subobject_object(handle)
        if template is None:
            continue
        nome = str(template.get_name())
        trovati.append(nome)
        pulito = nome[: -len("_GEN_VARIABLE")] if nome.endswith("_GEN_VARIABLE") else nome
        if pulito.lower() == atteso or nome.lower() == atteso:
            return template
    raise ValueError(
        "Componente '%s' non trovato. Presenti: %s" % (component_name, ", ".join(trovati) or "nessuno")
    )


def mcp_set_component_replication(blueprint_path, component_name, replicates=True):
    """Attiva/disattiva la replication di un singolo componente di un Blueprint."""
    blueprint = mcp_load_blueprint(blueprint_path)
    template = _mcp_bp_component_template(blueprint, component_name)
    if not hasattr(template, "replicates"):
        raise ValueError(
            "'%s' è un %s: solo gli ActorComponent hanno una replication propria."
            % (component_name, template.get_class().get_name())
        )
    with mcp_transaction("MCP: replication di %s/%s" % (blueprint_path, component_name)):
        template.set_editor_property("replicates", bool(replicates))
    unreal.BlueprintEditorLibrary.compile_blueprint(blueprint)
    mcp_asset_lib().save_asset(blueprint_path)
    return {
        "blueprint": blueprint_path,
        "component": str(template.get_name()),
        "replicates": bool(template.get_editor_property("replicates")),
    }


def mcp_set_component_default(blueprint_path, component_name, property_name, value):
    """Scrive una proprietà sul *template* di un componente di un Blueprint.

    È la via per le proprietà `EditDefaultsOnly` che la Python API rifiuta di
    scrivere su un attore spawnato ("cannot be edited on instances") — il
    limite documentato nella fase 6 per `SensesConfig` di AIPerception.
    """
    blueprint = mcp_load_blueprint(blueprint_path)
    template = _mcp_bp_component_template(blueprint, component_name)
    with mcp_transaction("MCP: default di %s/%s" % (blueprint_path, component_name)):
        template.set_editor_property(property_name, mcp_coerce_value(value))
        letto = template.get_editor_property(property_name)
    unreal.BlueprintEditorLibrary.compile_blueprint(blueprint)
    mcp_asset_lib().save_asset(blueprint_path)
    if isinstance(letto, (bool, int, float, str)) or letto is None:
        valore_letto = letto
    else:
        valore_letto = str(letto)
    return {
        "blueprint": blueprint_path,
        "component": str(template.get_name()),
        "property": property_name,
        "value": valore_letto,
    }


#: Valori di EPlayNetMode (ByteProperty: Python legge/scrive interi).
MCP_NET_MODES = {"standalone": 0, "listen_server": 1, "client": 2}

MCP_PLAY_SETTINGS_PATH = "/Script/UnrealEd.Default__LevelEditorPlaySettings"


def mcp_configure_pie(num_players=1, net_mode="standalone", one_process=True):
    """Imposta il Play In Editor.

    `ULevelEditorPlaySettings` non è esposta nel modulo `unreal` di UE 5.8: si
    raggiunge il suo CDO con `load_object` e si usano i nomi PascalCase delle
    UPROPERTY (le varianti snake_case non vengono risolte).
    """
    mode = MCP_NET_MODES.get(str(net_mode).lower())
    if mode is None:
        raise ValueError("net_mode deve essere uno di: %s" % ", ".join(sorted(MCP_NET_MODES)))

    settings = unreal.load_object(None, MCP_PLAY_SETTINGS_PATH)
    if settings is None:
        raise RuntimeError(
            "LevelEditorPlaySettings non raggiungibile: configura il PIE da "
            "Edit > Editor Preferences > Level Editor > Play."
        )

    settings.set_editor_property("PlayNumberOfClients", int(num_players))
    settings.set_editor_property("RunUnderOneProcess", bool(one_process))

    # PlayNetMode è una ByteProperty su EPlayNetMode, enum non esposto al Python
    # di UE 5.8: non si può assegnare a runtime, si scrive nella config utente.
    mode_name = {0: "PIE_Standalone", 1: "PIE_ListenServer", 2: "PIE_Client"}[mode]
    note = None
    try:
        settings.set_editor_property("PlayNetMode", mode)
        applied_now = True
    except Exception:  # noqa: BLE001
        applied_now = False
        mcp_set_project_setting(
            "/Script/UnrealEd.LevelEditorPlaySettings",
            "PlayNetMode",
            mode_name,
            "EditorPerProjectUserSettings",
        )
        note = (
            "net_mode written to Config/DefaultEditorPerProjectUserSettings.ini: it "
            "takes effect on the next editor start. To use it right away, pick it from "
            "the dropdown next to the Play button (Net Mode: Play As Listen Server)."
        )

    return {
        "num_players": settings.get_editor_property("PlayNumberOfClients"),
        "net_mode": mode_name,
        "net_mode_active_now": applied_now,
        "one_process": settings.get_editor_property("RunUnderOneProcess"),
        "note": note,
    }


def mcp_start_pie():
    subsystem = mcp_level_subsystem()
    for method in ("editor_play_simulate", "editor_request_begin_play"):
        if hasattr(subsystem, method):
            getattr(subsystem, method)()
            return {"started": True, "api": method}
    if hasattr(unreal, "EditorLevelLibrary"):
        unreal.EditorLevelLibrary.editor_play_in_viewport(unreal.Vector(), unreal.Rotator())
        return {"started": True, "api": "EditorLevelLibrary.editor_play_in_viewport"}
    raise RuntimeError("Nessuna API di avvio PIE disponibile in questa versione di Unreal.")


def mcp_stop_pie():
    subsystem = mcp_level_subsystem()
    for method in ("editor_request_end_play", "editor_end_play"):
        if hasattr(subsystem, method):
            getattr(subsystem, method)()
            return {"stopped": True, "api": method}
    if hasattr(unreal, "EditorLevelLibrary"):
        unreal.EditorLevelLibrary.editor_end_play()
        return {"stopped": True, "api": "EditorLevelLibrary.editor_end_play"}
    raise RuntimeError("Nessuna API di stop PIE disponibile.")


def mcp_live_compile(attesa_massima=20.0):
    """Ricompila il C++ **senza chiudere l'editor**, tramite Live Coding.

    Live Coding applica patch ai binari già caricati, quindi funziona solo sulle
    modifiche al *corpo* delle funzioni. Aggiungere o togliere UCLASS, UFUNCTION,
    UPROPERTY cambia i dati di reflection e richiede comunque una compilazione
    completa a editor chiuso (ue_build_start).
    """
    import time as _time

    log_dir = mcp_full_path(unreal.Paths.project_log_dir())
    file_log = max((os.path.join(log_dir, f) for f in os.listdir(log_dir) if f.endswith(".log")),
                   key=os.path.getmtime)

    with open(file_log, encoding="utf-8", errors="replace") as handle:
        righe_prima = len(handle.read().splitlines())

    mondo = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    unreal.SystemLibrary.execute_console_command(mondo, "LiveCoding.Compile")

    esiti = ("succeeded", "failed", "no changes", "up to date", "error")
    scadenza = _time.time() + float(attesa_massima)
    nuove = []

    while _time.time() < scadenza:
        _time.sleep(1.0)
        with open(file_log, encoding="utf-8", errors="replace") as handle:
            righe = handle.read().splitlines()
        nuove = [r.split("]")[-1].strip() for r in righe[righe_prima:] if "LiveCoding" in r]
        if any(any(e in r.lower() for e in esiti) for r in nuove):
            break

    riuscito = any("succeeded" in r.lower() or "no changes" in r.lower() for r in nuove)
    fallito = any("failed" in r.lower() or "error" in r.lower() for r in nuove)

    return {
        "started": True,
        "succeeded": riuscito,
        "failed": fallito,
        "in_progress": not (riuscito or fallito),
        "log": nuove[-10:],
        "note": (
            "Live Coding patches existing function bodies. If you added or changed a "
            "UCLASS/UFUNCTION/UPROPERTY you still need ue_build_start with the editor "
            "closed."
        ),
    }


def mcp_set_project_setting(section, key, value, config="Game"):
    """Scrive in Config/Default<config>.ini. Alcune voci richiedono riavvio editor."""
    config_dir = mcp_full_path(unreal.Paths.project_config_dir())
    ini_path = os.path.join(config_dir, "Default%s.ini" % config)
    lines = []
    if os.path.exists(ini_path):
        with open(ini_path, encoding="utf-8-sig") as handle:
            lines = handle.read().splitlines()

    header = "[%s]" % section
    entry = "%s=%s" % (key, value)
    if header not in lines:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend([header, entry])
    else:
        start = lines.index(header)
        end = len(lines)
        for index in range(start + 1, len(lines)):
            if lines[index].startswith("["):
                end = index
                break
        replaced = False
        for index in range(start + 1, end):
            if lines[index].split("=")[0].strip() == key:
                lines[index] = entry
                replaced = True
                break
        if not replaced:
            lines.insert(end, entry)

    with open(ini_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return {"file": ini_path, "section": section, "key": key, "value": value}


# --------------------------------------------------------------------- audio


def mcp_create_metasound_source(package_path, name):
    full = "%s/%s" % (package_path.rstrip("/"), name)
    if mcp_asset_lib().does_asset_exist(full):
        return {"path": full, "created": False, "reason": "esiste già"}

    factory_class = None
    asset_class = None
    for candidate in ("MetaSoundSourceFactory", "MetasoundSourceFactory"):
        if hasattr(unreal, candidate):
            factory_class = getattr(unreal, candidate)
            break
    for candidate in ("MetaSoundSource", "MetasoundSource"):
        if hasattr(unreal, candidate):
            asset_class = getattr(unreal, candidate)
            break
    if factory_class is None or asset_class is None:
        raise RuntimeError(
            "MetaSounds non disponibile via Python. Abilita il plugin 'MetaSound' "
            "(Edit > Plugins > Audio) e riavvia l'editor."
        )

    asset = mcp_asset_tools().create_asset(name, package_path, asset_class, factory_class())
    if asset is None:
        raise RuntimeError("Creazione MetaSound '%s' fallita." % full)
    mcp_asset_lib().save_asset(full)
    return {"path": full, "created": True}


def mcp_create_sound_cue(package_path, name, wave_path=None):
    full = "%s/%s" % (package_path.rstrip("/"), name)
    if mcp_asset_lib().does_asset_exist(full):
        return {"path": full, "created": False, "reason": "esiste già"}

    factory = unreal.SoundCueFactoryNew()
    if wave_path:
        wave = mcp_asset_lib().load_asset(wave_path)
        if wave is None:
            raise ValueError("SoundWave '%s' non trovato." % wave_path)
        factory.set_editor_property("initial_sound_wave", wave)

    asset = mcp_asset_tools().create_asset(name, package_path, unreal.SoundCue, factory)
    if asset is None:
        raise RuntimeError("Creazione SoundCue '%s' fallita." % full)
    mcp_asset_lib().save_asset(full)
    return {"path": full, "created": True, "wave": wave_path}


# ------------------------------------------------------------------- utility


def mcp_project_status():
    subsystem = mcp_level_subsystem()
    try:
        current_level = str(subsystem.get_current_level().get_outer().get_name())
    except Exception:  # noqa: BLE001
        current_level = "sconosciuto"
    return {
        "engine_version": str(unreal.SystemLibrary.get_engine_version()),
        "project_file": mcp_full_path(unreal.Paths.get_project_file_path()),
        "project_content_dir": mcp_full_path(unreal.Paths.project_content_dir()),
        "current_level": current_level,
        "actor_count": len(mcp_actor_subsystem().get_all_level_actors()),
        "python_ok": True,
        "capabilities": mcp_capabilities(),
    }


def mcp_full_path(percorso):
    """Percorso assoluto e con i separatori del sistema.

    `unreal.Paths.*` restituisce percorsi **relativi alla cartella dei binari
    del motore**, non al progetto: `project_saved_dir()` viene fuori come
    `../../../../../../Users/.../Saved/`. Dentro l'editor funzionano lo stesso —
    il suo working directory è quello — ma qui vengono restituiti a un processo
    che sta altrove, e che quindi non ritrova più niente. Lo screenshot tornava
    con `captured: false` proprio per questo, pur essendo stato scritto.

    Va fatto anche il contrario dei separatori: `os.path.join` su Windows
    aggiunge backslash a una stringa che ne ha già di misti.
    """
    if not percorso:
        return percorso
    testo = str(percorso)
    try:
        testo = str(unreal.Paths.convert_relative_path_to_full(testo))
    except Exception:  # noqa: BLE001
        testo = os.path.abspath(testo)
    return os.path.normpath(testo)


def mcp_log_path():
    """Il file di log dell'editor attualmente in scrittura, o None.

    Non è sempre `<Progetto>.log`: quando gira una seconda istanza Unreal
    aggiunge un suffisso (`_2`), quindi si prende il più recente della cartella.
    """
    log_dir = mcp_full_path(unreal.Paths.project_log_dir())
    if not os.path.isdir(log_dir):
        return None
    candidates = [os.path.join(log_dir, f) for f in os.listdir(log_dir) if f.endswith(".log")]
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def mcp_tail_log(lines=80, only_errors=False):
    newest = mcp_log_path()
    if newest is None:
        return {"file": None, "lines": []}
    with open(newest, encoding="utf-8", errors="replace") as handle:
        content = handle.read().splitlines()
    if only_errors:
        content = [r for r in content if "Error" in r or "Warning" in r]
    # Le righe del log contengono le sentinelle delle chiamate MCP precedenti:
    # vanno neutralizzate, altrimenti confondono il parser della risposta.
    pulite = [r.replace("<<<MCP_JSON", "<<<mcp-json") for r in content[-int(lines) :]]
    return {"file": newest, "lines": pulite}


def mcp_dumps(value):
    return json.dumps(value, default=str)


# ============================================================== reflection
#
# Verificato dal vivo su UE 5.8 il 2026-07-31 (nessuna API qui è indovinata):
# la Python API di UE non espone un modo generico per elencare proprietà e
# funzioni di una classe arbitraria — `unreal.Class`/`unreal.Struct` offrono
# solo `get_editor_property(nome)`, che richiede di conoscere già il nome.
# `ClassIterator`/`StructIterator` invece funzionano, ma elencano le
# sottoclassi/sotto-struct di quella passata, non i suoi campi: è
# un'iterazione di gerarchia, non di membri. Per questo la reflection qui si
# ferma a "trova classi/struct per gerarchia" ed "elenca i valori di un
# enum nativo esposto ai binding Python": è quello che la API permette senza
# ricorrere a `ue_exec_python` a mano.
#
# Nota: NON riusa `mcp_resolve_class`/`mcp_resolve_struct` già definite più
# sopra per lo spawn — quelle restituiscono il *tipo* Python (serve a
# `spawn_actor_from_class`), qui invece serve l'oggetto `Class`/`ScriptStruct`
# di riflessione (`.static_class()`/`.static_struct()`), che è un'altra cosa:
# confonderli ha rotto lo spawn degli attori nella prima stesura di questa
# fase (`ClassIterator` con un `type` grezzo lancia
# "Cannot nativize 'Class' as 'Struct'"/comportamento errato). Nomi diversi
# apposta.


def mcp_reflect_resolve_class(name):
    """Risolve un nome di classe nell'oggetto `Class` di riflessione di Unreal.

    Prova prima l'attributo comodo (``unreal.Actor``), che copre le classi
    native e Blueprint con binding Python generato; poi ``find_object`` con
    un percorso completo (``/Script/Engine.Actor``, o
    ``/Game/.../BP_Nome.BP_Nome_C`` per una Blueprint del progetto). Un nome
    corto senza percorso (solo ``"Actor"``) NON si risolve con
    ``find_object``: serve il primo tentativo.
    """
    attr = getattr(unreal, name, None)
    if attr is not None and hasattr(attr, "static_class"):
        return attr.static_class()
    obj = unreal.find_object(None, name)
    if obj is None:
        raise ValueError("Classe '%s' non trovata." % name)
    return obj


def mcp_reflect_resolve_struct(name):
    """Come `mcp_reflect_resolve_class`, per gli struct (`ScriptStruct`)."""
    attr = getattr(unreal, name, None)
    if attr is not None and hasattr(attr, "static_struct"):
        return attr.static_struct()
    obj = unreal.find_object(None, name)
    if obj is None:
        raise ValueError("Struct '%s' non trovato." % name)
    return obj


def mcp_find_classes(parent, name_contains=None, limit=200):
    """Elenca le classi (native e Blueprint) derivate da `parent`, `parent` incluso.

    `ClassIterator` include il progetto: le Blueprint compaiono con il loro
    nome generato (`BP_PlayerCharacter_C`), oltre alla loro classe SKEL
    ombra.
    """
    base = mcp_reflect_resolve_class(parent)
    filtro = (name_contains or "").lower()
    trovate = []
    troncato = False
    for c in unreal.ClassIterator(base):
        nome = c.get_name()
        if filtro and filtro not in nome.lower():
            continue
        if len(trovate) >= limit:
            troncato = True
            break
        trovate.append({"name": nome, "path": c.get_path_name()})
    return {"parent": parent, "count": len(trovate), "truncated": troncato, "classes": trovate}


def mcp_find_structs(parent, name_contains=None, limit=200):
    """Elenca gli struct derivati da `parent` (`ScriptStruct`), `parent` incluso."""
    base = mcp_reflect_resolve_struct(parent)
    filtro = (name_contains or "").lower()
    trovate = []
    troncato = False
    for s in unreal.StructIterator(base):
        nome = s.get_name()
        if filtro and filtro not in nome.lower():
            continue
        if len(trovate) >= limit:
            troncato = True
            break
        trovate.append({"name": nome, "path": s.get_path_name()})
    return {"parent": parent, "count": len(trovate), "truncated": troncato, "structs": trovate}


def mcp_reflect_enum(enum_name):
    """Elenca i valori di un enum nativo esposto ai binding Python.

    Il nome esposto è quello UENUM senza il prefisso `E` (`ECollisionChannel`
    diventa `CollisionChannel`). Copre la quasi totalità degli enum nativi
    del motore. Non copre gli enum Blueprint (`UserDefinedEnum`, asset in
    `/Game/...`): quelli vanno caricati con `unreal.load_asset(path)` via
    `ue_exec_python`, perché non hanno questo binding.
    """
    enum_type = getattr(unreal, enum_name, None)
    if enum_type is None:
        raise ValueError(
            "Enum '%s' non trovato. Prova senza il prefisso 'E' (es. "
            "'CollisionChannel' non 'ECollisionChannel'); se è un enum "
            "Blueprint (asset in /Game/...) non è coperto da questo tool."
            % enum_name
        )
    try:
        membri = list(enum_type)
    except TypeError as exc:
        raise ValueError("'%s' non è un enum esposto ai binding Python." % enum_name) from exc
    valori = [
        {"name": m.name, "value": int(m.value), "display_name": str(m.get_display_name())}
        for m in membri
    ]
    return {"name": enum_name, "count": len(valori), "values": valori}


# ===================================================================== UMG
#
# Verificato dal vivo su UE 5.8 il 2026-07-31: creare l'asset Widget
# Blueprint funziona, `parent_class` accetta anche una classe C++ del
# progetto (non solo `UserWidget`/`EditorUtilityWidget`). Popolare il
# `WidgetTree` (aggiungere TextBlock/Button/CanvasPanel, il layout) NO:
# `get_editor_property("WidgetTree")` sulla CDO della classe generata
# risponde "protected and cannot be read" — stesso muro dei grafi Blueprint
# già documentato per `ue_reparent_blueprint`. Il layout va disegnato a mano
# nel Widget Designer; per la logica, la stessa via C++ già in uso altrove
# (`ue_cpp_class_create` con proprietà `BindWidget` -> reparent) funziona
# perché quelle proprietà sono lette dal compilatore Blueprint al momento
# della compilazione, non scritte da qui a runtime.


def mcp_create_widget_blueprint(package_path, name, parent_class="UserWidget", editor_utility=False):
    full = "%s/%s" % (package_path.rstrip("/"), name)
    if mcp_asset_lib().does_asset_exist(full):
        return {"path": full, "created": False, "reason": "esiste già"}

    resolved_parent = mcp_resolve_class(parent_class)
    if editor_utility:
        factory = unreal.EditorUtilityWidgetBlueprintFactory()
        asset_class = unreal.EditorUtilityWidgetBlueprint
    else:
        factory = unreal.WidgetBlueprintFactory()
        asset_class = unreal.WidgetBlueprint
    factory.set_editor_property("parent_class", resolved_parent)

    asset = mcp_asset_tools().create_asset(name, package_path, asset_class, factory)
    if asset is None:
        raise RuntimeError("Creazione Widget Blueprint '%s' fallita." % full)
    mcp_asset_lib().save_asset(full)
    return {
        "path": full,
        "created": True,
        "parent_class": parent_class,
        "editor_utility": bool(editor_utility),
    }


# ============================================================ blueprint graph
#
# Verificato dal vivo su UE 5.8 il 2026-07-31. Le istruzioni del server
# dicono "i grafi Blueprint non sono scriptabili" — vero solo in parte:
# `unreal.BlueprintEditorLibrary` espone un sottoinsieme reale di editing
# (aggiungere nodi evento per eventi ereditati overridabili, aggiungere grafi
# funzione vuoti, leggere i pin di un nodo di cui si ha già il riferimento),
# ma la proprietà `Nodes` di `EdGraph` è protetta esattamente come
# `WidgetTree` in UMG (fase 2): niente elenco dei nodi di un grafo qualunque,
# niente creazione di nodi arbitrari (Print String, Branch, chiamate a
# funzione libere, nodi matematici...). C'è anche un modo per collegare due
# pin (`BlueprintGraphPin.try_create_connection`, verificato funzionante),
# ma non è esposto come tool: gli unici nodi raggiungibili da qui sono nodi
# evento, e un nodo evento ha *solo* pin di output (verificato anche su un
# evento con 8 parametri, ReceiveHit) — senza un nodo con un pin di input non
# c'è nessuna connessione valida da fare, quindi sarebbe stato un tool
# funzionante ma inutile nella pratica. Per logica vera resta la via C++ già
# in uso altrove (`ue_cpp_class_create` -> `ue_reparent_blueprint`); questi
# tool servono a predisporre l'aggancio (l'evento esiste, il suo grafo pure),
# non a scrivere la logica.


def mcp_bp_list_graphs(blueprint_path):
    blueprint = mcp_load_blueprint(blueprint_path)
    nomi = [g.get_name() for g in unreal.BlueprintEditorLibrary.list_graphs(blueprint)]
    return {"path": blueprint_path, "graphs": nomi}


def mcp_bp_list_events(blueprint_path):
    """Elenca gli eventi (custom, ereditati overridabili, di interfaccia) visibili sul Blueprint.

    `is_implemented` dice se esiste già un nodo per quell'evento nel grafo:
    utile prima di chiamare `mcp_bp_add_event_override`, che altrimenti
    restituisce comunque il nodo esistente senza duplicarlo.
    """
    blueprint = mcp_load_blueprint(blueprint_path)
    eventi = [
        {
            "name": str(e.get_editor_property("name")),
            "is_implemented": bool(e.get_editor_property("is_implemented")),
        }
        for e in unreal.BlueprintEditorLibrary.list_events(blueprint)
    ]
    return {"path": blueprint_path, "events": eventi}


def _mcp_bp_pin_dict(pin, node_path):
    return {
        "node_path": node_path,
        "name": str(pin.get_pin_name()),
        "direction": pin.get_pin_direction().name,
        "type": str(pin.get_pin_type_display_string()),
    }


def mcp_bp_add_event_override(blueprint_path, event_name, x=0, y=0):
    """Aggiunge (o ritrova, se già presente) il nodo di un evento ereditato overridabile.

    Restituisce il path del nodo e i suoi pin, a scopo informativo (un nodo
    evento ha solo pin di output: non c'è altro da collegarci con quello che
    la Python API di UE permette — vedi il commento in cima a questa
    sezione).
    """
    blueprint = mcp_load_blueprint(blueprint_path)
    library = unreal.BlueprintEditorLibrary
    node = library.add_event_override(blueprint, event_name, unreal.IntPoint(int(x), int(y)))
    if node is None:
        raise ValueError(
            "'%s' non è un evento ereditato overridabile su questo Blueprint, "
            "o il Blueprint non ha un grafo evento. Controlla con mcp_bp_list_events."
            % event_name
        )
    node_path = node.get_path_name()
    pins = [_mcp_bp_pin_dict(p, node_path) for p in library.list_all_pins(node)]
    mcp_asset_lib().save_asset(blueprint_path)
    return {"node_path": node_path, "pins": pins}


def mcp_bp_add_function_graph(blueprint_path, func_name):
    """Crea un grafo funzione vuoto con i nodi Entry/Return di default.

    Quei nodi non sono raggiungibili da qui (stesso limite di `Nodes`
    protetta): il corpo della funzione va scritto a mano nel Blueprint
    Editor, o la funzione lasciata vuota se serve solo come slot da
    riempire in seguito.
    """
    blueprint = mcp_load_blueprint(blueprint_path)
    graph = unreal.BlueprintEditorLibrary.add_function_graph(blueprint, func_name)
    if graph is None:
        raise ValueError("Impossibile creare la funzione '%s'." % func_name)
    mcp_asset_lib().save_asset(blueprint_path)
    return {"graph_name": graph.get_name()}


# =================================================================== animazione
#
# Verificato dal vivo su UE 5.8 il 2026-07-31, su asset reali del progetto
# (Remy_Skeleton, BS_Remy_Locomozione, Idle/Walking/Running). A differenza
# dei grafi Blueprint/UMG (fasi 2-3), qui la scrittura funziona davvero: le
# proprietà di BlendSpace (`BlendParameters`, `SampleData`) sono array di
# struct ordinari, non protetti — creare un nuovo asset, riempirlo di
# parametri e sample, salvare e ricaricarlo da zero mostra i dati persistiti.
# L'AnimGraph di un Anim Blueprint resta invece un EdGraph come gli altri:
# stesso muro di `ue_bp_add_event_override`, non trattato qui — questo modulo
# crea solo l'asset (come `ue_create_widget_blueprint` per UMG).


def mcp_skeleton_info(skeleton_path):
    skeleton = mcp_asset_lib().load_asset(skeleton_path)
    if skeleton is None:
        raise ValueError("Skeleton '%s' non trovato." % skeleton_path)
    pose = skeleton.get_reference_pose()
    return {
        "path": skeleton_path,
        "bones": [str(b) for b in pose.get_bone_names()],
        "sockets": [str(s) for s in pose.get_socket_names()],
    }


def mcp_anim_sequence_info(anim_path):
    seq = mcp_asset_lib().load_asset(anim_path)
    if seq is None:
        raise ValueError("Animazione '%s' non trovata." % anim_path)
    library = unreal.AnimationLibrary
    return {
        "path": anim_path,
        "length_seconds": library.get_sequence_length(seq),
        "num_frames": library.get_num_frames(seq),
        "notify_track_names": [str(n) for n in library.get_animation_notify_track_names(seq)],
        "notify_event_names": [str(n) for n in library.get_animation_notify_event_names(seq)],
        "sync_marker_names": [str(n) for n in library.get_unique_marker_names(seq)],
        "curve_names": [
            str(n) for n in library.get_animation_curve_names(seq, unreal.RawCurveTrackTypes.RCT_FLOAT)
        ],
    }


def mcp_create_blend_space_1d(
    package_path, name, skeleton_path, axis_name="Speed", axis_min=0.0, axis_max=1.0,
    grid_num=4, samples=None,
):
    """Crea un BlendSpace1D con un asse e, opzionalmente, i suoi sample.

    `samples` è una lista di `{"value": float, "animation": path}`. Solo 1D
    per ora: BlendSpace (2D) usa la stessa struttura dati ma non è stata
    verificata dal vivo in questa fase.
    """
    full = "%s/%s" % (package_path.rstrip("/"), name)
    if mcp_asset_lib().does_asset_exist(full):
        return {"path": full, "created": False, "reason": "esiste già"}

    skeleton = mcp_asset_lib().load_asset(skeleton_path)
    if skeleton is None:
        raise ValueError("Skeleton '%s' non trovato." % skeleton_path)

    factory = unreal.BlendSpaceFactoryNew()
    factory.set_editor_property("target_skeleton", skeleton)
    asset = mcp_asset_tools().create_asset(name, package_path, unreal.BlendSpace1D, factory)
    if asset is None:
        raise RuntimeError("Creazione BlendSpace '%s' fallita." % full)

    parametri = asset.get_editor_property("BlendParameters")
    parametro = parametri[0]
    parametro.set_editor_property("DisplayName", axis_name)
    parametro.set_editor_property("Min", float(axis_min))
    parametro.set_editor_property("Max", float(axis_max))
    parametro.set_editor_property("GridNum", int(grid_num))
    parametri[0] = parametro
    asset.set_editor_property("BlendParameters", parametri)

    aggiunti = []
    for voce in samples or []:
        anim = mcp_asset_lib().load_asset(voce["animation"])
        if anim is None:
            raise ValueError("Animazione '%s' non trovata." % voce["animation"])
        campione = unreal.BlendSample()
        campione.set_editor_property("Animation", anim)
        campione.set_editor_property("SampleValue", unreal.Vector(float(voce["value"]), 0.0, 0.0))
        aggiunti.append(campione)
    if aggiunti:
        asset.set_editor_property("SampleData", aggiunti)

    mcp_asset_lib().save_asset(full)
    return {
        "path": full,
        "created": True,
        "axis": {
            "name": axis_name,
            "min": float(axis_min),
            "max": float(axis_max),
            "grid_num": int(grid_num),
        },
        "samples": len(aggiunti),
    }


def mcp_create_anim_montage(package_path, name, source_animation_path):
    full = "%s/%s" % (package_path.rstrip("/"), name)
    if mcp_asset_lib().does_asset_exist(full):
        return {"path": full, "created": False, "reason": "esiste già"}

    source = mcp_asset_lib().load_asset(source_animation_path)
    if source is None:
        raise ValueError("Animazione '%s' non trovata." % source_animation_path)

    factory = unreal.AnimMontageFactory()
    factory.set_editor_property("source_animation", source)
    factory.set_editor_property("target_skeleton", source.get_editor_property("skeleton"))
    asset = mcp_asset_tools().create_asset(name, package_path, unreal.AnimMontage, factory)
    if asset is None:
        raise RuntimeError("Creazione AnimMontage '%s' fallita." % full)
    mcp_asset_lib().save_asset(full)

    library = unreal.AnimationLibrary
    return {
        "path": full,
        "created": True,
        "source_animation": source_animation_path,
        "slot_names": [str(n) for n in library.get_montage_slot_names(asset)],
    }


def mcp_create_anim_blueprint(package_path, name, skeleton_path, parent_class="AnimInstance"):
    """Crea l'asset Anim Blueprint. Il grafo (AnimGraph) non è raggiungibile
    da qui: stesso limite del Blueprint graph, vedi il commento in cima a
    questa sezione."""
    full = "%s/%s" % (package_path.rstrip("/"), name)
    if mcp_asset_lib().does_asset_exist(full):
        return {"path": full, "created": False, "reason": "esiste già"}

    skeleton = mcp_asset_lib().load_asset(skeleton_path)
    if skeleton is None:
        raise ValueError("Skeleton '%s' non trovato." % skeleton_path)
    resolved_parent = mcp_resolve_class(parent_class)

    factory = unreal.AnimBlueprintFactory()
    factory.set_editor_property("target_skeleton", skeleton)
    factory.set_editor_property("parent_class", resolved_parent)
    asset = mcp_asset_tools().create_asset(name, package_path, unreal.AnimBlueprint, factory)
    if asset is None:
        raise RuntimeError("Creazione Anim Blueprint '%s' fallita." % full)
    mcp_asset_lib().save_asset(full)
    return {"path": full, "created": True, "skeleton": skeleton_path, "parent_class": parent_class}


# ======================================================================= niagara
#
# Verificato dal vivo su UE 5.8 il 2026-07-31, anche su template popolati
# della libreria di sistema (/Niagara/DefaultAssets/Templates/Systems/...).
# `EmitterHandles` di `NiagaraSystem` è protetta esattamente come `Nodes` di
# `EdGraph` e `WidgetTree`: niente aggiunta di emitter o moduli via Python.
# `NiagaraFunctionLibrary.get_all_emitters`/`get_all_user_parameters` invece
# funzionano davvero e sono a livello di ASSET, non di componente a runtime:
# leggono un NiagaraSystem senza bisogno del PIE in esecuzione. Quindi:
# creazione dell'asset vuoto sì, introspezione di un sistema esistente sì,
# authoring dell'emitter stack no — stessa forma delle fasi 2 e 4.


def mcp_create_niagara_system(package_path, name):
    full = "%s/%s" % (package_path.rstrip("/"), name)
    if mcp_asset_lib().does_asset_exist(full):
        return {"path": full, "created": False, "reason": "esiste già"}

    factory = unreal.NiagaraSystemFactoryNew()
    asset = mcp_asset_tools().create_asset(name, package_path, unreal.NiagaraSystem, factory)
    if asset is None:
        raise RuntimeError("Creazione Niagara System '%s' fallita." % full)
    mcp_asset_lib().save_asset(full)
    return {"path": full, "created": True}


def mcp_niagara_system_info(system_path):
    sistema = mcp_asset_lib().load_asset(system_path)
    if sistema is None:
        raise ValueError("Niagara System '%s' non trovato." % system_path)

    library = unreal.NiagaraFunctionLibrary
    emitter = [
        {
            "name": str(e.emitter_name),
            "enabled": bool(e.is_enabled),
            "lightweight": bool(e.is_lightweight),
        }
        for e in library.get_all_emitters(sistema)
    ]
    parametri = [
        {"name": str(p.parameter_name), "type": str(p.type_name)}
        for p in library.get_all_user_parameters(sistema)
    ]
    return {"path": system_path, "emitters": emitter, "user_parameters": parametri}


# ======================================================================= gameplay
#
# Verificato dal vivo su UE 5.8 il 2026-07-31. Fisica/collisione: le funzioni
# `set_simulate_physics`/`set_collision_enabled`/`set_collision_profile_name`
# di `UPrimitiveComponent` sono UFUNCTION vere, non proprietà dirette (ecco
# perché `SimulatePhysics` come `get/set_editor_property` fallisce con
# "Failed to find property" — bisogna passare dai metodi). Il NavMesh è del
# tutto scriptabile: piazzare un `NavMeshBoundsVolume` è già coperto da
# `ue_spawn_actor` (nessun tool dedicato), un `RebuildNavigation` via console
# command genera il navmesh, e `NavigationSystemV1` risponde a query di punti
# raggiungibili e pathfinding sincrono senza bisogno del PIE.
#
# Blackboard e Behavior Tree rompono ulteriormente il pattern "i sistemi a
# grafo sono protetti" scoperto nelle fasi 2/3/5: qui SONO scrivibili,
# `RootNode`/`Children`/`Decorators`/`Services` compresi. La differenza è che
# BT/Blackboard non hanno un vero editor a nodi K2-style sotto il cofano — i
# nodi sono normali `UObject` referenziati da proprietà o array di struct
# (`FBTCompositeChild`), non un `EdGraph`. EQS invece resta bloccato come
# Blueprint/UMG/Niagara: `Options` di `EnvQuery` è protetta.
#
# Nota tecnica sugli struct: `Children` di un nodo composite è un
# `TArray<FBTCompositeChild>` — struct per valore. Leggerlo con
# `get_editor_property` restituisce COPIE: modificare un elemento (es.
# aggiungere un decorator) richiede sempre riscrivere l'intero array sul nodo
# genitore con `set_editor_property("Children", ...)`. I nodi stessi
# (composite/task/decorator/service) sono `UObject` referenziati per
# puntatore: modificarli direttamente persiste senza bisogno di riscrivere
# nulla a monte, qualunque sia la profondità nell'albero.
#
# Le proprietà "bindable da blackboard" sui task (es. `BTTask_Wait.WaitTime`)
# sono struct `FValueOrBBKey_*` con due campi: `key` (nome chiave blackboard,
# se vuota si usa il valore fisso) e `default_value` (il valore fisso).
# `mcp_bt_set_node_property` lo gestisce in automatico.
#
# AI Perception: il componente si aggiunge già con `ue_add_component`
# generico (nessun tool dedicato). `SensesConfig` invece è `EditDefaultsOnly`
# e la Python API rifiuta di scriverla su un'istanza spawnata ("cannot be
# edited on instances"); scriverla sul component template del Blueprint
# richiede un accesso all'oggetto template che non si è trovato in modo
# affidabile (il subsystem dei subobject non espone un modo diretto per
# risalire dall'handle all'oggetto, e il nome della proprietà sulla CDO non
# corrisponde al nome dato al componente). Va configurato a mano nel pannello
# Details del Blueprint, stessa categoria di limite di UMG/Blueprint graph.


def mcp_component_physics_info(actor_label, component):
    actor = mcp_require_actor(actor_label)
    comp = mcp_find_component(actor, component)
    return {
        "actor": actor_label,
        "component": component,
        "simulate_physics": bool(comp.is_simulating_physics()),
        "collision_enabled": str(comp.get_collision_enabled()),
        "collision_profile": str(comp.get_collision_profile_name()),
    }


def _mcp_collision_enum(nome):
    chiave = re.sub(r"(?<!^)(?=[A-Z])", "_", str(nome)).upper().replace("-", "_").replace(" ", "_")
    chiave = re.sub(r"_+", "_", chiave)
    if not hasattr(unreal.CollisionEnabled, chiave):
        disponibili = sorted(m for m in dir(unreal.CollisionEnabled) if m.isupper())
        raise ValueError(
            "CollisionEnabled '%s' sconosciuto. Validi: %s" % (nome, ", ".join(disponibili))
        )
    return getattr(unreal.CollisionEnabled, chiave)


def mcp_set_component_physics(
    actor_label, component, simulate_physics=None, collision_enabled=None, collision_profile=None
):
    actor = mcp_require_actor(actor_label)
    comp = mcp_find_component(actor, component)
    applicato = {}
    with mcp_transaction("MCP: fisica di %s/%s" % (actor_label, component)):
        if simulate_physics is not None:
            comp.set_simulate_physics(bool(simulate_physics))
            applicato["simulate_physics"] = bool(simulate_physics)
        if collision_profile is not None:
            comp.set_collision_profile_name(collision_profile)
            applicato["collision_profile"] = collision_profile
        if collision_enabled is not None:
            comp.set_collision_enabled(_mcp_collision_enum(collision_enabled))
            applicato["collision_enabled"] = collision_enabled
    return {
        "actor": actor_label,
        "component": component,
        "applied": applicato,
        "info": mcp_component_physics_info(actor_label, component),
    }


# -------------------------------------------------------------- navmesh


def _mcp_nav_system():
    subsys = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
    world = subsys.get_editor_world()
    return world, unreal.NavigationSystemV1.get_navigation_system(world)


def mcp_nav_rebuild():
    """Rigenera il navmesh (equivalente al comando console `RebuildNavigation`).

    Serve almeno un `NavMeshBoundsVolume` nel livello: piazzalo con
    `ue_spawn_actor` (classe `NavMeshBoundsVolume`) prima di chiamare questo.
    """
    world, navsys = _mcp_nav_system()
    unreal.SystemLibrary.execute_console_command(world, "RebuildNavigation")
    return {"triggered": True, "is_building": bool(navsys.is_navigation_being_built(world))}


def mcp_nav_query_point(origin, radius=500.0):
    world, navsys = _mcp_nav_system()
    punto = mcp_to_vector(origin)
    reachable = navsys.get_random_reachable_point_in_radius(world, punto, float(radius))
    return {
        "origin": mcp_vec(punto),
        "radius": float(radius),
        "random_reachable_point": mcp_vec(reachable) if reachable else None,
    }


def mcp_nav_find_path(start, end):
    world, navsys = _mcp_nav_system()
    a = mcp_to_vector(start)
    b = mcp_to_vector(end)
    path = navsys.find_path_to_location_synchronously(world, a, b)
    if path is None:
        return {"found": False, "start": mcp_vec(a), "end": mcp_vec(b)}
    return {
        "found": True,
        "start": mcp_vec(a),
        "end": mcp_vec(b),
        "is_valid": bool(path.is_valid()),
        "is_partial": bool(path.is_partial()),
        "path_points": [mcp_vec(p) for p in path.path_points],
    }


# -------------------------------------------------------------- blackboard


_MCP_BLACKBOARD_KEY_TYPES = {
    "object": "BlackboardKeyType_Object",
    "class": "BlackboardKeyType_Class",
    "bool": "BlackboardKeyType_Bool",
    "int": "BlackboardKeyType_Int",
    "float": "BlackboardKeyType_Float",
    "string": "BlackboardKeyType_String",
    "name": "BlackboardKeyType_Name",
    "vector": "BlackboardKeyType_Vector",
    "rotator": "BlackboardKeyType_Rotator",
    "enum": "BlackboardKeyType_Enum",
}


def mcp_create_blackboard(package_path, name):
    full = "%s/%s" % (package_path.rstrip("/"), name)
    if mcp_asset_lib().does_asset_exist(full):
        return {"path": full, "created": False, "reason": "esiste già"}
    factory = unreal.BlackboardDataFactory()
    asset = mcp_asset_tools().create_asset(name, package_path, unreal.BlackboardData, factory)
    if asset is None:
        raise RuntimeError("Creazione Blackboard '%s' fallita." % full)
    mcp_asset_lib().save_asset(full)
    return {"path": full, "created": True}


def mcp_blackboard_add_key(blackboard_path, key_name, key_type="object"):
    bb = mcp_asset_lib().load_asset(blackboard_path)
    if bb is None:
        raise ValueError("Blackboard '%s' non trovato." % blackboard_path)
    nome_classe = _MCP_BLACKBOARD_KEY_TYPES.get(str(key_type).lower())
    if nome_classe is None:
        raise ValueError(
            "Tipo chiave '%s' sconosciuto. Validi: %s"
            % (key_type, ", ".join(sorted(_MCP_BLACKBOARD_KEY_TYPES)))
        )
    cls = mcp_resolve_class(nome_classe)
    key_type_obj = unreal.new_object(cls, outer=bb)
    entry = unreal.BlackboardEntry()
    entry.set_editor_property("EntryName", key_name)
    entry.set_editor_property("KeyType", key_type_obj)

    keys = list(bb.get_editor_property("Keys"))
    keys.append(entry)
    bb.set_editor_property("Keys", keys)
    mcp_asset_lib().save_asset(blackboard_path)
    return mcp_blackboard_info(blackboard_path)


def mcp_blackboard_info(blackboard_path):
    bb = mcp_asset_lib().load_asset(blackboard_path)
    if bb is None:
        raise ValueError("Blackboard '%s' non trovato." % blackboard_path)
    chiavi = [
        {
            "name": str(k.get_editor_property("EntryName")),
            "type": str(k.get_editor_property("KeyType").get_class().get_name()),
        }
        for k in bb.get_editor_property("Keys")
    ]
    return {"path": blackboard_path, "keys": chiavi}


# -------------------------------------------------------------- behavior tree


def _mcp_bt_resolve_node(bt, path):
    """Risolve un path tipo 'root', '0', '0.1' in un nodo (composite o task).

    Ogni pezzo del path (separato da '.') è l'indice del figlio nell'array
    `Children` del nodo composite corrente, a partire dal `RootNode`.
    """
    root = bt.get_editor_property("RootNode")
    if root is None:
        raise ValueError("Il Behavior Tree non ha ancora un RootNode (usa mcp_create_behavior_tree).")
    if path in (None, "", "root"):
        return root
    nodo = root
    for pezzo in str(path).split("."):
        idx = int(pezzo)
        figli = nodo.get_editor_property("Children")
        if idx >= len(figli):
            raise ValueError(
                "Path '%s': indice %d fuori range (figli disponibili: %d)." % (path, idx, len(figli))
            )
        link = figli[idx]
        figlio = link.get_editor_property("ChildComposite") or link.get_editor_property("ChildTask")
        if figlio is None:
            raise ValueError("Path '%s': il ramo %d è vuoto." % (path, idx))
        nodo = figlio
    return nodo


def _mcp_bt_parent_and_index(bt, path):
    """Risolve il nodo composite genitore e l'indice del figlio indicato
    dall'ultimo pezzo del path. Serve per le operazioni sul child link
    (decorator, inserimento figli): il path non può essere 'root', perché la
    radice non ha un child link proprio (non è figlia di nessuno)."""
    if path in (None, "", "root"):
        raise ValueError("Serve un path a un nodo figlio (es. '0', '0.1'), non 'root'.")
    pezzi = str(path).split(".")
    parent_path = ".".join(pezzi[:-1])
    parent = _mcp_bt_resolve_node(bt, parent_path)
    idx = int(pezzi[-1])
    figli = parent.get_editor_property("Children")
    if idx >= len(figli):
        raise ValueError("Path '%s': indice %d fuori range (figli disponibili: %d)." % (path, idx, len(figli)))
    return parent, idx


def mcp_create_behavior_tree(package_path, name, blackboard_path=None, root_composite="BTComposite_Selector"):
    full = "%s/%s" % (package_path.rstrip("/"), name)
    if mcp_asset_lib().does_asset_exist(full):
        return {"path": full, "created": False, "reason": "esiste già"}

    factory = unreal.BehaviorTreeFactory()
    bt = mcp_asset_tools().create_asset(name, package_path, unreal.BehaviorTree, factory)
    if bt is None:
        raise RuntimeError("Creazione Behavior Tree '%s' fallita." % full)

    if blackboard_path:
        bb = mcp_asset_lib().load_asset(blackboard_path)
        if bb is None:
            raise ValueError("Blackboard '%s' non trovato." % blackboard_path)
        bt.set_editor_property("BlackboardAsset", bb)

    cls = mcp_resolve_class(root_composite)
    root = unreal.new_object(cls, outer=bt)
    bt.set_editor_property("RootNode", root)

    mcp_asset_lib().save_asset(full)
    return {"path": full, "created": True, "blackboard": blackboard_path, "root_composite": root_composite}


def mcp_bt_add_node(bt_path, parent_path, node_class, index=None):
    """Aggiunge un nodo (composite o task, dedotto dalla classe base) come
    figlio del composite trovato a `parent_path` ('root' per la radice).
    Restituisce il path del nodo appena creato, da riusare nelle chiamate
    successive (decorator, service, set_node_property, figli annidati)."""
    bt = mcp_asset_lib().load_asset(bt_path)
    if bt is None:
        raise ValueError("Behavior Tree '%s' non trovato." % bt_path)

    genitore = _mcp_bt_resolve_node(bt, parent_path)
    if not isinstance(genitore, unreal.BTCompositeNode):
        raise ValueError("Path '%s' non è un nodo composite: non può avere figli." % parent_path)

    cls = mcp_resolve_class(node_class)
    nuovo_nodo = unreal.new_object(cls, outer=bt)

    link = unreal.BTCompositeChild()
    if isinstance(nuovo_nodo, unreal.BTCompositeNode):
        link.set_editor_property("ChildComposite", nuovo_nodo)
    else:
        link.set_editor_property("ChildTask", nuovo_nodo)

    figli = list(genitore.get_editor_property("Children"))
    if index is None or int(index) >= len(figli):
        figli.append(link)
        nuovo_indice = len(figli) - 1
    else:
        nuovo_indice = int(index)
        figli.insert(nuovo_indice, link)
    genitore.set_editor_property("Children", figli)
    mcp_asset_lib().save_asset(bt_path)

    prefisso = "" if parent_path in (None, "", "root") else str(parent_path)
    nuovo_path = ("%s.%d" % (prefisso, nuovo_indice)) if prefisso else str(nuovo_indice)
    return {"path": bt_path, "node_path": nuovo_path, "node_class": node_class}


def mcp_bt_add_decorator(bt_path, node_path, decorator_class):
    bt = mcp_asset_lib().load_asset(bt_path)
    if bt is None:
        raise ValueError("Behavior Tree '%s' non trovato." % bt_path)
    genitore, idx = _mcp_bt_parent_and_index(bt, node_path)
    cls = mcp_resolve_class(decorator_class)
    dec = unreal.new_object(cls, outer=bt)

    figli = list(genitore.get_editor_property("Children"))
    link = figli[idx]
    decoratori = list(link.get_editor_property("Decorators"))
    decoratori.append(dec)
    link.set_editor_property("Decorators", decoratori)
    figli[idx] = link
    genitore.set_editor_property("Children", figli)
    mcp_asset_lib().save_asset(bt_path)
    return {
        "path": bt_path,
        "node_path": node_path,
        "decorator_class": decorator_class,
        "decorator_count": len(decoratori),
    }


def mcp_bt_add_service(bt_path, node_path, service_class):
    """I service vanno solo su nodi composite (Selector/Sequence): sui task
    la proprietà `Services` è protetta (verificato dal vivo)."""
    bt = mcp_asset_lib().load_asset(bt_path)
    if bt is None:
        raise ValueError("Behavior Tree '%s' non trovato." % bt_path)
    nodo = _mcp_bt_resolve_node(bt, node_path)
    if not isinstance(nodo, unreal.BTCompositeNode):
        raise ValueError(
            "Path '%s' non è un nodo composite: i service vanno solo su Selector/Sequence." % node_path
        )
    cls = mcp_resolve_class(service_class)
    svc = unreal.new_object(cls, outer=bt)

    servizi = list(nodo.get_editor_property("Services"))
    servizi.append(svc)
    nodo.set_editor_property("Services", servizi)
    mcp_asset_lib().save_asset(bt_path)
    return {"path": bt_path, "node_path": node_path, "service_class": service_class, "service_count": len(servizi)}


def mcp_bt_set_node_property(bt_path, node_path, property_name, value):
    """Imposta una proprietà su un nodo (composite o task) trovato per path.

    Gestisce in automatico i campi bindable da blackboard (struct
    `FValueOrBBKey_*`, es. `BTTask_Wait.WaitTime`): se la proprietà corrente è
    uno di questi struct, scrive in `default_value` invece di sostituire
    l'intero struct.
    """
    bt = mcp_asset_lib().load_asset(bt_path)
    if bt is None:
        raise ValueError("Behavior Tree '%s' non trovato." % bt_path)
    nodo = _mcp_bt_resolve_node(bt, node_path)

    valore_attuale = nodo.get_editor_property(property_name)
    nome_tipo = type(valore_attuale).__name__
    if hasattr(valore_attuale, "set_editor_property"):
        if nome_tipo.startswith("ValueOrBBKey_"):
            valore_attuale.set_editor_property("default_value", mcp_coerce_value(value))
            nodo.set_editor_property(property_name, valore_attuale)
            mcp_asset_lib().save_asset(bt_path)
            return {
                "path": bt_path,
                "node_path": node_path,
                "property": property_name,
                "applied": value,
                "via": "default_value",
            }

    nodo.set_editor_property(property_name, mcp_coerce_value(value))
    mcp_asset_lib().save_asset(bt_path)
    return {"path": bt_path, "node_path": node_path, "property": property_name, "applied": value}


def mcp_bt_info(bt_path):
    bt = mcp_asset_lib().load_asset(bt_path)
    if bt is None:
        raise ValueError("Behavior Tree '%s' non trovato." % bt_path)

    def _dump(nodo, path):
        if nodo is None:
            return None
        info = {"path": path, "class": str(nodo.get_class().get_name())}
        if isinstance(nodo, unreal.BTCompositeNode):
            try:
                info["services"] = [str(s.get_class().get_name()) for s in nodo.get_editor_property("Services")]
            except Exception:  # noqa: BLE001
                info["services"] = []
            figli = []
            for i, link in enumerate(nodo.get_editor_property("Children")):
                sotto = link.get_editor_property("ChildComposite") or link.get_editor_property("ChildTask")
                sotto_path = "%s.%d" % (path, i) if path else str(i)
                nodo_info = _dump(sotto, sotto_path)
                if nodo_info is not None:
                    try:
                        nodo_info["decorators"] = [
                            str(d.get_class().get_name()) for d in link.get_editor_property("Decorators")
                        ]
                    except Exception:  # noqa: BLE001
                        nodo_info["decorators"] = []
                figli.append(nodo_info)
            info["children"] = figli
        return info

    root = bt.get_editor_property("RootNode")
    blackboard = bt.get_editor_property("BlackboardAsset")
    return {
        "path": bt_path,
        "blackboard": str(blackboard.get_path_name()) if blackboard else None,
        "root": _dump(root, ""),
    }


# -------------------------------------------------------------- EQS


def mcp_create_eqs_asset(package_path, name):
    """Crea solo l'asset EQS vuoto. `Options` di `EnvQuery` è protetta nella
    Python API (stesso muro di WidgetTree/EdGraph/EmitterHandles, verificato
    dal vivo) — nessuna query configurabile via script, solo authoring a mano
    nell'editor EQS."""
    full = "%s/%s" % (package_path.rstrip("/"), name)
    if mcp_asset_lib().does_asset_exist(full):
        return {"path": full, "created": False, "reason": "esiste già"}
    factory = unreal.EnvironmentQueryFactory()
    asset = mcp_asset_tools().create_asset(name, package_path, unreal.EnvQuery, factory)
    if asset is None:
        raise RuntimeError("Creazione EQS '%s' fallita." % full)
    mcp_asset_lib().save_asset(full)
    return {"path": full, "created": True}


# ======================================================================= GAS
#
# Investigato dal vivo su UE 5.8 il 31/07/2026. Il plugin GameplayAbilities
# non era abilitato di default sul progetto: va abilitato con
# `ue_project_set_plugins` e l'editor va riavviato prima che queste classi
# esistano in Python.
#
# GameplayEffect e AttributeSet sono Blueprintable "normali": si creano già
# con `ue_create_blueprint` generico (`parent_class="GameplayEffect"` o
# `"AttributeSet"`), nessun tool dedicato serve per quello. GameplayAbility
# invece ha un asset dedicato (`GameplayAbilityBlueprint`, non `Blueprint`
# semplice) che richiede `GameplayAbilitiesBlueprintFactory` — da qui
# `mcp_create_gameplay_ability`. Aggiungere un attributo (`GameplayAttributeData`)
# a un AttributeSet funziona già con `ue_add_variable` passando il path
# completo dello struct come `sub_type`
# (`/Script/GameplayAbilities.GameplayAttributeData`) — non è nella whitelist
# di nomi corti di `MCP_STRUCT_PATHS`, va il path per esteso.
#
# **Il muro e come è stato aggirato**: `FGameplayModifierInfo.Attribute`/
# `.ModifierOp` e `FGameplayAttribute.AttributeName` rifiutano
# `set_editor_property` ("cannot be edited on instances" / read-only) — la
# via normale per costruire un modifier via Python è bloccata. Aggirato con
# `import_text` sull'INTERO struct `GameplayModifierInfo` in una volta sola,
# stessa tecnica già in uso in questo file per `EdGraphPinType`: il parser
# testuale non passa dalla stessa restrizione della reflection a singola
# proprietà. Verificato dal vivo end-to-end: costruito un modifier
# (`Attribute` che punta a un attributo reale di un AttributeSet Blueprint,
# `ModifierOp=AddBase`, `ScalableFloatMagnitude.Value=25`), aggiunto
# all'array `Modifiers` di un GameplayEffect, salvato l'asset, ricaricato da
# zero — il modifier era davvero lì con tutti i valori. Il campo `Attribute`
# interno (il puntatore FProperty vero) resta vuoto nell'export testuale
# anche dopo il roundtrip: sembra normale per come funziona
# `FGameplayAttribute` (la risoluzione avviene a runtime via
# `AttributeName`+`AttributeOwner`, non da un puntatore serializzato) ma
# **non è stato verificato in PIE** — solo la persistenza sull'asset, non il
# comportamento a runtime.
#
# I nomi corti accettati per `modifier_op` sono alias della sintassi
# testuale interna (`AddBase`, `AddFinal`, `MultiplyAdditive`,
# `DivideAdditive`, `MultiplyCompound`, `Override`), non i nomi dei membri
# dell'enum Python (`ADD_BASE` ecc.), perché passano per `import_text` e non
# per `set_editor_property`.


def _mcp_gas_enum_member(enum_type, nome):
    """Converte un nome amichevole (es. 'instanced_per_actor') nel membro
    reale dell'enum Python (es. `INSTANCED_PER_ACTOR`)."""
    chiave = re.sub(r"(?<!^)(?=[A-Z])", "_", str(nome)).upper().replace("-", "_").replace(" ", "_")
    chiave = re.sub(r"_+", "_", chiave)
    if not hasattr(enum_type, chiave):
        disponibili = sorted(m for m in dir(enum_type) if m.isupper())
        raise ValueError("Valore '%s' sconosciuto. Validi: %s" % (nome, ", ".join(disponibili)))
    return getattr(enum_type, chiave)


#: Alias amichevoli -> sintassi testuale interna di EGameplayModOp, per
#: `import_text` (bypassa il muro di ModifierInfo, vedi commento sopra).
_MCP_MODIFIER_OP_TEXT = {
    "add": "AddBase",
    "additive": "AddBase",
    "addbase": "AddBase",
    "add_base": "AddBase",
    "addfinal": "AddFinal",
    "add_final": "AddFinal",
    "multiply": "MultiplyAdditive",
    "multiplyadditive": "MultiplyAdditive",
    "multiply_additive": "MultiplyAdditive",
    "divide": "DivideAdditive",
    "divideadditive": "DivideAdditive",
    "divide_additive": "DivideAdditive",
    "multiplycompound": "MultiplyCompound",
    "multiply_compound": "MultiplyCompound",
    "override": "Override",
}


def mcp_create_gameplay_ability(package_path, name, instancing_policy=None, net_execution_policy=None):
    full = "%s/%s" % (package_path.rstrip("/"), name)
    if mcp_asset_lib().does_asset_exist(full):
        return {"path": full, "created": False, "reason": "esiste già"}

    factory = unreal.GameplayAbilitiesBlueprintFactory()
    bp = mcp_asset_tools().create_asset(name, package_path, unreal.GameplayAbilityBlueprint, factory)
    if bp is None:
        raise RuntimeError("Creazione GameplayAbility '%s' fallita." % full)

    cdo = unreal.get_default_object(bp.generated_class())
    applicato = {}
    if instancing_policy:
        cdo.set_editor_property(
            "InstancingPolicy",
            _mcp_gas_enum_member(unreal.GameplayAbilityInstancingPolicy, instancing_policy),
        )
        applicato["instancing_policy"] = instancing_policy
    if net_execution_policy:
        cdo.set_editor_property(
            "NetExecutionPolicy",
            _mcp_gas_enum_member(unreal.GameplayAbilityNetExecutionPolicy, net_execution_policy),
        )
        applicato["net_execution_policy"] = net_execution_policy

    unreal.BlueprintEditorLibrary.compile_blueprint(bp)
    mcp_asset_lib().save_asset(full)
    return {"path": full, "created": True, "applied": applicato}


def mcp_create_gameplay_effect(package_path, name, duration_policy=None, period=None):
    full = "%s/%s" % (package_path.rstrip("/"), name)
    if mcp_asset_lib().does_asset_exist(full):
        return {"path": full, "created": False, "reason": "esiste già"}

    factory = unreal.BlueprintFactory()
    factory.set_editor_property("parent_class", unreal.GameplayEffect)
    bp = mcp_asset_tools().create_asset(name, package_path, unreal.Blueprint, factory)
    if bp is None:
        raise RuntimeError("Creazione GameplayEffect '%s' fallita." % full)

    cdo = unreal.get_default_object(bp.generated_class())
    applicato = {}
    if duration_policy:
        cdo.set_editor_property(
            "DurationPolicy", _mcp_gas_enum_member(unreal.GameplayEffectDurationType, duration_policy)
        )
        applicato["duration_policy"] = duration_policy
    if period is not None:
        periodo = cdo.get_editor_property("Period")
        periodo.set_editor_property("Value", float(period))
        cdo.set_editor_property("Period", periodo)
        applicato["period"] = period

    unreal.BlueprintEditorLibrary.compile_blueprint(bp)
    mcp_asset_lib().save_asset(full)
    return {"path": full, "created": True, "applied": applicato}


def _mcp_gas_attribute_owner_path(attribute_set_path):
    bp = mcp_asset_lib().load_asset(attribute_set_path)
    if bp is None:
        raise ValueError("AttributeSet '%s' non trovato." % attribute_set_path)
    return bp.generated_class().get_path_name()


def mcp_ge_add_modifier(ge_path, attribute_set_path, attribute_name, modifier_op, magnitude):
    """Aggiunge un modifier a un GameplayEffect: collega un attributo di un
    AttributeSet Blueprint esistente, un'operazione e un valore fisso
    (ScalableFloat, niente curve/attribute-based magnitude per ora).

    Aggira il muro descritto in cima al file costruendo l'intero struct
    `GameplayModifierInfo` via `import_text`.
    """
    ge = mcp_load_blueprint(ge_path)
    cdo = unreal.get_default_object(ge.generated_class())

    owner_path = _mcp_gas_attribute_owner_path(attribute_set_path)
    chiave_op = str(modifier_op).lower().replace("_", "").replace(" ", "").replace("-", "")
    op_testo = _MCP_MODIFIER_OP_TEXT.get(chiave_op)
    if op_testo is None:
        raise ValueError(
            "modifier_op '%s' sconosciuto. Validi: add, add_final, multiply, divide, "
            "multiply_compound, override." % modifier_op
        )

    mod = unreal.GameplayModifierInfo()
    testo = (
        "(ModifierOp=%s,"
        "ModifierMagnitude=(MagnitudeCalculationType=ScalableFloat,ScalableFloatMagnitude=(Value=%f)),"
        'Attribute=(AttributeName="%s",AttributeOwner="%s"))'
    ) % (op_testo, float(magnitude), attribute_name, owner_path)
    if not mod.import_text(testo):
        raise RuntimeError("import_text ha rifiutato il modifier costruito: %s" % testo)

    modificatori = list(cdo.get_editor_property("Modifiers"))
    modificatori.append(mod)
    cdo.set_editor_property("Modifiers", modificatori)
    mcp_asset_lib().save_asset(ge_path)
    return mcp_ge_info(ge_path)


def mcp_ge_add_component(ge_path, component_class):
    """Aggiunge un `GameplayEffectComponent` (es. `AssetTagsGameplayEffectComponent`,
    `TargetTagRequirementsGameplayEffectComponent`, `ChanceToApplyGameplayEffectComponent`)
    a un GameplayEffect. Solo l'aggiunta: configurare i tag/condizioni al suo
    interno va fatto con `ue_exec_python` o a mano nell'editor — non
    verificato quali sotto-proprietà sono scrivibili caso per caso."""
    ge = mcp_load_blueprint(ge_path)
    cdo = unreal.get_default_object(ge.generated_class())

    cls = mcp_resolve_class(component_class)
    comp = unreal.new_object(cls, outer=cdo)

    componenti = list(cdo.get_editor_property("GEComponents"))
    componenti.append(comp)
    cdo.set_editor_property("GEComponents", componenti)
    mcp_asset_lib().save_asset(ge_path)
    return {
        "path": ge_path,
        "component_class": component_class,
        "ge_components": [str(c.get_class().get_name()) for c in cdo.get_editor_property("GEComponents")],
    }


def mcp_ge_info(ge_path):
    ge = mcp_load_blueprint(ge_path)
    cdo = unreal.get_default_object(ge.generated_class())

    modificatori = []
    for m in cdo.get_editor_property("Modifiers"):
        attr = m.get_editor_property("Attribute")
        proprietario = attr.get_editor_property("AttributeOwner")
        magnitudine = m.get_editor_property("ModifierMagnitude").get_editor_property("ScalableFloatMagnitude")
        modificatori.append(
            {
                "attribute_name": str(attr.get_editor_property("AttributeName")),
                "attribute_owner": proprietario.get_path_name() if proprietario else None,
                "modifier_op": str(m.get_editor_property("ModifierOp")),
                "magnitude": float(magnitudine.get_editor_property("Value")),
            }
        )

    periodo = cdo.get_editor_property("Period")
    componenti = [str(c.get_class().get_name()) for c in cdo.get_editor_property("GEComponents")]
    return {
        "path": ge_path,
        "duration_policy": str(cdo.get_editor_property("DurationPolicy")),
        "period": float(periodo.get_editor_property("Value")),
        "modifiers": modificatori,
        "ge_components": componenti,
    }


# ===================================================================== landscape
#
# Fase 9, verificata dal vivo su UE 5.8 il 2026-07-31 — ed è la fase più
# ridimensionata di tutte.
#
# **Creare** un landscape da Python non si può: `spawn_actor_from_class(
# unreal.Landscape, ...)` non produce un terreno ma un `LandscapePlaceholder`
# vuoto (nessun componente, nessun target layer, nemmeno i metodi di
# `ALandscape`). Le classi che lo creano davvero — `ULandscapeSubsystem`,
# `ULandscapeEditorObject`, `UActorFactoryLandscape` — esistono nel motore
# (trovate con `ClassIterator`) ma non sono esposte al Python di UE:
# `hasattr(unreal, "LandscapeSubsystem")` è False. Il landscape va creato a
# mano con il Landscape Mode dell'editor; da lì in poi tutto il resto è
# scriptabile.
#
# Su un landscape **esistente** funzionano: heightmap in ingresso e in
# uscita, weightmap dei layer di pittura, materiale, grass, e la lettura di
# target layer / edit layer. Il ponte fra un file immagine e il landscape è
# un `TextureRenderTarget2D` transitorio: `import_file_as_texture2d` →
# `begin_draw_canvas_to_render_target` → `Canvas.draw_texture` →
# `landscape_import_heightmap_from_render_target`. Questa catena è verificata
# dal vivo fino al render target compreso (PNG 64×64 a gradiente riletto
# pixel per pixel dal RT con i valori giusti); l'ultimo anello — la chiamata
# sul landscape — **non è verificato**, perché in quattroCantoni non esiste
# nessun landscape su cui provarlo e Python non può crearne uno.


def _mcp_mondo_editor():
    return unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()


#: Formati di render target accettati dai tool di landscape.
MCP_RT_FORMATS = {
    "rgba8": "RTF_RGBA8",
    "rgba16f": "RTF_RGBA16f",
    "rgba32f": "RTF_RGBA32f",
    "r8": "RTF_R8",
    "r16f": "RTF_R16f",
    "r32f": "RTF_R32f",
}


def _mcp_rt_format(nome):
    chiave = str(nome).lower().replace("_", "").replace("-", "")
    chiave = chiave[3:] if chiave.startswith("rtf") else chiave
    membro = MCP_RT_FORMATS.get(chiave)
    if membro is None:
        raise ValueError(
            "Formato render target '%s' sconosciuto. Validi: %s"
            % (nome, ", ".join(sorted(MCP_RT_FORMATS)))
        )
    return getattr(unreal.TextureRenderTargetFormat, membro)


def mcp_landscape_list():
    """I landscape presenti nel livello corrente.

    Se è vuota, il livello non ha terreni: nessun tool di questa sezione ha
    qualcosa su cui lavorare, e crearne uno va fatto dall'editor (Landscape
    Mode) perché Python non può.
    """
    trovati = []
    for attore in mcp_actor_subsystem().get_all_level_actors():
        if not isinstance(attore, unreal.LandscapeProxy):
            continue
        trovati.append(
            {
                "label": str(attore.get_actor_label()),
                "class": str(attore.get_class().get_name()),
                "location": mcp_vec(attore.get_actor_location()),
                "components": len(attore.get_components_by_class(unreal.LandscapeComponent)),
            }
        )
    return {"landscapes": trovati, "count": len(trovati)}


def mcp_require_landscape(label=None):
    """Il landscape indicato, o l'unico presente se il livello ne ha uno solo."""
    landscape = [
        a for a in mcp_actor_subsystem().get_all_level_actors() if isinstance(a, unreal.LandscapeProxy)
    ]
    if not landscape:
        raise ValueError(
            "Nessun landscape nel livello corrente. Python non può crearne uno: "
            "va aggiunto dall'editor con Landscape Mode."
        )
    if label is None:
        if len(landscape) > 1:
            raise ValueError(
                "Nel livello ci sono più landscape (%s): indica quale con `label`."
                % ", ".join(str(a.get_actor_label()) for a in landscape)
            )
        return landscape[0]
    for attore in landscape:
        if str(attore.get_actor_label()) == str(label):
            return attore
    raise ValueError(
        "Landscape '%s' non trovato. Presenti: %s"
        % (label, ", ".join(str(a.get_actor_label()) for a in landscape))
    )


def mcp_landscape_info(label=None):
    """Componenti, materiale, target layer (pittura) ed edit layer di un landscape."""
    landscape = mcp_require_landscape(label)
    componenti = landscape.get_components_by_class(unreal.LandscapeComponent)

    info = {
        "label": str(landscape.get_actor_label()),
        "class": str(landscape.get_class().get_name()),
        "location": mcp_vec(landscape.get_actor_location()),
        "scale": mcp_vec(landscape.get_actor_scale3d()),
        "components": len(componenti),
    }

    materiale = landscape.get_editor_property("landscape_material")
    info["material"] = materiale.get_path_name() if materiale else None

    for chiave, proprieta in (
        ("component_size_quads", "component_size_quads"),
        ("subsection_size_quads", "subsection_size_quads"),
        ("num_subsections", "num_subsections"),
    ):
        try:
            info[chiave] = int(landscape.get_editor_property(proprieta))
        except Exception:  # noqa: BLE001
            info[chiave] = None

    # `get_target_layer_names` e `get_edit_layers_bp` esistono solo su ALandscape,
    # non su ALandscapeStreamingProxy: chi ha solo un proxy vede meno cose.
    if hasattr(landscape, "get_target_layer_names"):
        info["target_layers"] = [str(n) for n in landscape.get_target_layer_names()]
    if hasattr(landscape, "get_edit_layers_bp"):
        info["edit_layers"] = [str(x) for x in landscape.get_edit_layers_bp()]
    if hasattr(landscape, "get_grass_enabled"):
        info["grass_enabled"] = bool(landscape.get_grass_enabled())
    return info


def mcp_render_target_from_image(image_path, width=None, height=None, rt_format="RGBA8"):
    """Carica un'immagine dal disco in un render target transitorio.

    È il ponte fra un file heightmap/weightmap e le API di landscape, che
    accettano solo `TextureRenderTarget2D`. Verificato dal vivo: un PNG a
    gradiente riletto dal render target restituisce i valori attesi pixel per
    pixel.
    """
    if not os.path.isfile(image_path):
        raise ValueError("File immagine '%s' inesistente." % image_path)

    mondo = _mcp_mondo_editor()
    texture = unreal.RenderingLibrary.import_file_as_texture2d(mondo, image_path)
    if texture is None:
        raise RuntimeError(
            "Unreal non è riuscito a importare '%s' come texture (formati tipici: "
            "PNG, EXR, TGA)." % image_path
        )

    larghezza = int(width or texture.blueprint_get_size_x())
    altezza = int(height or texture.blueprint_get_size_y())
    render_target = unreal.RenderingLibrary.create_render_target2d(
        mondo, larghezza, altezza, _mcp_rt_format(rt_format)
    )
    if render_target is None:
        raise RuntimeError("Creazione del render target %dx%d fallita." % (larghezza, altezza))

    canvas, _dimensione, contesto = unreal.RenderingLibrary.begin_draw_canvas_to_render_target(
        mondo, render_target
    )
    if canvas is None:
        unreal.RenderingLibrary.end_draw_canvas_to_render_target(mondo, contesto)
        raise RuntimeError("Canvas non disponibile sul render target.")
    try:
        canvas.draw_texture(
            texture,
            unreal.Vector2D(0.0, 0.0),
            unreal.Vector2D(float(larghezza), float(altezza)),
            unreal.Vector2D(0.0, 0.0),
            unreal.Vector2D(1.0, 1.0),
        )
    finally:
        unreal.RenderingLibrary.end_draw_canvas_to_render_target(mondo, contesto)

    return render_target, {
        "image": image_path,
        "width": larghezza,
        "height": altezza,
        "format": str(rt_format),
    }


def mcp_landscape_import_heightmap(image_path, label=None, rt_format="RGBA8", from_rg_channel=False):
    """Sovrascrive l'heightmap di un landscape con un'immagine dal disco."""
    landscape = mcp_require_landscape(label)
    render_target, dettagli = mcp_render_target_from_image(image_path, rt_format=rt_format)

    with mcp_transaction("MCP: heightmap di %s" % landscape.get_actor_label()):
        esito = landscape.landscape_import_heightmap_from_render_target(
            render_target, bool(from_rg_channel)
        )
    if not esito:
        raise RuntimeError(
            "Unreal ha rifiutato l'import dell'heightmap. Cause tipiche: il "
            "render target non copre la risoluzione del landscape, o il "
            "formato non è fra RTF_RGBA8/RGBA16f/RGBA32f."
        )
    return {
        "landscape": str(landscape.get_actor_label()),
        "imported": True,
        "source": dettagli,
        "from_rg_channel": bool(from_rg_channel),
    }


def mcp_landscape_import_weightmap(layer_name, image_path, label=None, rt_format="RGBA8"):
    """Sovrascrive il weightmap di un layer di pittura con un'immagine.

    Il layer deve già esistere sul landscape (`target_layers` in
    `mcp_landscape_info`): i target layer nascono dal materiale del
    landscape, non si creano da qui.
    """
    landscape = mcp_require_landscape(label)
    if hasattr(landscape, "get_target_layer_names"):
        disponibili = [str(n) for n in landscape.get_target_layer_names()]
        if disponibili and str(layer_name) not in disponibili:
            raise ValueError(
                "Layer '%s' non presente sul landscape. Disponibili: %s"
                % (layer_name, ", ".join(disponibili))
            )

    render_target, dettagli = mcp_render_target_from_image(image_path, rt_format=rt_format)
    with mcp_transaction("MCP: weightmap %s" % layer_name):
        esito = landscape.landscape_import_weightmap_from_render_target(
            render_target, unreal.Name(str(layer_name))
        )
    if not esito:
        raise RuntimeError("Unreal ha rifiutato l'import del weightmap '%s'." % layer_name)
    return {
        "landscape": str(landscape.get_actor_label()),
        "layer": str(layer_name),
        "imported": True,
        "source": dettagli,
    }


def mcp_landscape_export_heightmap(
    output_dir, file_name, label=None, resolution=1024, rt_format="RGBA8", into_rg_channel=False
):
    """Esporta l'heightmap del landscape come immagine sul disco.

    Il formato del file lo decide il render target: RTF_RGBA8 esce in PNG,
    i formati float in HDR.
    """
    landscape = mcp_require_landscape(label)
    mondo = _mcp_mondo_editor()
    lato = int(resolution)
    render_target = unreal.RenderingLibrary.create_render_target2d(
        mondo, lato, lato, _mcp_rt_format(rt_format)
    )
    esito = landscape.landscape_export_heightmap_to_render_target(
        render_target, bool(into_rg_channel), True
    )
    if not esito:
        raise RuntimeError("Unreal ha rifiutato l'export dell'heightmap.")

    unreal.RenderingLibrary.export_render_target(mondo, render_target, output_dir, file_name)
    return {
        "landscape": str(landscape.get_actor_label()),
        "file": os.path.join(output_dir, file_name),
        "resolution": lato,
        "format": str(rt_format),
    }


def mcp_landscape_set_material(material_path, label=None):
    """Assegna il materiale a un landscape (definisce anche i suoi target layer)."""
    landscape = mcp_require_landscape(label)
    materiale = mcp_asset_lib().load_asset(material_path)
    if materiale is None:
        raise ValueError("Materiale '%s' non trovato." % material_path)

    with mcp_transaction("MCP: materiale di %s" % landscape.get_actor_label()):
        landscape.set_editor_property("landscape_material", materiale)
    return mcp_landscape_info(str(landscape.get_actor_label()))


def mcp_landscape_set_grass(enabled=True, label=None):
    """Accende o spegne il grass system del landscape (foliage procedurale)."""
    landscape = mcp_require_landscape(label)
    if not hasattr(landscape, "set_grass_enabled"):
        raise RuntimeError(
            "'%s' è un %s: il grass si comanda dall'attore Landscape principale."
            % (landscape.get_actor_label(), landscape.get_class().get_name())
        )
    landscape.set_grass_enabled(bool(enabled))
    return {
        "landscape": str(landscape.get_actor_label()),
        "grass_enabled": bool(landscape.get_grass_enabled()),
    }


# =========================================================================== PCG
#
# Fase 10, verificata dal vivo su UE 5.8 il 2026-07-31 — ed è la sorpresa
# della roadmap. Dopo Blueprint, UMG e Niagara ci si aspettava l'ennesimo
# `EdGraph` protetto; invece il grafo PCG è pienamente scriptabile:
# `add_node_of_type`, `add_edge`, `remove_edge`, `remove_node`,
# `get_all_edges`, `nodes`, `set_node_position` sono tutti esposti, e le
# proprietà di un nodo si scrivono sul suo `PCGSettings` come su un oggetto
# qualunque. Verificato costruendo un grafo Input → SurfaceSampler →
# StaticMeshSpawner, salvandolo e rileggendolo da zero: nodi, archi e
# proprietà c'erano ancora, e i nomi dei nodi compaiono nel .uasset.
#
# La ragione è la stessa dei Behavior Tree della fase 6: il grafo PCG è un
# grafo di dati veri (`UPCGNode` + `UPCGEdge`), non un `UEdGraph` di nodi K2
# con il vero contenuto in una proprietà protetta. L'`UPCGEditorGraph` è solo
# la sua rappresentazione visiva, e non serve toccarlo.


def _mcp_pcg_graph(graph_path):
    grafo = mcp_asset_lib().load_asset(graph_path)
    if grafo is None:
        raise ValueError("Grafo PCG '%s' non trovato." % graph_path)
    if not isinstance(grafo, unreal.PCGGraph):
        raise ValueError(
            "'%s' è un %s, non un PCGGraph." % (graph_path, grafo.get_class().get_name())
        )
    return grafo


def _mcp_pcg_pin_labels(pins):
    etichette = []
    for pin in pins or []:
        try:
            proprieta = pin.get_editor_property("properties")
            etichette.append(str(proprieta.get_editor_property("label")))
        except Exception:  # noqa: BLE001, S112
            continue
    return etichette


def _mcp_pcg_node(grafo, nome):
    """Un nodo del grafo per nome.

    "input" e "output" sono alias dei due nodi che ogni grafo PCG ha già:
    senza di loro chi chiama dovrebbe indovinare che si chiamano
    `DefaultInputNode` e `DefaultOutputNode`.
    """
    chiave = str(nome).strip().lower()
    if chiave in ("input", "in", "defaultinputnode"):
        return grafo.get_input_node()
    if chiave in ("output", "out", "defaultoutputnode"):
        return grafo.get_output_node()

    nodi = list(grafo.nodes)
    for nodo in nodi:
        if str(nodo.get_name()) == str(nome):
            return nodo
    for nodo in nodi:
        if str(nodo.get_name()).lower() == chiave:
            return nodo
    raise ValueError(
        "Nodo '%s' non trovato nel grafo. Presenti: %s"
        % (nome, ", ".join(str(n.get_name()) for n in nodi) or "nessuno (grafo vuoto)")
    )


def mcp_create_pcg_graph(package_path, name):
    """Crea un asset PCGGraph vuoto (con i suoi nodi Input e Output)."""
    full = "%s/%s" % (package_path.rstrip("/"), name)
    if mcp_asset_lib().does_asset_exist(full):
        return {"path": full, "created": False, "reason": "esiste già"}

    grafo = mcp_asset_tools().create_asset(
        name, package_path, unreal.PCGGraph, unreal.PCGGraphFactory()
    )
    if grafo is None:
        raise RuntimeError("Creazione del grafo PCG '%s' fallita." % full)
    mcp_asset_lib().save_asset(full)
    return {"path": full, "created": True}


def mcp_pcg_add_node(graph_path, settings_class, position=None):
    """Aggiunge un nodo al grafo, dalla classe di settings che lo definisce.

    In PCG il "tipo" di un nodo *è* la sua classe di settings: un
    SurfaceSampler è un nodo con `PCGSurfaceSamplerSettings`.
    """
    grafo = _mcp_pcg_graph(graph_path)
    cls = mcp_resolve_class(settings_class)
    if cls is None:
        raise ValueError("Classe di settings PCG '%s' non risolta." % settings_class)

    esito = grafo.add_node_of_type(cls)
    nodo = esito[0] if isinstance(esito, tuple) else esito
    if nodo is None:
        raise RuntimeError("Unreal non ha creato il nodo per '%s'." % settings_class)

    if position is not None:
        x, y = (position.get("x"), position.get("y")) if isinstance(position, dict) else position
        nodo.set_node_position(int(x), int(y))

    mcp_asset_lib().save_asset(graph_path)
    return {
        "graph": graph_path,
        "node": str(nodo.get_name()),
        "settings_class": str(settings_class),
        "input_pins": _mcp_pcg_pin_labels(nodo.input_pins),
        "output_pins": _mcp_pcg_pin_labels(nodo.output_pins),
    }


def mcp_pcg_connect(graph_path, from_node, from_pin, to_node, to_pin):
    """Collega due nodi del grafo. `from_node`/`to_node` accettano anche
    "input" e "output" per i nodi di ingresso e uscita del grafo."""
    grafo = _mcp_pcg_graph(graph_path)
    partenza = _mcp_pcg_node(grafo, from_node)
    arrivo = _mcp_pcg_node(grafo, to_node)

    disponibili_out = _mcp_pcg_pin_labels(partenza.output_pins)
    disponibili_in = _mcp_pcg_pin_labels(arrivo.input_pins)
    if disponibili_out and str(from_pin) not in disponibili_out:
        raise ValueError(
            "'%s' non ha un pin di uscita '%s'. Disponibili: %s"
            % (partenza.get_name(), from_pin, ", ".join(disponibili_out))
        )
    if disponibili_in and str(to_pin) not in disponibili_in:
        raise ValueError(
            "'%s' non ha un pin di ingresso '%s'. Disponibili: %s"
            % (arrivo.get_name(), to_pin, ", ".join(disponibili_in))
        )

    grafo.add_edge(partenza, unreal.Name(str(from_pin)), arrivo, unreal.Name(str(to_pin)))
    mcp_asset_lib().save_asset(graph_path)
    return {
        "graph": graph_path,
        "connected": "%s.%s -> %s.%s"
        % (partenza.get_name(), from_pin, arrivo.get_name(), to_pin),
        "edges": len(grafo.get_all_edges()),
    }


def mcp_pcg_disconnect(graph_path, from_node, from_pin, to_node, to_pin):
    """Rimuove un collegamento fra due nodi."""
    grafo = _mcp_pcg_graph(graph_path)
    partenza = _mcp_pcg_node(grafo, from_node)
    arrivo = _mcp_pcg_node(grafo, to_node)

    rimosso = grafo.remove_edge(
        partenza, unreal.Name(str(from_pin)), arrivo, unreal.Name(str(to_pin))
    )
    mcp_asset_lib().save_asset(graph_path)
    return {"graph": graph_path, "removed": bool(rimosso), "edges": len(grafo.get_all_edges())}


def mcp_pcg_remove_node(graph_path, node):
    """Toglie un nodo dal grafo, con tutti i suoi collegamenti."""
    grafo = _mcp_pcg_graph(graph_path)
    nodo = _mcp_pcg_node(grafo, node)
    nome = str(nodo.get_name())
    grafo.remove_node(nodo)
    mcp_asset_lib().save_asset(graph_path)
    return {"graph": graph_path, "removed": nome, "nodes": [str(n.get_name()) for n in grafo.nodes]}


def mcp_pcg_set_node_property(graph_path, node, property_name, value):
    """Scrive una proprietà sulle settings di un nodo (densità del sampler,
    mesh dello spawner, seed…)."""
    grafo = _mcp_pcg_graph(graph_path)
    nodo = _mcp_pcg_node(grafo, node)
    settings = nodo.get_settings()
    if settings is None:
        raise RuntimeError("Il nodo '%s' non espone settings." % node)

    settings.set_editor_property(property_name, mcp_coerce_value(value))
    letto = settings.get_editor_property(property_name)
    mcp_asset_lib().save_asset(graph_path)
    return {
        "graph": graph_path,
        "node": str(nodo.get_name()),
        "settings_class": str(settings.get_class().get_name()),
        "property": property_name,
        "value": letto if isinstance(letto, (bool, int, float, str)) or letto is None else str(letto),
    }


def mcp_pcg_graph_info(graph_path):
    """Nodi (con pin e classe di settings) e archi di un grafo PCG."""
    grafo = _mcp_pcg_graph(graph_path)

    def descrivi(nodo, ruolo=None):
        settings = nodo.get_settings()
        posizione = nodo.get_node_position()
        return {
            "name": str(nodo.get_name()),
            "role": ruolo,
            "settings_class": str(settings.get_class().get_name()) if settings else None,
            "input_pins": _mcp_pcg_pin_labels(nodo.input_pins),
            "output_pins": _mcp_pcg_pin_labels(nodo.output_pins),
            "position": {"x": int(posizione[0]), "y": int(posizione[1])} if posizione else None,
        }

    nodi = [descrivi(grafo.get_input_node(), "input"), descrivi(grafo.get_output_node(), "output")]
    nodi += [descrivi(n) for n in grafo.nodes]

    archi = []
    for arco in grafo.get_all_edges():
        # In `UPCGEdge` i nomi sono dal punto di vista dell'arco: `input_pin`
        # è il pin da cui l'arco parte (un pin di *output* del nodo a monte).
        origine = arco.get_editor_property("input_pin")
        destinazione = arco.get_editor_property("output_pin")
        if origine is None or destinazione is None:
            continue
        archi.append(
            {
                "from": str(origine.get_editor_property("node").get_name()),
                "from_pin": _mcp_pcg_pin_labels([origine])[0] if _mcp_pcg_pin_labels([origine]) else None,
                "to": str(destinazione.get_editor_property("node").get_name()),
                "to_pin": _mcp_pcg_pin_labels([destinazione])[0]
                if _mcp_pcg_pin_labels([destinazione])
                else None,
            }
        )

    return {"graph": graph_path, "nodes": nodi, "edges": archi}


def mcp_pcg_spawn_volume(graph_path, label=None, location=None, size=None):
    """Piazza un PCGVolume nel livello e ci attacca il grafo.

    Il volume è il dominio su cui il grafo lavora. La sua dimensione si regola
    con la scala dell'attore: il brush di default è 200×200×200 cm, quindi
    `size` è espressa in centimetri e viene convertita in scala.
    """
    grafo = _mcp_pcg_graph(graph_path)
    posizione = mcp_to_vector(location)

    with mcp_transaction("MCP: volume PCG per %s" % graph_path):
        volume = mcp_actor_subsystem().spawn_actor_from_class(
            unreal.PCGVolume, posizione, unreal.Rotator(0.0, 0.0, 0.0)
        )
        if volume is None:
            raise RuntimeError("Spawn del PCGVolume fallito.")
        if label:
            volume.set_actor_label(label)
        if size is not None:
            lati = mcp_to_vector(size, (200.0, 200.0, 200.0))
            volume.set_actor_scale3d(
                unreal.Vector(lati.x / 200.0, lati.y / 200.0, lati.z / 200.0)
            )

        componente = volume.get_component_by_class(unreal.PCGComponent)
        if componente is None:
            raise RuntimeError("Il PCGVolume non ha un PCGComponent.")
        componente.set_graph(grafo)

    return {
        "actor": str(volume.get_actor_label()),
        "graph": graph_path,
        "location": mcp_vec(volume.get_actor_location()),
        "scale": mcp_vec(volume.get_actor_scale3d()),
    }


def mcp_pcg_generate(label, force=True):
    """Fa rigenerare il PCG di un attore (volume o attore con PCGComponent)."""
    attore = mcp_require_actor(label)
    componente = attore.get_component_by_class(unreal.PCGComponent)
    if componente is None:
        raise ValueError(
            "'%s' non ha un PCGComponent: aggiungilo con `ue_add_component` o "
            "usa un PCGVolume." % label
        )
    grafo = componente.get_graph()
    componente.generate(bool(force))
    return {
        "actor": label,
        "graph": grafo.get_path_name() if grafo else None,
        "generated": True,
    }


def mcp_pcg_cleanup(label, remove_components=True):
    """Cancella ciò che il PCG ha generato su un attore."""
    attore = mcp_require_actor(label)
    componente = attore.get_component_by_class(unreal.PCGComponent)
    if componente is None:
        raise ValueError("'%s' non ha un PCGComponent." % label)
    componente.cleanup(bool(remove_components))
    return {"actor": label, "cleaned": True}

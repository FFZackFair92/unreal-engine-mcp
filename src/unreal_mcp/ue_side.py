"""Helper eseguiti DENTRO l'editor Unreal.

Questo file non viene mai importato dal server MCP: viene letto come testo e
anteposto ad ogni snippet inviato a ``ExecutePythonCommandEx``. Per questo può
usare liberamente il modulo ``unreal``, disponibile solo nell'editor.
"""

import json
import os

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
        "log_bytes": len(nuove),
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
        if referenti and not force:
            raise ValueError(
                "%s è referenziato da %d asset (%s). Cancellarlo lascerebbe "
                "riferimenti rotti: usa force=True se è quello che vuoi."
                % (percorso, len(referenti), ", ".join(referenti[:5]))
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
        cdo = unreal.get_default_object(blueprint.generated_class())
        cdo.set_editor_property(var_name, default_value)
        library.compile_blueprint(blueprint)
        applied_default = default_value

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
    cartella = os.path.join(unreal.Paths.project_saved_dir(), "Screenshots", "MCP")
    if not os.path.isdir(cartella):
        os.makedirs(cartella)

    nome = filename or ("mcp_%d.png" % int(_mcp_time().time() * 1000))
    if not nome.lower().endswith(".png"):
        nome += ".png"
    destinazione = os.path.join(cartella, nome)

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

    log_dir = unreal.Paths.project_log_dir()
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
    config_dir = unreal.Paths.project_config_dir()
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
        "project_file": str(unreal.Paths.get_project_file_path()),
        "project_content_dir": str(unreal.Paths.project_content_dir()),
        "current_level": current_level,
        "actor_count": len(mcp_actor_subsystem().get_all_level_actors()),
        "python_ok": True,
        "capabilities": mcp_capabilities(),
    }


def mcp_log_path():
    """Il file di log dell'editor attualmente in scrittura, o None.

    Non è sempre `<Progetto>.log`: quando gira una seconda istanza Unreal
    aggiunge un suffisso (`_2`), quindi si prende il più recente della cartella.
    """
    log_dir = unreal.Paths.project_log_dir()
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

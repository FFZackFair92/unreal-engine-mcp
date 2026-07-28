"""Helper eseguiti DENTRO l'editor Unreal.

Questo file non viene mai importato dal server MCP: viene letto come testo e
anteposto ad ogni snippet inviato a ``ExecutePythonCommandEx``. Per questo può
usare liberamente il modulo ``unreal``, disponibile solo nell'editor.
"""

import json
import os

import unreal  # noqa: F401  (disponibile solo dentro l'editor)

# ---------------------------------------------------------------- subsystems


def mcp_actor_subsystem():
    return unreal.get_editor_subsystem(unreal.EditorActorSubsystem)


def mcp_level_subsystem():
    return unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)


def mcp_asset_tools():
    return unreal.AssetToolsHelpers.get_asset_tools()


def mcp_asset_lib():
    return unreal.EditorAssetLibrary


# --------------------------------------------------------- versione e capacità


def mcp_engine_minor():
    """Minor version del motore (es. 8 per 5.8), o None se non riconoscibile."""
    try:
        parts = str(unreal.SystemLibrary.get_engine_version()).split(".")
        if parts[0] == "5":
            return int(parts[1])
    except Exception:  # noqa: BLE001
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
    for task, path in zip(tasks, files):
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


def mcp_spawn(class_or_asset, location=None, rotation=None, scale=None, label=None):
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
    nota = None
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
        nota = (
            "net_mode scritto in Config/DefaultEditorPerProjectUserSettings.ini: "
            "attivo al prossimo avvio dell'editor. Per usarlo subito, cambialo dal menu "
            "a tendina accanto al pulsante Play (Net Mode: Play As Listen Server)."
        )

    return {
        "num_players": settings.get_editor_property("PlayNumberOfClients"),
        "net_mode": mode_name,
        "net_mode_attivo_subito": applied_now,
        "one_process": settings.get_editor_property("RunUnderOneProcess"),
        "nota": nota,
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

    with open(file_log, "r", encoding="utf-8", errors="replace") as handle:
        righe_prima = len(handle.read().splitlines())

    mondo = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    unreal.SystemLibrary.execute_console_command(mondo, "LiveCoding.Compile")

    esiti = ("succeeded", "failed", "no changes", "up to date", "error")
    scadenza = _time.time() + float(attesa_massima)
    nuove = []

    while _time.time() < scadenza:
        _time.sleep(1.0)
        with open(file_log, "r", encoding="utf-8", errors="replace") as handle:
            righe = handle.read().splitlines()
        nuove = [r.split("]")[-1].strip() for r in righe[righe_prima:] if "LiveCoding" in r]
        if any(any(e in r.lower() for e in esiti) for r in nuove):
            break

    riuscito = any("succeeded" in r.lower() or "no changes" in r.lower() for r in nuove)
    fallito = any("failed" in r.lower() or "error" in r.lower() for r in nuove)

    return {
        "avviato": True,
        "riuscito": riuscito,
        "fallito": fallito,
        "in_corso": not (riuscito or fallito),
        "log": nuove[-10:],
        "nota": (
            "Live Coding applica patch alle funzioni esistenti. Se hai aggiunto o "
            "modificato UCLASS/UFUNCTION/UPROPERTY serve comunque ue_build_start "
            "a editor chiuso."
        ),
    }


def mcp_set_project_setting(section, key, value, config="Game"):
    """Scrive in Config/Default<config>.ini. Alcune voci richiedono riavvio editor."""
    config_dir = unreal.Paths.project_config_dir()
    ini_path = os.path.join(config_dir, "Default%s.ini" % config)
    lines = []
    if os.path.exists(ini_path):
        with open(ini_path, "r", encoding="utf-8-sig") as handle:
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


def mcp_tail_log(lines=80, only_errors=False):
    log_dir = unreal.Paths.project_log_dir()
    candidates = [os.path.join(log_dir, f) for f in os.listdir(log_dir) if f.endswith(".log")]
    if not candidates:
        return {"file": None, "lines": []}
    newest = max(candidates, key=os.path.getmtime)
    with open(newest, "r", encoding="utf-8", errors="replace") as handle:
        content = handle.read().splitlines()
    if only_errors:
        content = [l for l in content if "Error" in l or "Warning" in l]
    # Le righe del log contengono le sentinelle delle chiamate MCP precedenti:
    # vanno neutralizzate, altrimenti confondono il parser della risposta.
    pulite = [l.replace("<<<MCP_JSON", "<<<mcp-json") for l in content[-int(lines) :]]
    return {"file": newest, "lines": pulite}


def mcp_dumps(value):
    return json.dumps(value, default=str)

"""Modulo `unreal` finto: permette di eseguire gli snippet generati dal server MCP
in locale, senza avere Unreal Engine installato.

Non riproduce il motore: riproduce la superficie di API usata da `ue_side.py`,
quel tanto che basta perché gli snippet vengano eseguiti davvero (e quindi ne
verifichiamo sintassi, nomi degli helper e flusso dei dati).
"""

from __future__ import annotations

import types
import uuid


class FakeObject:
    """Base con il protocollo get/set_editor_property di Unreal."""

    def __init__(self, **kwargs):
        self._props = dict(kwargs)

    def set_editor_property(self, name, value):
        self._props[name] = value

    def get_editor_property(self, name):
        return self._props.get(name)


class Vector:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = float(x), float(y), float(z)


class Rotator:
    def __init__(self, roll=0.0, pitch=0.0, yaw=0.0):
        self.roll, self.pitch, self.yaw = float(roll), float(pitch), float(yaw)


class Name(str):
    pass


class Text(str):
    def is_empty(self):
        return len(self) == 0


class Guid(str):
    pass


class Transform(FakeObject):
    @staticmethod
    def static_struct():
        return "StructTransform"


class LinearColor(FakeObject):
    @staticmethod
    def static_struct():
        return "StructLinearColor"


def _static_struct_for(label):
    return lambda: "Struct" + label


Vector.static_struct = staticmethod(_static_struct_for("Vector"))
Rotator.static_struct = staticmethod(_static_struct_for("Rotator"))


class Actor(FakeObject):
    def __init__(self, class_name="Actor", label="Actor_0"):
        super().__init__()
        self._class_name = class_name
        self._label = label
        self._location = Vector()
        self._rotation = Rotator()
        self._scale = Vector(1, 1, 1)

    # API Unreal
    def get_actor_label(self):
        return self._label

    def set_actor_label(self, label):
        self._label = label

    def get_name(self):
        return self._label

    def get_class(self):
        return types.SimpleNamespace(get_name=lambda: self._class_name)

    def get_path_name(self):
        return "/Game/Levels/L_Test.L_Test:PersistentLevel." + self._label

    def get_actor_location(self):
        return self._location

    def get_actor_rotation(self):
        return self._rotation

    def set_actor_location(self, value, sweep=False, teleport=False):
        self._location = value

    def set_actor_rotation(self, value, teleport=False):
        self._rotation = value

    def set_actor_scale3d(self, value):
        self._scale = value


class Blueprint(FakeObject):
    def __init__(self, path, parent):
        super().__init__(new_variables=[])
        self.path = path
        self.parent = parent

    def generated_class(self):
        return self.path + "_C"

    def get_class(self):
        return types.SimpleNamespace(get_name=lambda: "Blueprint")


class SoundCue(FakeObject):
    pass


class MetaSoundSource(FakeObject):
    pass


class SoundWave(FakeObject):
    pass


class BlueprintFactory(FakeObject):
    pass


class SoundCueFactoryNew(FakeObject):
    pass


class MetaSoundSourceFactory(FakeObject):
    pass


class AssetImportTask(FakeObject):
    pass


class FbxImportUI(FakeObject):
    pass


class AddNewSubobjectParams(FakeObject):
    pass


class EdGraphPinType(FakeObject):
    """In UE 5.8 si popola solo via import_text (le proprietà non sono esposte)."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.text = ""

    def import_text(self, content):
        self.text = content
        return content.startswith("(PinCategory=")

    def export_text(self):
        return self.text


class BPVariableDescription(FakeObject):
    pass


class LevelEditorPlaySettings(FakeObject):
    pass


class LifetimeCondition:
    COND_NONE = "COND_NONE"


class PlayNetMode:
    PIE_Standalone = "PIE_Standalone"
    PIE_ListenServer = "PIE_ListenServer"
    PIE_Client = "PIE_Client"


class _World:
    def __init__(self, state):
        self._state = state


def build_fake_unreal(tmp_path):
    """Costruisce un modulo `unreal` finto con stato in memoria."""

    state = {
        "assets": {},        # path -> oggetto
        "asset_classes": {}, # path -> nome classe
        "actors": [],
        "levels": [],
        "current_level": "L_Test",
        "imports": [],
        "saved": [],
        "pie": [],
    }

    module = types.ModuleType("unreal")
    module._state = state

    # ---- tipi base
    for cls in (
        Vector, Rotator, Name, Text, Guid, Transform, LinearColor, Actor, Blueprint,
        SoundCue, SoundWave, MetaSoundSource, BlueprintFactory, SoundCueFactoryNew,
        MetaSoundSourceFactory, AssetImportTask, FbxImportUI, AddNewSubobjectParams,
        EdGraphPinType, BPVariableDescription, LevelEditorPlaySettings, LifetimeCondition,
        PlayNetMode,
    ):
        setattr(module, cls.__name__, cls)

    # classi comuni risolte per nome (mcp_resolve_class usa hasattr(unreal, ref))
    for name in (
        "StaticMeshActor", "PlayerStart", "PointLight", "Character", "Pawn",
        "GameModeBase", "GameStateBase", "PlayerState", "AIController",
        "StaticMeshComponent", "BoxComponent", "SceneComponent",
    ):
        setattr(module, name, type(name, (FakeObject,), {"__unreal_name__": name}))

    # ---- subsystem attori
    class EditorActorSubsystem:
        def spawn_actor_from_class(self, cls, location, rotation):
            name = getattr(cls, "__unreal_name__", getattr(cls, "__name__", str(cls)))
            actor = Actor(class_name=str(name), label="%s_%d" % (name, len(state["actors"])))
            actor._location, actor._rotation = location, rotation
            state["actors"].append(actor)
            return actor

        def spawn_actor_from_object(self, asset, location, rotation):
            actor = Actor(class_name="StaticMeshActor", label="Mesh_%d" % len(state["actors"]))
            actor._location, actor._rotation = location, rotation
            state["actors"].append(actor)
            return actor

        def get_all_level_actors(self):
            return list(state["actors"])

        def destroy_actor(self, actor):
            state["actors"] = [a for a in state["actors"] if a is not actor]

    class LevelEditorSubsystem:
        def new_level(self, path):
            state["levels"].append(path)
            state["current_level"] = path
            return True

        def new_level_from_template(self, path, template):
            state["levels"].append(path)
            state["current_level"] = path
            return True

        def load_level(self, path):
            state["current_level"] = path
            return True

        def save_current_level(self):
            state["saved"].append(state["current_level"])
            return True

        def get_current_level(self):
            outer = types.SimpleNamespace(get_name=lambda: state["current_level"])
            return types.SimpleNamespace(get_outer=lambda: outer)

        def editor_play_simulate(self):
            state["pie"].append("start")

        def editor_request_end_play(self):
            state["pie"].append("stop")

    class SubobjectDataSubsystem:
        def k2_gather_subobject_data_for_blueprint(self, blueprint):
            return ["root_handle"]

        def add_new_subobject(self, params):
            return "handle", Text("")

        def rename_subobject(self, handle, name):
            return True

    def get_editor_subsystem(cls):
        return cls()

    def get_engine_subsystem(cls):
        return cls()

    module.EditorActorSubsystem = EditorActorSubsystem
    module.LevelEditorSubsystem = LevelEditorSubsystem
    module.SubobjectDataSubsystem = SubobjectDataSubsystem
    module.get_editor_subsystem = get_editor_subsystem
    module.get_engine_subsystem = get_engine_subsystem

    # ---- asset library
    class EditorAssetLibrary:
        @staticmethod
        def does_asset_exist(path):
            return path in state["assets"]

        @staticmethod
        def load_asset(path):
            return state["assets"].get(path)

        @staticmethod
        def save_asset(path, only_if_is_dirty=False):
            state["saved"].append(path)
            return True

        @staticmethod
        def save_directory(path, only_if_is_dirty=True, recursive=True):
            state["saved"].append(path)
            return True

        @staticmethod
        def list_assets(path, recursive=True, include_folder=False):
            return [p for p in state["assets"] if p.startswith(path)]

        @staticmethod
        def find_asset_data(path):
            class_name = state["asset_classes"].get(path, "Object")
            return types.SimpleNamespace(
                asset_class_path=types.SimpleNamespace(asset_name=class_name)
            )

        @staticmethod
        def does_directory_exist(path):
            return False

        @staticmethod
        def make_directory(path):
            return True

    class AssetTools:
        def create_asset(self, name, package_path, asset_class, factory):
            full = "%s/%s" % (package_path.rstrip("/"), name)
            if asset_class is Blueprint:
                asset = Blueprint(full, factory.get_editor_property("parent_class"))
            else:
                asset = asset_class()
            state["assets"][full] = asset
            state["asset_classes"][full] = getattr(asset_class, "__name__", "Object")
            return asset

        def import_asset_tasks(self, tasks):
            for task in tasks:
                filename = task.get_editor_property("filename")
                dest = task.get_editor_property("destination_path")
                stem = filename.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
                path = "%s/%s" % (dest.rstrip("/"), stem)
                task.set_editor_property("imported_object_paths", [path])
                state["assets"][path] = SoundWave() if filename.endswith(".wav") else FakeObject()
                state["asset_classes"][path] = "SoundWave" if filename.endswith(".wav") else "StaticMesh"
                state["imports"].append(path)

    module.EditorAssetLibrary = EditorAssetLibrary
    module.AssetToolsHelpers = types.SimpleNamespace(get_asset_tools=lambda: AssetTools())

    # ---- blueprint editor (API ufficiale UE 5.4+)
    def _vars(blueprint):
        return blueprint._props.setdefault("_variables", {})

    def add_member_variable(blueprint, member_name, variable_type):
        name = str(member_name)
        if name in _vars(blueprint):
            return False
        _vars(blueprint)[name] = {"type": variable_type, "replication": "NONE", "editable": True}
        return True

    def set_replication(blueprint, variable_name, replication):
        _vars(blueprint)[str(variable_name)]["replication"] = str(replication)

    def set_instance_editable(blueprint, variable_name, editable):
        _vars(blueprint)[str(variable_name)]["editable"] = bool(editable)

    module.BlueprintEditorLibrary = types.SimpleNamespace(
        compile_blueprint=lambda bp: state["saved"].append("compiled:" + bp.path),
        add_member_variable=add_member_variable,
        list_member_variable_names=lambda bp: [Name(n) for n in _vars(bp)],
        set_blueprint_variable_replication=set_replication,
        set_blueprint_variable_instance_editable=set_instance_editable,
    )
    module.BlueprintVariableReplication = types.SimpleNamespace(
        NONE="NONE", REPLICATED="REPLICATED", REP_NOTIFY="REP_NOTIFY"
    )
    module.GuidLibrary = types.SimpleNamespace(new_guid=lambda: Guid(str(uuid.uuid4())))

    # ---- varie
    def execute_console_command(world, cmd):
        state.setdefault("console", []).append(cmd)
        # Live Coding scrive l'esito nel log dell'editor: lo simuliamo.
        if cmd == "LiveCoding.Compile":
            log = tmp_path / "Saved" / "Logs" / "MyGame.log"
            with open(log, "a", encoding="utf-8") as handle:
                handle.write("[0]LogLiveCoding: Display: Starting Live Coding compile.\n")
                handle.write("[0]LogLiveCoding: Display: Live coding succeeded\n")

    module.SystemLibrary = types.SimpleNamespace(
        get_engine_version=lambda: "5.8.0-fake+++UE5",
        execute_console_command=execute_console_command,
    )

    class UnrealEditorSubsystem:
        def get_editor_world(self):
            return types.SimpleNamespace(get_name=lambda: state["current_level"])

        def get_game_world(self):
            return None

    module.UnrealEditorSubsystem = UnrealEditorSubsystem
    config_dir = tmp_path / "Config"
    log_dir = tmp_path / "Saved" / "Logs"
    config_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "MyGame.log").write_text(
        "LogInit: avvio\nLogBlueprint: Error: variabile mancante\nLogTemp: fine\n",
        encoding="utf-8",
    )
    module.Paths = types.SimpleNamespace(
        get_project_file_path=lambda: str(tmp_path / "MyGame.uproject"),
        project_content_dir=lambda: str(tmp_path / "Content"),
        project_config_dir=lambda: str(config_dir),
        project_log_dir=lambda: str(log_dir),
    )

    _defaults = {}

    def get_default_object(cls):
        key = getattr(cls, "__name__", str(cls))
        if key not in _defaults:
            _defaults[key] = FakeObject()
        return _defaults[key]

    module.get_default_object = get_default_object
    module.load_class = lambda outer, path: getattr(module, str(path).rsplit(".", 1)[-1], None)

    play_settings = FakeObject(PlayNumberOfClients=1, RunUnderOneProcess=True, PlayNetMode=0)
    state["assets"]["/Script/UnrealEd.Default__LevelEditorPlaySettings"] = play_settings
    module.load_object = lambda outer, path: state["assets"].get(path)

    return module

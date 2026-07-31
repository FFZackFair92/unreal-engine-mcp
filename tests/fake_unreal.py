"""Modulo `unreal` finto: permette di eseguire gli snippet generati dal server MCP
in locale, senza avere Unreal Engine installato.

Non riproduce il motore: riproduce la superficie di API usata da `ue_side.py`,
quel tanto che basta perché gli snippet vengano eseguiti davvero (e quindi ne
verifichiamo sintassi, nomi degli helper e flusso dei dati).
"""

from __future__ import annotations

import re
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

    def get_class(self):
        """Default ragionevole: sottoclassi con esigenze diverse (Actor,
        ActorComponent, Blueprint) lo sovrascrivono già."""
        return types.SimpleNamespace(get_name=lambda: type(self).__name__)

    def get_path_name(self):
        """Se l'oggetto è stato creato da AssetTools.create_asset conosce il
        proprio path reale (usato da mcp_bt_info per il Blackboard collegato
        a un Behavior Tree); altrimenti un placeholder plausibile."""
        return self._props.get("_path", "/Game/_Fake/%s" % type(self).__name__)

    def get_name(self):
        """L'ultimo segmento del path, come nel motore — usato dai messaggi di
        errore e dai nomi di transazione."""
        return self.get_path_name().rsplit("/", 1)[-1].split(".")[0]


class Vector:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = float(x), float(y), float(z)


class Rotator:
    def __init__(self, roll=0.0, pitch=0.0, yaw=0.0):
        self.roll, self.pitch, self.yaw = float(roll), float(pitch), float(yaw)


class IntPoint:
    def __init__(self, x=0, y=0):
        self.x, self.y = int(x), int(y)

    def __getitem__(self, indice):
        """Nel motore un IntPoint si indicizza come una coppia, ed è così che
        `mcp_pcg_graph_info` legge la posizione di un nodo (verificato dal
        vivo su UE 5.8)."""
        return (self.x, self.y)[indice]


class Name(str):
    pass


class Text(str):
    def is_empty(self):
        return len(self) == 0


class Guid(str):
    pass


class Transform(FakeObject):
    """Come nel motore: costruibile sia vuota (per `static_struct`) sia con
    la terna posizionale location/rotation/scale, che è la forma usata dal
    foliage e dal sequencer."""

    def __init__(self, location=None, rotation=None, scale=None, **kwargs):
        super().__init__(**kwargs)
        self.translation = location if location is not None else Vector()
        self.rotation = rotation if rotation is not None else Rotator()
        self.scale3d = scale if scale is not None else Vector(1.0, 1.0, 1.0)

    @staticmethod
    def static_struct():
        return "StructTransform"


class LinearColor(FakeObject):
    def __init__(self, r=0.0, g=0.0, b=0.0, a=1.0):
        super().__init__()
        self.r, self.g, self.b, self.a = float(r), float(g), float(b), float(a)

    @staticmethod
    def static_struct():
        return "StructLinearColor"


def _static_struct_for(label):
    return lambda: "Struct" + label


Vector.static_struct = staticmethod(_static_struct_for("Vector"))
Rotator.static_struct = staticmethod(_static_struct_for("Rotator"))


class ActorComponent(FakeObject):
    def __init__(self, name="Component", class_name="ActorComponent"):
        super().__init__()
        self._name = name
        self._class_name = class_name
        self.materials = {}
        # fase 6: fisica/collisione (UFUNCTION vere nel motore, non proprietà
        # dirette — vedi il commento in cima a ue_side.py::mcp_set_component_physics).
        self._simulate_physics = False
        self._collision_enabled = "NoCollision"
        self._collision_profile = "BlockAll"
        # fase 8: la replication di un componente. Nel motore è una UPROPERTY
        # *e* un attributo Python: `mcp_set_component_replication` fa
        # `hasattr(template, "replicates")` per distinguere un ActorComponent
        # da qualcosa che non replica, quindi qui serve entrambe le facce.
        self._props["replicates"] = False

    @property
    def replicates(self):
        return bool(self._props.get("replicates", False))

    def get_name(self):
        return self._name

    def get_class(self):
        return types.SimpleNamespace(get_name=lambda: self._class_name)

    def set_material(self, slot, material):
        self.materials[int(slot)] = material

    def set_simulate_physics(self, value):
        self._simulate_physics = bool(value)

    def is_simulating_physics(self):
        return self._simulate_physics

    def set_collision_enabled(self, value):
        self._collision_enabled = value

    def get_collision_enabled(self):
        return self._collision_enabled

    def set_collision_profile_name(self, value):
        self._collision_profile = str(value)

    def get_collision_profile_name(self):
        return self._collision_profile


class Actor(FakeObject):
    def __init__(self, class_name="Actor", label="Actor_0", class_path=None):
        super().__init__()
        self._class_name = class_name
        self._class_path = class_path
        self._label = label
        self._location = Vector()
        self._rotation = Rotator()
        self._scale = Vector(1, 1, 1)
        # Ogni attore ha almeno un mesh component, come StaticMeshActor.
        self._components = [
            ActorComponent("StaticMeshComponent0", "StaticMeshComponent")
        ]
        self._attach_parent = None
        self._attached = []

    def get_components_by_class(self, cls):
        return list(self._components)

    def get_component_by_class(self, cls):
        """Il primo componente della classe chiesta, o None.

        A differenza di `get_components_by_class` qui il filtro per classe
        conta davvero: `mcp_pcg_generate` distingue un attore con un
        PCGComponent da uno senza, e senza filtro il finto direbbe sempre di sì.
        """
        atteso = getattr(cls, "__unreal_name__", getattr(cls, "__name__", str(cls)))
        for componente in self._components:
            if componente.get_class().get_name() == atteso:
                return componente
        return None

    # API Unreal
    def get_actor_label(self):
        return self._label

    def set_actor_label(self, label):
        self._label = label

    def get_name(self):
        return self._label

    def get_class(self):
        """Come nel motore: la classe ha anche un path, non solo un nome.

        Per un attore nato da un Blueprint il path è `<pacchetto>.<Nome>_C`, ed
        è l'unico modo di risalire dall'istanza all'asset che l'ha generata —
        cioè quello che serve per non cancellare un Blueprint ancora in uso.
        """
        percorso = self._class_path or ("/Script/Engine." + self._class_name)
        return types.SimpleNamespace(
            get_name=lambda: self._class_name,
            get_path_name=lambda: percorso,
        )

    def get_path_name(self):
        return "/Game/Levels/L_Test.L_Test:PersistentLevel." + self._label

    # ---- gerarchia
    def attach_to_actor(self, parent, socket_name="", location_rule=None,
                        rotation_rule=None, scale_rule=None,
                        weld_simulated_bodies=False):
        if self._attach_parent is not None:
            self._attach_parent._attached.remove(self)
        self._attach_parent = parent
        parent._attached.append(self)
        self._attach_socket = socket_name

    def detach_from_actor(self, location_rule=None, rotation_rule=None, scale_rule=None):
        if self._attach_parent is not None:
            self._attach_parent._attached.remove(self)
            self._attach_parent = None

    def get_attach_parent_actor(self):
        return self._attach_parent

    def get_attached_actors(self):
        return list(self._attached)

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

    def get_actor_scale3d(self):
        return self._scale


class _FakeGeneratedClassPath(str):
    """Il path "<pacchetto>_C" restituito da `generated_class()`.

    È una stringa (serve a `mcp_resolve_class`/allo spawn, che controllano
    `isinstance(cls, str) and cls.endswith("_C")`), ma porta anche
    `get_path_name()` come il vero `UClass` del motore — serve alla fase 7
    (GAS), dove `_mcp_gas_attribute_owner_path` chiama
    `bp.generated_class().get_path_name()`.
    """

    def get_path_name(self):
        return str(self)


class Blueprint(FakeObject):
    def __init__(self, path, parent):
        super().__init__(new_variables=[])
        self.path = path
        self.parent = parent

    def generated_class(self):
        return _FakeGeneratedClassPath(self.path + "_C")

    def get_class(self):
        return types.SimpleNamespace(get_name=lambda: "Blueprint")


class FakeGraph:
    """Un EdGraph finto: solo il nome, come basta a mcp_bp_list_graphs."""

    def __init__(self, name):
        self._name = name

    def get_name(self):
        return self._name


class FakePin:
    """Un pin finto: quel poco che _mcp_bp_pin_dict legge davvero."""

    def __init__(self, name, direction, type_display):
        self._name = name
        self._direction = direction
        self._type_display = type_display

    def get_pin_name(self):
        return self._name

    def get_pin_direction(self):
        return types.SimpleNamespace(name=self._direction)

    def get_pin_type_display_string(self):
        return self._type_display


class FakeEventNode:
    """Un K2Node_Event finto, con un path plausibile e i suoi pin (tutti output)."""

    def __init__(self, blueprint, event_name, pins):
        self._blueprint = blueprint
        self._event_name = event_name
        self.pins = pins

    def get_path_name(self):
        return "%s:EventGraph.K2Node_Event_%s" % (self._blueprint.path, self._event_name)


# ----------------------------------------------- fase 12: albero dei widget
#
# Il WidgetTree è raggiungibile come subobject anche se la proprietà
# `WidgetTree` è protetta. Il finto riproduce la struttura *e* il limite che
# resta: `RootWidget` non è scrivibile, quindi un albero creato da zero è
# vuoto e nessun widget può esserci aggiunto.

#: Quali classi di widget sono pannelli, e con che slot.
UMG_PANNELLI = {
    "CanvasPanel": "CanvasPanelSlot",
    "VerticalBox": "VerticalBoxSlot",
    "HorizontalBox": "HorizontalBoxSlot",
    "Overlay": "OverlaySlot",
    "Border": "BorderSlot",
}


class Margin:
    def __init__(self, left=0.0, top=0.0, right=0.0, bottom=0.0):
        self.left, self.top = float(left), float(top)
        self.right, self.bottom = float(right), float(bottom)


class UmgSlot(FakeObject):
    """Lo slot di un widget dentro il suo pannello.

    I `set_<nome>` sono metodi veri nel motore, non proprietà: il finto li
    genera via `__getattr__` così `_mcp_umg_applica_slot` prende la stessa
    strada che prenderebbe sull'editor.
    """

    def __init__(self, class_name):
        super().__init__()
        self._class_name = class_name

    def get_class(self):
        return types.SimpleNamespace(get_name=lambda: self._class_name)

    def __getattr__(self, nome):
        if not nome.startswith("set_"):
            raise AttributeError(nome)
        chiave = nome[4:]
        # padding vuole un Margin, position/size un Vector2D: se arriva il
        # tipo sbagliato il motore solleva, e qui pure.
        def imposta(valore):
            if chiave == "padding" and not isinstance(valore, Margin):
                raise TypeError("Cannot nativize %s as Margin" % type(valore).__name__)
            if chiave in ("position", "size") and not isinstance(valore, Vector2D):
                raise TypeError("Cannot nativize %s as Vector2D" % type(valore).__name__)
            self._props[chiave] = valore

        return imposta


class Widget(FakeObject):
    def __init__(self, name, class_name):
        super().__init__()
        self._name = name
        self._class_name = class_name
        self._parent = None
        self._figli = []
        self.slot = None

    def get_name(self):
        return self._name

    def get_class(self):
        return types.SimpleNamespace(get_name=lambda: self._class_name)

    def get_parent(self):
        return self._parent

    def get_outer(self):
        return getattr(self, "_outer", None)

    # --- API dei pannelli (nel motore sta su PanelWidget)
    def add_child(self, widget):
        if self._class_name not in UMG_PANNELLI:
            return None
        widget._parent = self
        widget.slot = UmgSlot(UMG_PANNELLI[self._class_name])
        self._figli.append(widget)
        return widget.slot

    def get_all_children(self):
        return list(self._figli)

    def remove_child(self, widget):
        if widget not in self._figli:
            return False
        self._figli.remove(widget)
        widget._parent = None
        widget.slot = None
        return True

    def set_editor_property(self, name, value):
        # Le proprietà di testo sono FText: una stringa nuda viene rifiutata,
        # esattamente come nel motore, così il fallback su unreal.Text conta.
        if name in ("Text", "ToolTipText") and not isinstance(value, Text):
            raise TypeError("Cannot nativize str as Text")
        super().set_editor_property(name, value)


class PanelWidget(Widget):
    """Nel motore è la base dei contenitori; qui serve solo per l'isinstance
    che distingue un pannello da un widget foglia."""


class WidgetTree(FakeObject):
    pass


# ------------------------------------------- fase 11: authoring del grafo K2
#
# Il grafo Blueprint è scriptabile da UE 5.8 via `BlueprintGraphEditor`, che
# non tocca la proprietà protetta `EdGraph.Nodes` ma lavora sul grafo
# dall'esterno. Il finto riproduce le due trappole che contano:
# i *titoli* dei nodi sono localizzati mentre i *nomi oggetto* no, e
# `set_pin_value` accetta qualunque stringa senza validarla contro il tipo
# del pin (verificato dal vivo: "non_un_bool" finisce su un pin booleano).

#: Pin per funzione, come li dà il motore: (nome, direzione, tipo, default).
K2_FUNZIONI = {
    "/Script/Engine.KismetSystemLibrary:PrintString": [
        ("execute", "input", "Exec", ""),
        ("InString", "input", "Stringa", "Hello"),
        ("bPrintToScreen", "input", "Booleano", "true"),
        ("Duration", "input", "Float (precisione singola)", "2.000000"),
        ("then", "output", "Exec", ""),
    ],
    "/Script/Engine.GameplayStatics:GetPlayerPawn": [
        ("PlayerIndex", "input", "Intero", "0"),
        ("ReturnValue", "output", "Pawn Riferimento Oggetto", ""),
    ],
}

#: Nodi della palette, con il nome localizzato che ha l'editor italiano.
K2_PALETTE = {
    "Utilità|ControlloDiFlusso|Ramo": ("K2Node_IfThenElse", "Ramo"),
    "Utilità|Casting|CastToObject": ("K2Node_DynamicCast", "Cast To Object"),
    "Sviluppo|PrintString": ("K2Node_CallFunction", "PrintString"),
}

#: Eventi già presenti in un event graph appena creato.
K2_EVENTI_NEL_GRAFO = {
    "ReceiveBeginPlay": "Evento BeginPlay",
    "ReceiveTick": "Evento Tick",
}


class K2Pin(FakeObject):
    """Un pin del grafo K2. I collegamenti vivono qui, come nell'engine."""

    def __init__(self, name, direction, type_display, value="", node=None):
        super().__init__()
        self.name = name
        self.direction = direction
        self.type_display = type_display
        self.value = value
        self.node = node
        self.connected = []


class K2Node(FakeObject):
    def __init__(self, name, title, class_name, pins):
        super().__init__()
        self._name = name
        self._title = title
        self._class_name = class_name
        self.pins = []
        for nome, direzione, tipo, default in pins:
            self.pins.append(K2Pin(nome, direzione, tipo, default, self))
        self._pos = IntPoint(0, 0)

    def get_name(self):
        return self._name

    def get_node_title(self):
        """Localizzato, come nell'editor: mai usato come chiave dai tool."""
        return self._title

    def get_class(self):
        return types.SimpleNamespace(get_name=lambda: self._class_name)

    def list_all_pins(self):
        return list(self.pins)

    def list_input_pins(self):
        return [p for p in self.pins if p.direction == "input"]

    def list_output_pins(self):
        return [p for p in self.pins if p.direction == "output"]

    def get_node_pos(self):
        return self._pos

    def set_node_pos(self, pos):
        self._pos = pos


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


class WidgetBlueprintFactory(FakeObject):
    pass


class EditorUtilityWidgetBlueprintFactory(FakeObject):
    pass


class WidgetBlueprint(FakeObject):
    @property
    def path(self):
        """`compile_blueprint` legge `.path` su qualunque Blueprint: un Widget
        Blueprint non deriva da Blueprint nel finto, ma il path ce l'ha."""
        return self._props.get("_path", "/Game/_Fake/WidgetBlueprint")


class EditorUtilityWidgetBlueprint(FakeObject):
    pass


class AnimPoseFake:
    """La reference pose di uno Skeleton finto: solo ossa e socket."""

    def __init__(self, bones, sockets):
        self._bones = [Name(b) for b in bones]
        self._sockets = [Name(s) for s in sockets]

    def get_bone_names(self):
        return list(self._bones)

    def get_socket_names(self):
        return list(self._sockets)


class Skeleton(FakeObject):
    def __init__(self, bones=None, sockets=None):
        super().__init__()
        self._pose = AnimPoseFake(
            bones or ["root", "spine", "head"], sockets or []
        )

    def get_reference_pose(self):
        return self._pose


class AnimSequence(FakeObject):
    def __init__(self, skeleton=None, length=1.0, num_frames=30):
        super().__init__(skeleton=skeleton)
        self.length = length
        self.num_frames = num_frames
        self.notify_track_names = []
        self.notify_event_names = []
        self.sync_marker_names = []
        self.curve_names = []


class BlendParameter(FakeObject):
    pass


class BlendSample(FakeObject):
    pass


class BlendSpace1D(FakeObject):
    def __init__(self):
        super().__init__(
            BlendParameters=[BlendParameter(DisplayName="None", Min=0.0, Max=1.0, GridNum=4)],
            SampleData=[],
        )


class BlendSpaceFactoryNew(FakeObject):
    pass


class AnimMontage(FakeObject):
    def __init__(self):
        super().__init__()
        self.slot_names = ["DefaultSlot"]


class AnimMontageFactory(FakeObject):
    pass


class AnimBlueprint(FakeObject):
    pass


class AnimBlueprintFactory(FakeObject):
    pass


class NiagaraSystem(FakeObject):
    def __init__(self):
        super().__init__()
        self.emitters = []
        self.user_parameters = []


class NiagaraSystemFactoryNew(FakeObject):
    pass


class NiagaraEmitterInfoFake:
    """Come NiagaraMinimalEmitterInfo: attributi diretti, non get_editor_property."""

    def __init__(self, emitter_name, is_enabled=True, is_lightweight=False):
        self.emitter_name = emitter_name
        self.is_enabled = is_enabled
        self.is_lightweight = is_lightweight


class NiagaraUserParameterInfoFake:
    def __init__(self, parameter_name, type_name):
        self.parameter_name = parameter_name
        self.type_name = type_name


# --------------------------------------------------------------- fase 6: gameplay
#
# Blackboard/Behavior Tree sono UObject e struct normali (RootNode, Children,
# Decorators, Services scrivibili per davvero) — non un EdGraph come
# Blueprint/UMG/Niagara. Le proprietà "bindable da blackboard" sui task (es.
# BTTask_Wait.WaitTime) sono struct FValueOrBBKey_* riconosciuti dal nome del
# tipo Python (verificato dal vivo: `str(type(wt))` dà "<class
# 'ValueOrBBKey_Float'>"), non da static_struct().


class NavigationPath(FakeObject):
    def __init__(self, points, valid=True, partial=False):
        super().__init__()
        self.path_points = list(points)
        self._valid = valid
        self._partial = partial

    def is_valid(self):
        return self._valid

    def is_partial(self):
        return self._partial


class BlackboardEntry(FakeObject):
    pass


class BlackboardData(FakeObject):
    def __init__(self):
        super().__init__(Keys=[])


class BlackboardDataFactory(FakeObject):
    pass


class BehaviorTree(FakeObject):
    def __init__(self):
        super().__init__(BlackboardAsset=None, RootNode=None)


class BehaviorTreeFactory(FakeObject):
    pass


class BTCompositeChild(FakeObject):
    def __init__(self):
        super().__init__(ChildComposite=None, ChildTask=None, Decorators=[])


class BTCompositeNode(FakeObject):
    def __init__(self):
        super().__init__(Children=[], Services=[])


class BTComposite_Selector(BTCompositeNode):
    pass


class BTComposite_Sequence(BTCompositeNode):
    pass


class BTTaskNode(FakeObject):
    pass


class ValueOrBBKey_Float(FakeObject):
    def __init__(self, default_value=0.0, key=""):
        super().__init__(default_value=default_value, key=key)


class BTTask_Wait(BTTaskNode):
    def __init__(self):
        super().__init__(WaitTime=ValueOrBBKey_Float())


class BTDecorator_Blackboard(FakeObject):
    pass


class BTDecorator_Cooldown(FakeObject):
    pass


class BTService_DefaultFocus(FakeObject):
    pass


class EnvQuery(FakeObject):
    pass


class EnvironmentQueryFactory(FakeObject):
    pass


# --------------------------------------------------------------- fase 7: GAS
#
# Il muro vero è FGameplayModifierInfo.Attribute/.ModifierOp e
# FGameplayAttribute.AttributeName che rifiutano set_editor_property
# ("cannot be edited on instances" / read-only) — aggirato nel codice vero
# con `import_text` sull'intero struct. Qui il finto `import_text` fa un
# parsing minimo della stessa identica sintassi generata da
# `mcp_ge_add_modifier` (non è un parser UE generico, basta che riconosca il
# proprio output).


class ScalableFloat(FakeObject):
    def __init__(self, value=0.0):
        super().__init__(Value=float(value))


class GameplayEffectModifierMagnitude(FakeObject):
    def __init__(self, scalable_float=None):
        super().__init__(ScalableFloatMagnitude=scalable_float or ScalableFloat())


class _FakeClassRef:
    """Riferimento a una classe generata da Blueprint, quel poco che serve a
    `AttributeOwner` (get_path_name, get_class)."""

    def __init__(self, path):
        self._path = path

    def get_path_name(self):
        return self._path

    def get_class(self):
        nome = self._path.rsplit(".", 1)[-1]
        return types.SimpleNamespace(get_name=lambda: nome)


class GameplayAttribute(FakeObject):
    def __init__(self, attribute_name="", attribute_owner=None):
        super().__init__(AttributeName=attribute_name, AttributeOwner=attribute_owner)

    def import_text(self, testo):
        nome = re.search(r'AttributeName="([^"]*)"', testo)
        if nome:
            self._props["AttributeName"] = nome.group(1)
        proprietario = re.search(r'AttributeOwner="([^"]*)"', testo)
        if proprietario:
            self._props["AttributeOwner"] = _FakeClassRef(proprietario.group(1))
        return True

    def export_text(self):
        return '(AttributeName="%s")' % self._props.get("AttributeName", "")


class GameplayModifierInfo(FakeObject):
    def __init__(self):
        super().__init__(
            Attribute=GameplayAttribute(),
            ModifierOp="AddBase",
            ModifierMagnitude=GameplayEffectModifierMagnitude(),
        )

    def import_text(self, testo):
        op = re.search(r"ModifierOp=(\w+)", testo)
        if op:
            self._props["ModifierOp"] = op.group(1)
        valore = re.search(r"Value=(-?[0-9.]+)", testo)
        self._props["ModifierMagnitude"] = GameplayEffectModifierMagnitude(
            ScalableFloat(float(valore.group(1)) if valore else 0.0)
        )
        attr = GameplayAttribute()
        attr.import_text(testo)
        self._props["Attribute"] = attr
        return True

    def export_text(self):
        return "(ModifierOp=%s)" % self._props.get("ModifierOp", "AddBase")


class GameplayEffect(FakeObject):
    def __init__(self):
        super().__init__(
            DurationPolicy="Instant",
            Period=ScalableFloat(),
            Modifiers=[],
            GEComponents=[],
        )


class AttributeSet(FakeObject):
    pass


class GameplayAbility(FakeObject):
    pass


class GameplayAbilitiesBlueprintFactory(FakeObject):
    pass


class GameplayAbilityBlueprint(Blueprint):
    pass


class AssetImportTask(FakeObject):
    pass


class FbxImportUI(FakeObject):
    pass


class AddNewSubobjectParams(FakeObject):
    pass


class _SubobjectHandle:
    """Handle del SubobjectDataSubsystem: nel motore è opaco, qui tiene
    direttamente il componente a cui punta."""

    def __init__(self, componente):
        self.componente = componente


class _SubobjectData:
    """Il dato dietro un handle, da cui `SubobjectDataBlueprintFunctionLibrary.
    get_object` estrae il template del componente."""

    def __init__(self, componente):
        self.componente = componente


def _chiave_blueprint(blueprint):
    """Path del Blueprint, sia che arrivi l'oggetto sia che arrivi la stringa."""
    return getattr(blueprint, "path", None) or str(blueprint)


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


class Material(FakeObject):
    pass


class MaterialInstanceConstant(FakeObject):
    pass


class MaterialFactoryNew(FakeObject):
    pass


class MaterialInstanceConstantFactoryNew(FakeObject):
    pass


class MaterialExpressionTextureSampleParameter2D(FakeObject):
    pass


class MaterialExpressionScalarParameter(FakeObject):
    pass


class Texture2D(FakeObject):
    def __init__(self, size_x=64, size_y=64, **kwargs):
        super().__init__(**kwargs)
        self._size = (int(size_x), int(size_y))

    def blueprint_get_size_x(self):
        return self._size[0]

    def blueprint_get_size_y(self):
        return self._size[1]


class Vector2D:
    def __init__(self, x=0.0, y=0.0):
        self.x, self.y = float(x), float(y)


class TextureRenderTarget2D(FakeObject):
    def __init__(self, size_x=0, size_y=0, format="RTF_RGBA8"):
        super().__init__()
        self.size_x, self.size_y = int(size_x), int(size_y)
        self.format = format
        self.disegnato = None  # la texture disegnata sopra, per i test


class FakeCanvas:
    """Il Canvas che `begin_draw_canvas_to_render_target` restituisce: qui
    serve solo a registrare che il disegno è avvenuto sul render target giusto."""

    def __init__(self, render_target):
        self.render_target = render_target

    def draw_texture(self, texture, posizione, dimensione, uv_origine, uv_estensione):
        self.render_target.disegnato = texture


# ------------------------------------------------------------------ landscape
#
# Fase 9: `LandscapeProxy` è la classe base (i proxy di World Partition),
# `Landscape` l'attore principale — solo quest'ultimo ha target layer, edit
# layer e grass, e i tool devono degradare bene quando trovano solo un proxy.


class LandscapeComponent(FakeObject):
    pass


class LandscapeProxy(Actor):
    def __init__(self, label="LandscapeProxy_0", components=4, class_name="LandscapeStreamingProxy"):
        super().__init__(class_name=class_name, label=label)
        self._components = [LandscapeComponent() for _ in range(int(components))]
        self._props.update(
            {
                "landscape_material": None,
                "component_size_quads": 63,
                "subsection_size_quads": 63,
                "num_subsections": 1,
            }
        )
        self.heightmap_importati = []
        self.weightmap_importati = []
        self.heightmap_esportati = []

    def landscape_import_heightmap_from_render_target(self, render_target, from_rg_channel=False, edit_layer_index=0):
        if render_target is None or render_target.disegnato is None:
            return False
        self.heightmap_importati.append((render_target, bool(from_rg_channel)))
        return True

    def landscape_import_weightmap_from_render_target(self, render_target, layer_name, edit_layer_index=0):
        if render_target is None or render_target.disegnato is None:
            return False
        self.weightmap_importati.append((render_target, str(layer_name)))
        return True

    def landscape_export_heightmap_to_render_target(self, render_target, into_rg_channel=False, export_proxies=True):
        self.heightmap_esportati.append((render_target, bool(into_rg_channel)))
        return True


class Landscape(LandscapeProxy):
    def __init__(self, label="Landscape_0", components=4, target_layers=(), edit_layers=()):
        super().__init__(label=label, components=components, class_name="Landscape")
        self._target_layers = [str(n) for n in target_layers]
        self._edit_layers = [str(n) for n in edit_layers]
        self._grass = True

    def get_target_layer_names(self):
        return list(self._target_layers)

    def get_edit_layers_bp(self):
        return list(self._edit_layers)

    def get_grass_enabled(self):
        return self._grass

    def set_grass_enabled(self, valore):
        self._grass = bool(valore)


# ------------------------------------------------------------------------ PCG
#
# Fase 10: il grafo PCG è un grafo di dati veri (nodi + archi come oggetti),
# non un EdGraph protetto — perciò il finto lo riproduce per davvero, archi
# compresi, invece di limitarsi a registrare le chiamate.

#: Pin di ingresso/uscita per classe di settings, come li dà il motore.
PCG_PIN_LAYOUT = {
    "PCGSurfaceSamplerSettings": (["Surface", "Bounding Shape", "Seed"], ["Out"]),
    "PCGStaticMeshSpawnerSettings": (["In", "TargetActor", "Seed"], ["Out"]),
    "PCGCreatePointsGridSettings": ([], ["Out"]),
    "PCGDensityFilterSettings": (["In"], ["Out"]),
    "PCGGraphInputOutputSettings": (["In"], ["In"]),
}


class PCGSettings(FakeObject):
    def __init__(self, class_name="PCGSettings", **kwargs):
        super().__init__(**kwargs)
        self._class_name = class_name

    def get_class(self):
        return types.SimpleNamespace(get_name=lambda: self._class_name)


class _PCGPinProperties(FakeObject):
    def __init__(self, label):
        super().__init__(label=Name(label))


class _PCGPin(FakeObject):
    def __init__(self, label, node):
        super().__init__(properties=_PCGPinProperties(label), node=node)


class PCGEdge(FakeObject):
    """Nell'engine i nomi sono dal punto di vista dell'arco: `input_pin` è il
    pin da cui l'arco parte, cioè un pin di *output* del nodo a monte."""

    def __init__(self, pin_partenza, pin_arrivo):
        super().__init__(input_pin=pin_partenza, output_pin=pin_arrivo)


class PCGNode(FakeObject):
    def __init__(self, name, settings_class="PCGSettings"):
        super().__init__()
        self._name = name
        self._settings = PCGSettings(settings_class)
        ingressi, uscite = PCG_PIN_LAYOUT.get(settings_class, (["In"], ["Out"]))
        self.input_pins = [_PCGPin(etichetta, self) for etichetta in ingressi]
        self.output_pins = [_PCGPin(etichetta, self) for etichetta in uscite]
        self._position = IntPoint(0, 0)

    def get_name(self):
        return self._name

    def get_settings(self):
        return self._settings

    def set_node_position(self, position_x, position_y):
        self._position = IntPoint(position_x, position_y)

    def get_node_position(self):
        return self._position


class PCGGraph(FakeObject):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._nodi = []
        self._archi = []
        self._contatori = {}
        self._input = PCGNode("DefaultInputNode", "PCGGraphInputOutputSettings")
        self._output = PCGNode("DefaultOutputNode", "PCGGraphInputOutputSettings")

    # `nodes` esclude input e output, come nel motore.
    @property
    def nodes(self):
        return list(self._nodi)

    def get_input_node(self):
        return self._input

    def get_output_node(self):
        return self._output

    def add_node_of_type(self, settings_class):
        nome_classe = getattr(settings_class, "__unreal_name__", getattr(settings_class, "__name__", str(settings_class)))
        radice = re.sub(r"^PCG|Settings$", "", nome_classe)
        indice = self._contatori.get(radice, 0)
        self._contatori[radice] = indice + 1
        nodo = PCGNode("%s_%d" % (radice, indice), nome_classe)
        self._nodi.append(nodo)
        # Il motore restituisce (nodo, settings): l'helper regge entrambe le forme.
        return nodo, nodo.get_settings()

    def _pin(self, nodo, etichetta, uscita):
        for pin in nodo.output_pins if uscita else nodo.input_pins:
            if str(pin.get_editor_property("properties").get_editor_property("label")) == str(etichetta):
                return pin
        return None

    def add_edge(self, from_, from_pin_label, to, to_pin_label):
        partenza = self._pin(from_, from_pin_label, uscita=True)
        arrivo = self._pin(to, to_pin_label, uscita=False)
        if partenza is None or arrivo is None:
            return to
        self._archi.append(PCGEdge(partenza, arrivo))
        return to

    def remove_edge(self, from_, from_label, to, to_label):
        prima = len(self._archi)
        self._archi = [
            e
            for e in self._archi
            if not (
                e.get_editor_property("input_pin") is self._pin(from_, from_label, True)
                and e.get_editor_property("output_pin") is self._pin(to, to_label, False)
            )
        ]
        return len(self._archi) < prima

    def remove_node(self, node):
        self._nodi = [n for n in self._nodi if n is not node]
        self._archi = [
            e
            for e in self._archi
            if e.get_editor_property("input_pin").get_editor_property("node") is not node
            and e.get_editor_property("output_pin").get_editor_property("node") is not node
        ]

    def get_all_edges(self):
        return list(self._archi)


class PCGGraphFactory(FakeObject):
    pass


class PCGComponent(ActorComponent):
    def __init__(self, name="PCG Component"):
        super().__init__(name, "PCGComponent")
        self._grafo = None
        self.generazioni = []
        self.pulizie = []

    def set_graph(self, grafo):
        self._grafo = grafo

    def get_graph(self):
        return self._grafo

    def generate(self, force):
        self.generazioni.append(bool(force))

    def cleanup(self, remove_components):
        self.pulizie.append(bool(remove_components))


class PCGVolume(Actor):
    def __init__(self, label="PCGVolume_0"):
        super().__init__(class_name="PCGVolume", label=label)
        self._components = [PCGComponent()]


# ---------------------------------------------------------------- fase 14a: foliage


class StaticMesh(FakeObject):
    def __init__(self, path="/Engine/BasicShapes/Cube"):
        super().__init__(_path=path)


class FoliageType(FakeObject):
    """Le proprietà di default sono quelle che il motore mostra su un
    FoliageType appena creato: `mcp_set_foliage_property` le rilegge dopo aver
    scritto, e un dict vuoto le farebbe tornare tutte None."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._props.setdefault("mesh", None)
        self._props.setdefault("density", 100.0)
        self._props.setdefault("radius", 0.0)
        self._props.setdefault("random_yaw", True)
        self._props.setdefault("align_to_normal", True)


class FoliageType_InstancedStaticMesh(FoliageType):
    pass


class FoliageType_InstancedStaticMeshFactory(FakeObject):
    pass


class FoliageTypeObject(FakeObject):
    pass


class ProceduralFoliageSpawner(FakeObject):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._props.setdefault("foliage_types", [])
        self._props.setdefault("tile_size", 10000.0)


class ProceduralFoliageSpawnerFactory(FakeObject):
    pass


class FoliageInstancedStaticMeshComponent(ActorComponent):
    """Il componente che regge davvero le istanze.

    Non è un dettaglio del finto: è la via da cui passano `mcp_foliage_list`,
    `mcp_foliage_query` e `mcp_foliage_remove`, perché `FoliageStatistics`
    nell'editor risponde sempre 0 (verificato dal vivo). Qui le istanze sono
    una lista di Transform e gli indici si comportano come nel motore —
    `remove_instances` rinumera quelle che restano.
    """

    def __init__(self, mesh, name="FoliageInstancedStaticMeshComponent_0"):
        super().__init__(name, "FoliageInstancedStaticMeshComponent")
        self._props["static_mesh"] = mesh
        self.istanze = []

    def get_instance_count(self):
        return len(self.istanze)

    def get_instance_transform(self, index, world_space=False):
        return self.istanze[int(index)]

    def get_instances_overlapping_sphere(self, center, radius, sphere_in_world_space=True):
        trovati = []
        for indice, trasformata in enumerate(self.istanze):
            posizione = trasformata.translation
            distanza = (
                (posizione.x - center.x) ** 2
                + (posizione.y - center.y) ** 2
                + (posizione.z - center.z) ** 2
            ) ** 0.5
            if distanza <= float(radius):
                trovati.append(indice)
        return trovati

    def remove_instances(self, indices):
        for indice in sorted((int(i) for i in indices), reverse=True):
            del self.istanze[indice]
        return True


class InstancedFoliageActor(Actor):
    def __init__(self, label="InstancedFoliageActor0"):
        super().__init__(class_name="InstancedFoliageActor", label=label)
        self._components = []


class ProceduralFoliageComponent(ActorComponent):
    def __init__(self, name="ProceduralFoliage"):
        super().__init__(name, "ProceduralFoliageComponent")
        self._props["foliage_spawner"] = None


class ProceduralFoliageVolume(Actor):
    def __init__(self, label="ProceduralFoliageVolume"):
        super().__init__(class_name="ProceduralFoliageVolume", label=label)
        self._components = [ProceduralFoliageComponent()]


class HitResult(FakeObject):
    """Il risultato di un line trace, chiuso da tutte e due le parti come nel
    motore: né attributo Python né `get_editor_property`, solo `to_dict()` —
    ed è la trappola che ha rotto `mcp_foliage_scatter` alla prima stesura."""

    def __init__(self, blocking_hit, location):
        super().__init__()
        self._dati = {"blocking_hit": bool(blocking_hit), "location": location}

    def get_editor_property(self, name):
        raise Exception(
            "HitResult: Failed to find property '%s' for attribute '%s' on 'HitResult'"
            % (name, name)
        )

    def to_dict(self):
        return dict(self._dati)


# --------------------------------------------------------------- fase 14b: sequencer


class FrameRate(FakeObject):
    def __init__(self, numerator=30, denominator=1):
        super().__init__()
        self.numerator, self.denominator = int(numerator), int(denominator)


class FrameNumber(FakeObject):
    def __init__(self, value=0):
        super().__init__()
        self.value = int(value)


class _SeqFrame:
    def __init__(self, valore):
        self.frame_number = FrameNumber(valore)


class MovieSceneKeyInterpolation:
    AUTO = "AUTO"
    USER = "USER"
    BREAK = "BREAK"
    LINEAR = "LINEAR"
    CONSTANT = "CONSTANT"


class _SeqKey:
    def __init__(self, frame, valore, interpolazione=None):
        self._frame, self._valore, self.interpolazione = frame, valore, interpolazione

    def get_time(self):
        return _SeqFrame(self._frame)

    def get_value(self):
        return self._valore


class _SeqChannel:
    """Un canale di una sezione.

    Il suffisso numerico nel nome è deliberato e *variabile*: nel motore vero
    la stessa sezione ha dato `Location.Z_0` alla prima creazione e
    `Location.Z_3` alla seconda. Il finto lo riproduce con un contatore
    globale, così un tool che indirizzasse i canali per nome esatto fallirebbe
    qui come fallirebbe sull'editor.
    """

    _contatore = 0

    def __init__(self, nome_base):
        _SeqChannel._contatore += 1
        self._nome = "%s_%d" % (nome_base, _SeqChannel._contatore)
        self.chiavi = []

    def get_name(self):
        return self._nome

    def get_num_keys(self):
        return len(self.chiavi)

    def get_keys(self):
        return list(self.chiavi)

    def add_key(self, time, new_value, sub_frame=0.0, time_unit=None, interpolation=None):
        chiave = _SeqKey(int(time.value), new_value, interpolation)
        self.chiavi.append(chiave)
        self.chiavi.sort(key=lambda k: k._frame)
        return chiave


_SEQ_CANALI = {
    "MovieScene3DTransformTrack": [
        "Location.X",
        "Location.Y",
        "Location.Z",
        "Rotation.X",
        "Rotation.Y",
        "Rotation.Z",
        "Scale.X",
        "Scale.Y",
        "Scale.Z",
    ],
    "MovieSceneVisibilityTrack": ["MovieSceneScriptingBoolChannel"],
    "MovieSceneAudioTrack": [],
    "MovieSceneSkeletalAnimationTrack": [],
}


class _SeqSection:
    def __init__(self, classe_track):
        self._inizio, self._fine = None, None
        self._canali = [_SeqChannel(n) for n in _SEQ_CANALI.get(classe_track, [])]

    def set_range(self, start_frame, end_frame):
        self._inizio, self._fine = int(start_frame), int(end_frame)

    def has_start_frame(self):
        return self._inizio is not None

    def has_end_frame(self):
        return self._fine is not None

    def get_start_frame(self):
        return self._inizio

    def get_end_frame(self):
        return self._fine

    def get_all_channels(self):
        return list(self._canali)


class _SeqTrack:
    def __init__(self, classe):
        self._classe = classe
        self._sezioni = []

    def get_class(self):
        return types.SimpleNamespace(get_name=lambda: self._classe)

    def get_display_name(self):
        # Localizzato come nel motore: è il motivo per cui i tool indirizzano
        # le track per classe o per indice, mai per nome visualizzato.
        return {"MovieScene3DTransformTrack": "Trasforma"}.get(self._classe, self._classe)

    def add_section(self):
        sezione = _SeqSection(self._classe)
        self._sezioni.append(sezione)
        return sezione

    def get_sections(self):
        return list(self._sezioni)


class _SeqBinding:
    def __init__(self, sequenza, nome, classe, spawnable=False):
        self._sequenza, self._nome, self._classe = sequenza, nome, classe
        self.spawnable = spawnable
        self._tracks = []

    def is_valid(self):
        return True

    def get_display_name(self):
        return self._nome

    def get_id(self):
        return Guid(self._nome)

    def get_possessed_object_class(self):
        return types.SimpleNamespace(get_name=lambda: self._classe)

    def add_track(self, track_type):
        nome = getattr(track_type, "__unreal_name__", getattr(track_type, "__name__", str(track_type)))
        track = _SeqTrack(nome)
        self._tracks.append(track)
        return track

    def get_tracks(self):
        return list(self._tracks)

    def remove_track(self, track):
        self._tracks = [t for t in self._tracks if t is not track]

    def remove(self):
        self._sequenza._bindings = [b for b in self._sequenza._bindings if b is not self]


class LevelSequence(FakeObject):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._bindings = []
        self._ritmo = FrameRate(30, 1)
        self._inizio, self._fine = 0, 0

    def get_bindings(self):
        return list(self._bindings)

    def add_possessable(self, object_to_possess):
        legame = _SeqBinding(
            self,
            object_to_possess.get_actor_label(),
            object_to_possess.get_class().get_name(),
        )
        self._bindings.append(legame)
        return legame

    def add_spawnable_from_instance(self, object_to_spawn):
        legame = _SeqBinding(
            self,
            object_to_spawn.get_actor_label(),
            object_to_spawn.get_class().get_name(),
            spawnable=True,
        )
        self._bindings.append(legame)
        return legame

    def set_display_rate(self, display_rate):
        self._ritmo = display_rate

    def get_display_rate(self):
        return self._ritmo

    def set_playback_start(self, start_frame):
        self._inizio = int(start_frame)

    def set_playback_end(self, end_frame):
        self._fine = int(end_frame)

    def get_playback_start(self):
        return self._inizio

    def get_playback_end(self):
        return self._fine


class LevelSequenceFactoryNew(FakeObject):
    pass


class LandscapePlaceholder(Actor):
    """Quello che il motore spawna davvero se si prova a creare un Landscape
    da script: un attore vuoto, senza nessuno dei metodi di `ALandscape`."""

    def __init__(self, label="LandscapePlaceholder_0"):
        super().__init__(class_name="LandscapePlaceholder", label=label)
        self._components = []


class MaterialProperty:
    MP_BASE_COLOR = "MP_BASE_COLOR"
    MP_METALLIC = "MP_METALLIC"
    MP_SPECULAR = "MP_SPECULAR"
    MP_ROUGHNESS = "MP_ROUGHNESS"
    MP_EMISSIVE_COLOR = "MP_EMISSIVE_COLOR"
    MP_OPACITY = "MP_OPACITY"
    MP_OPACITY_MASK = "MP_OPACITY_MASK"
    MP_NORMAL = "MP_NORMAL"
    MP_AMBIENT_OCCLUSION = "MP_AMBIENT_OCCLUSION"


class MaterialSamplerType:
    SAMPLERTYPE_NORMAL = "SAMPLERTYPE_NORMAL"
    SAMPLERTYPE_COLOR = "SAMPLERTYPE_COLOR"


class ScopedEditorTransaction:
    """Registra le transazioni aperte, per verificare che le modifiche siano annullabili."""

    opened: list = []

    def __init__(self, description):
        self.description = description

    def __enter__(self):
        ScopedEditorTransaction.opened.append(self.description)
        return self

    def __exit__(self, *exc_info):
        return False


class _World:
    def __init__(self, state):
        self._state = state


# --------------------------------------------------------------- reflection
#
# `find_object`/`ClassIterator`/`StructIterator` non riproducono il motore:
# bastano una manciata di classi/struct con una gerarchia dichiarata a mano,
# quanto serve perché mcp_find_classes/mcp_find_structs/mcp_reflect_enum
# eseguano davvero (nomi degli helper, forma dei dati). Il comportamento vero
# della Python API di UE è stato verificato dal vivo su un editor 5.8, non
# indovinato: vedi il commento in cima a `ue_side.py::mcp_find_classes`.


class _ReflectedType:
    """Un Class o ScriptStruct finto: nome, percorso e gerarchia dichiarata."""

    def __init__(self, name, path, parents=()):
        self._name = name
        self._path = path
        self._ancestry = set(parents) | {name}

    def get_name(self):
        return self._name

    def get_path_name(self):
        return self._path

    def _deriva_da(self, nome_base):
        return nome_base in self._ancestry


class _ReflectionRegistry:
    """Registro nome/percorso -> _ReflectedType, condiviso da classi e struct."""

    def __init__(self):
        self._by_key = {}

    def register(self, name, path, parents=()):
        oggetto = _ReflectedType(name, path, parents)
        self._by_key[name] = oggetto
        self._by_key[path] = oggetto
        return oggetto

    def find(self, key):
        return self._by_key.get(key)

    def derived_from(self, base):
        visti = set()
        for oggetto in self._by_key.values():
            if id(oggetto) in visti:
                continue
            visti.add(id(oggetto))
            if oggetto._deriva_da(base.get_name()):
                yield oggetto


class ClassIterator:
    def __init__(self, base):
        self._base = base

    def __iter__(self):
        return _CLASS_REGISTRY.derived_from(self._base)


class StructIterator:
    def __init__(self, base):
        self._base = base

    def __iter__(self):
        return _STRUCT_REGISTRY.derived_from(self._base)


_CLASS_REGISTRY = _ReflectionRegistry()
_CLASS_REGISTRY.register("Actor", "/Script/Engine.Actor")
_CLASS_REGISTRY.register("Character", "/Script/Engine.Character", parents={"Actor"})
_CLASS_REGISTRY.register("Light", "/Script/Engine.Light", parents={"Actor"})
_CLASS_REGISTRY.register(
    "BP_Nemico_C",
    "/Game/Blueprints/BP_Nemico.BP_Nemico_C",
    parents={"Character", "Actor"},
)

_STRUCT_REGISTRY = _ReflectionRegistry()
_STRUCT_REGISTRY.register("Vector", "/Script/CoreUObject.Vector")
_STRUCT_REGISTRY.register(
    "Vector_NetQuantize", "/Script/Engine.Vector_NetQuantize", parents={"Vector"}
)
_STRUCT_REGISTRY.register("Transform", "/Script/CoreUObject.Transform")


def _find_object(outer, name, type=None, follow_redirectors=True):
    # Fase 12: il WidgetTree è un subobject del Widget Blueprint, e si prende
    # per nome — è così che i tool UMG aggirano la proprietà protetta.
    if name == "WidgetTree" and outer is not None and hasattr(outer, "_props"):
        return outer._props.setdefault("_widget_tree", WidgetTree())
    return _CLASS_REGISTRY.find(name) or _STRUCT_REGISTRY.find(name)


class _FakeEnumMember:
    def __init__(self, name, value):
        self.name = name
        self.value = value

    def get_display_name(self):
        # Il motore vero toglie il prefisso stile Hungarian (ECC_) e mette lo
        # spazio; qui basta un testo diverso dal `name` per accorgersi se il
        # tool usa la colonna sbagliata.
        return self.name.replace("_", " ").title()


class _FakeEnumType:
    """Riproduce solo l'iterazione di `unreal.CollisionChannel` e simili."""

    def __init__(self, members):
        self._members = [_FakeEnumMember(n, v) for n, v in members]

    def __iter__(self):
        return iter(self._members)


CollisionChannel = _FakeEnumType(
    [("ECC_WorldStatic", 0), ("ECC_WorldDynamic", 1), ("ECC_Pawn", 2)]
)


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
        "directories": set(),
        "deleted": [],
        "selected": [],
        "referencers": {},  # path -> chi lo referenzia, per il test di delete
        "bp_components": {},  # path del Blueprint -> componenti aggiunti (fase 8)
        "umg_oggetti": [],             # fase 12: (widget, outer)
        "textures_importate": [],      # fase 9
        "render_targets": [],          # fase 9
        "render_target_esportati": [], # fase 9
        # Una camera lontana dall'origine: è la situazione dei livelli veri, ed
        # è quella in cui spawnare a [0,0,0] produce un attore invisibile.
        # Rotator è (roll, pitch, yaw), come nel motore.
        "camera": {"location": Vector(12000, -3000, 800), "rotation": Rotator(0, -10, 45)},
    }

    module = types.ModuleType("unreal")
    module._state = state

    # ---- tipi base
    for cls in (
        Vector, Rotator, Name, Text, Guid, Transform, LinearColor, Actor, Blueprint,
        SoundCue, SoundWave, MetaSoundSource, BlueprintFactory, SoundCueFactoryNew,
        MetaSoundSourceFactory, AssetImportTask, FbxImportUI, AddNewSubobjectParams,
        EdGraphPinType, BPVariableDescription, LevelEditorPlaySettings, LifetimeCondition,
        PlayNetMode, ActorComponent, Material, MaterialInstanceConstant, MaterialFactoryNew,
        MaterialInstanceConstantFactoryNew, MaterialExpressionTextureSampleParameter2D,
        MaterialExpressionScalarParameter, Texture2D, MaterialProperty, MaterialSamplerType,
        ScopedEditorTransaction, WidgetBlueprintFactory, EditorUtilityWidgetBlueprintFactory,
        WidgetBlueprint, EditorUtilityWidgetBlueprint, IntPoint, Skeleton, AnimSequence,
        BlendParameter, BlendSample, BlendSpace1D, BlendSpaceFactoryNew, AnimMontage,
        AnimMontageFactory, AnimBlueprint, AnimBlueprintFactory, NiagaraSystem,
        NiagaraSystemFactoryNew, NavigationPath, BlackboardEntry, BlackboardData,
        BlackboardDataFactory, BehaviorTree, BehaviorTreeFactory, BTCompositeChild,
        BTCompositeNode, BTComposite_Selector, BTComposite_Sequence, BTTaskNode,
        ValueOrBBKey_Float, BTTask_Wait, BTDecorator_Blackboard, BTDecorator_Cooldown,
        BTService_DefaultFocus, EnvQuery, EnvironmentQueryFactory,
        ScalableFloat, GameplayEffectModifierMagnitude, GameplayAttribute,
        GameplayModifierInfo, GameplayEffect, AttributeSet, GameplayAbility,
        GameplayAbilitiesBlueprintFactory, GameplayAbilityBlueprint,
        Vector2D, TextureRenderTarget2D, LandscapeComponent, LandscapeProxy, Landscape,
        LandscapePlaceholder, PCGGraph, PCGGraphFactory, PCGNode, PCGSettings, PCGEdge,
        PCGComponent, PCGVolume, K2Node, K2Pin,
        StaticMesh, FoliageType, FoliageType_InstancedStaticMesh,
        FoliageType_InstancedStaticMeshFactory, FoliageTypeObject,
        FoliageInstancedStaticMeshComponent, InstancedFoliageActor,
        ProceduralFoliageSpawner, ProceduralFoliageSpawnerFactory,
        ProceduralFoliageComponent, ProceduralFoliageVolume, HitResult,
        LevelSequence, LevelSequenceFactoryNew, FrameRate, FrameNumber,
        MovieSceneKeyInterpolation,
        Widget, PanelWidget, WidgetTree, Margin, UmgSlot,
    ):
        setattr(module, cls.__name__, cls)

    ScopedEditorTransaction.opened = []

    # classi comuni risolte per nome (mcp_resolve_class usa hasattr(unreal, ref))
    for name in (
        "StaticMeshActor", "PlayerStart", "PointLight", "Character", "Pawn",
        "GameModeBase", "GameStateBase", "PlayerState", "AIController",
        "StaticMeshComponent", "BoxComponent", "SceneComponent",
        "UserWidget", "EditorUtilityWidget", "AnimInstance",
        "NavMeshBoundsVolume", "AIPerceptionComponent",
        "BlackboardKeyType_Object", "BlackboardKeyType_Class", "BlackboardKeyType_Bool",
        "BlackboardKeyType_Int", "BlackboardKeyType_Float", "BlackboardKeyType_String",
        "BlackboardKeyType_Name", "BlackboardKeyType_Vector", "BlackboardKeyType_Rotator",
        "BlackboardKeyType_Enum",
        "AssetTagsGameplayEffectComponent", "TargetTagRequirementsGameplayEffectComponent",
        "ChanceToApplyGameplayEffectComponent",
        # fase 10: in PCG il tipo di un nodo è la sua classe di settings
        "PCGSurfaceSamplerSettings", "PCGStaticMeshSpawnerSettings",
        "PCGCreatePointsGridSettings", "PCGDensityFilterSettings",
        # fase 14b: le track del sequencer sono risolte per nome di classe
        "MovieScene3DTransformTrack", "MovieSceneVisibilityTrack", "MovieSceneAudioTrack",
        "MovieSceneSkeletalAnimationTrack", "MovieSceneCameraCutTrack",
        "MovieSceneEventTrack", "MovieSceneFadeTrack", "MovieSceneFloatTrack",
    ):
        setattr(module, name, type(name, (FakeObject,), {"__unreal_name__": name}))

    # ---- subsystem attori
    class EditorActorSubsystem:
        def spawn_actor_from_class(self, cls, location, rotation):
            name = getattr(cls, "__unreal_name__", getattr(cls, "__name__", str(cls)))
            # Fase 9: spawnare un Landscape da script non crea un terreno, dà
            # un placeholder vuoto. Il finto lo riproduce perché è esattamente
            # la trappola in cui un agente cadrebbe.
            if name == "Landscape":
                placeholder = LandscapePlaceholder("LandscapePlaceholder_%d" % len(state["actors"]))
                placeholder._location, placeholder._rotation = location, rotation
                state["actors"].append(placeholder)
                return placeholder
            # Fase 10: un PCGVolume nasce già con il suo PCGComponent dentro.
            if name == "PCGVolume":
                volume = PCGVolume("PCGVolume_%d" % len(state["actors"]))
                volume._location, volume._rotation = location, rotation
                state["actors"].append(volume)
                return volume
            # Fase 14a: idem per il volume di foliage procedurale.
            if name == "ProceduralFoliageVolume":
                volume = ProceduralFoliageVolume(
                    "ProceduralFoliageVolume_%d" % len(state["actors"])
                )
                volume._location, volume._rotation = location, rotation
                state["actors"].append(volume)
                return volume
            # mcp_resolve_class restituisce la stringa "<pacchetto>_C" per i
            # Blueprint: da lì si ricava il path di classe come lo dà il motore.
            percorso = None
            if isinstance(cls, str) and cls.endswith("_C"):
                pacchetto = cls[:-2]
                percorso = "%s.%s_C" % (pacchetto, pacchetto.rsplit("/", 1)[-1])
                name = pacchetto.rsplit("/", 1)[-1] + "_C"
            actor = Actor(
                class_name=str(name),
                label="%s_%d" % (name, len(state["actors"])),
                class_path=percorso,
            )
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

        def get_selected_level_actors(self):
            return list(state["selected"])

        def set_selected_level_actors(self, actors):
            state["selected"] = list(actors)

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
        """Fase 8: i componenti aggiunti a un Blueprint restano davvero
        raggiungibili.

        Prima questo finto restituiva un solo handle fisso e scartava il
        componente creato: bastava per `mcp_add_component`, che non rilegge
        nulla, ma avrebbe fatto passare senza accorgersene tutto il percorso
        handle → dato → template su cui la fase 8 costruisce la replication e
        i default dei componenti.
        """

        def k2_gather_subobject_data_for_blueprint(self, blueprint):
            chiave = _chiave_blueprint(blueprint)
            return ["root_handle"] + [
                _SubobjectHandle(c) for c in state["bp_components"].get(chiave, [])
            ]

        def add_new_subobject(self, params):
            blueprint = params.get_editor_property("blueprint_context")
            cls = params.get_editor_property("new_class")
            nome = getattr(cls, "__unreal_name__", getattr(cls, "__name__", "ActorComponent"))
            componente = ActorComponent(nome + "_GEN_VARIABLE", nome)
            state["bp_components"].setdefault(_chiave_blueprint(blueprint), []).append(componente)
            return _SubobjectHandle(componente), Text("")

        def rename_subobject(self, handle, name):
            if isinstance(handle, _SubobjectHandle):
                handle.componente._name = "%s_GEN_VARIABLE" % name
            return True

        def k2_find_subobject_data_from_handle(self, handle):
            if not isinstance(handle, _SubobjectHandle):
                return None
            return _SubobjectData(handle.componente)

    class SubobjectDataBlueprintFunctionLibrary:
        @staticmethod
        def get_object(data):
            return None if data is None else data.componente

    module.SubobjectDataBlueprintFunctionLibrary = SubobjectDataBlueprintFunctionLibrary

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
            return path.rstrip("/") in state["directories"]

        @staticmethod
        def make_directory(path):
            state["directories"].add(path.rstrip("/"))
            return True

        @staticmethod
        def delete_asset(path):
            state["assets"].pop(path, None)
            state["asset_classes"].pop(path, None)
            state["deleted"].append(path)
            return True

        @staticmethod
        def delete_directory(path):
            radice = path.rstrip("/") + "/"
            for chiave in [k for k in state["assets"] if k.startswith(radice)]:
                state["assets"].pop(chiave, None)
                state["asset_classes"].pop(chiave, None)
            state["directories"].discard(path.rstrip("/"))
            state["deleted"].append(path)
            return True

        @staticmethod
        def find_package_referencers_for_asset(path, load_assets_to_confirm=False):
            return list(state["referencers"].get(path, []))

        @staticmethod
        def rename_asset(source, destination):
            if source not in state["assets"]:
                return False
            state["assets"][destination] = state["assets"].pop(source)
            if source in state["asset_classes"]:
                state["asset_classes"][destination] = state["asset_classes"].pop(source)
            return True

        @staticmethod
        def duplicate_asset(source, destination):
            if source not in state["assets"]:
                return None
            state["assets"][destination] = state["assets"][source]
            state["asset_classes"][destination] = state["asset_classes"].get(source, "Object")
            return state["assets"][destination]

    class AssetTools:
        def create_asset(self, name, package_path, asset_class, factory):
            full = "%s/%s" % (package_path.rstrip("/"), name)
            # GameplayAbilityBlueprint (fase 7) è un sottotipo di Blueprint con
            # un asset dedicato nel motore vero: si comporta come Blueprint
            # anche qui (path + parent + generated_class()).
            if isinstance(asset_class, type) and issubclass(asset_class, Blueprint):
                parent = factory.get_editor_property("parent_class")
                asset = asset_class(full, parent)
                # Il CDO va costruito con la "forma" del parent (fase 7: un
                # GameplayEffect finto ha Modifiers/GEComponents/Period di
                # default, un FakeObject nudo no) — registrato qui perché
                # generated_class() resta una stringa "<path>_C" per
                # compatibilità con lo spawn (vedi sotto), non un vero tipo.
                if isinstance(parent, type):
                    state.setdefault("bp_parent_class", {})[asset.generated_class()] = parent
            else:
                asset = asset_class()
            if hasattr(asset, "_props"):
                asset._props.setdefault("_path", full)
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

    def reparent_blueprint(blueprint, new_parent):
        blueprint.parent = new_parent
        state.setdefault("reparented", []).append((blueprint.path, str(new_parent)))
        # Unreal assorbe le variabili che coincidono con una UPROPERTY del padre:
        # qui simuliamo l'assorbimento di quelle che iniziano per "Parent".
        for nome in [n for n in _vars(blueprint) if n.startswith("Parent")]:
            del _vars(blueprint)[nome]

    def remove_unused_variables(blueprint):
        state.setdefault("cleaned", []).append(blueprint.path)

    # ---- grafo blueprint (fase 3): solo il sottoinsieme reale verificato dal
    # vivo su UE 5.8 — eventi ereditati overridabili (catalogo minimo, non
    # tutti quelli di Actor) e grafi funzione vuoti. Un nodo evento ha solo
    # pin di output, mai input: è la scoperta che ha fatto scartare un tool
    # di connessione pin, vedi il commento in cima a `ue_side.py`.
    _EVENTI_OVERRIDABILI = {
        "ReceiveBeginPlay": [("then", "EGPD_OUTPUT", "Exec")],
        "ReceiveTick": [
            ("DeltaSeconds", "EGPD_OUTPUT", "Float"),
            ("then", "EGPD_OUTPUT", "Exec"),
        ],
        "ReceiveEndPlay": [
            ("EndPlayReason", "EGPD_OUTPUT", "Byte"),
            ("then", "EGPD_OUTPUT", "Exec"),
        ],
    }

    def _grafi(blueprint):
        return blueprint._props.setdefault("_graphs", ["EventGraph", "UserConstructionScript"])

    def _eventi_implementati(blueprint):
        return blueprint._props.setdefault("_implemented_events", set())

    def list_graphs(blueprint):
        return [FakeGraph(nome) for nome in _grafi(blueprint)]

    def list_events(blueprint):
        implementati = _eventi_implementati(blueprint)
        return [
            FakeObject(name=Name(nome), is_implemented=(nome in implementati))
            for nome in _EVENTI_OVERRIDABILI
        ]

    def add_event_override(blueprint, event_name, position):
        nome = str(event_name)
        if nome not in _EVENTI_OVERRIDABILI:
            return None
        _eventi_implementati(blueprint).add(nome)
        pins = [FakePin(n, d, t) for n, d, t in _EVENTI_OVERRIDABILI[nome]]
        return FakeEventNode(blueprint, nome, pins)

    def add_function_graph(blueprint, func_name):
        _grafi(blueprint).append(str(func_name))
        return FakeGraph(str(func_name))

    def list_all_pins(node):
        return node.pins

    module.BlueprintEditorLibrary = types.SimpleNamespace(
        compile_blueprint=lambda bp: state["saved"].append("compiled:" + bp.path),
        add_member_variable=add_member_variable,
        list_member_variable_names=lambda bp: [Name(n) for n in _vars(bp)],
        set_blueprint_variable_replication=set_replication,
        set_blueprint_variable_instance_editable=set_instance_editable,
        reparent_blueprint=reparent_blueprint,
        remove_unused_variables=remove_unused_variables,
        list_graphs=list_graphs,
        list_events=list_events,
        add_event_override=add_event_override,
        add_function_graph=add_function_graph,
        list_all_pins=list_all_pins,
    )

    # ---- fase 11: authoring del grafo K2
    module.EdGraphPinDirection = types.SimpleNamespace(
        EGPD_INPUT="input", EGPD_OUTPUT="output"
    )

    def _nodi_del_grafo(blueprint, graph_name):
        grafi = blueprint._props.setdefault("_k2_nodes", {})
        if graph_name not in grafi:
            iniziali = []
            if graph_name == "EventGraph":
                for indice, (membro, titolo) in enumerate(K2_EVENTI_NEL_GRAFO.items()):
                    nodo = K2Node(
                        "K2Node_Event_%d" % indice, titolo, "K2Node_Event",
                        [("then", "output", "Exec", "")],
                    )
                    nodo._evento = membro
                    iniziali.append(nodo)
            grafi[graph_name] = iniziali
        return grafi[graph_name]

    def _nome_nuovo(blueprint, class_name):
        contatori = blueprint._props.setdefault("_k2_counters", {})
        indice = contatori.get(class_name, 0)
        contatori[class_name] = indice + 1
        return "%s_%d" % (class_name, indice)

    class _GraphEditor(FakeObject):
        def __init__(self, blueprint, graph_name):
            super().__init__()
            self.blueprint = blueprint
            self.graph_name = graph_name

        def get_graph(self):
            return FakeGraph(self.graph_name)

        def _aggiungi(self, class_name, title, pins):
            nodo = K2Node(_nome_nuovo(self.blueprint, class_name), title, class_name, pins)
            _nodi_del_grafo(self.blueprint, self.graph_name).append(nodo)
            return nodo

        def list_all_nodes(self):
            return list(_nodi_del_grafo(self.blueprint, self.graph_name))

        def find_event_node(self, event_name):
            for nodo in _nodi_del_grafo(self.blueprint, self.graph_name):
                if getattr(nodo, "_evento", None) == str(event_name):
                    return nodo
            return None

        def add_call_function_node(self, function_path):
            pins = K2_FUNZIONI.get(str(function_path))
            if pins is None:
                return None
            return self._aggiungi("K2Node_CallFunction", str(function_path).rsplit(":", 1)[-1], pins)

        def add_branch_node(self):
            return self._aggiungi(
                "K2Node_IfThenElse", "Ramo",
                [
                    ("execute", "input", "Exec", ""),
                    ("Condition", "input", "Booleano", "true"),
                    ("then", "output", "Exec", ""),
                    ("else", "output", "Exec", ""),
                ],
            )

        def add_custom_event_node(self, event_name):
            if self.graph_name != "EventGraph":
                return None
            return self._aggiungi(
                "K2Node_CustomEvent", str(event_name), [("then", "output", "Exec", "")]
            )

        def _nodo_variabile(self, member_name, class_name, prefisso, direzione):
            if str(member_name) not in _vars(self.blueprint):
                return None
            return self._aggiungi(
                class_name, "%s %s" % (prefisso, member_name),
                [(str(member_name), direzione, "Booleano", "false")],
            )

        def add_get_member_variable_node(self, member_name, class_path=""):
            return self._nodo_variabile(member_name, "K2Node_VariableGet", "Get", "output")

        def add_set_member_variable_node(self, member_name, class_path=""):
            return self._nodo_variabile(member_name, "K2Node_VariableSet", "Set", "input")

        def create_node_from_name(self, node_with_category, location, context_pins, declaring_class=None):
            voce = K2_PALETTE.get(str(node_with_category))
            if voce is None:
                return None
            class_name, titolo = voce
            if class_name == "K2Node_IfThenElse":
                return self.add_branch_node()
            return self._aggiungi(class_name, titolo, [("execute", "input", "Exec", "")])

        def list_available_nodes(self, context_pins):
            return sorted(K2_PALETTE)

        def remove_nodes(self, nodes):
            rimasti = [n for n in _nodi_del_grafo(self.blueprint, self.graph_name) if n not in nodes]
            for nodo in nodes:
                for pin in nodo.pins:
                    for altro in list(pin.connected):
                        altro.connected = [p for p in altro.connected if p is not pin]
                    pin.connected = []
            self.blueprint._props["_k2_nodes"][self.graph_name] = rimasti

        def list_nodes_with_errors(self):
            return []

        def list_nodes_with_warnings(self):
            return []

    def get_graph_editor_by_name(blueprint, graph_name):
        if str(graph_name) not in _grafi(blueprint):
            return None
        return _GraphEditor(blueprint, str(graph_name))

    module.BlueprintGraphEditor = types.SimpleNamespace(
        get_graph_editor_by_name=get_graph_editor_by_name,
        get_graph_editor=lambda graph: _GraphEditor(graph, graph.get_name()),
    )

    def _sono_compatibili(a, b):
        return a.direction == "output" and b.direction == "input" and a.type_display == b.type_display

    def _stacca(pin):
        for altro in list(pin.connected):
            altro.connected = [p for p in altro.connected if p is not pin]
        pin.connected = []

    module.BlueprintGraphPinLibrary = types.SimpleNamespace(
        get_pin_name=lambda pin: Name(pin.name),
        get_pin_direction=lambda pin: pin.direction,
        get_pin_type_display_string=lambda pin: pin.type_display,
        get_pin_value=lambda pin: pin.value,
        # Come nel motore: nessuna validazione contro il tipo del pin.
        set_pin_value=lambda pin, value: (setattr(pin, "value", str(value)), True)[1],
        get_owning_node=lambda pin: pin.node,
        list_connected_pins=lambda pin: list(pin.connected),
        can_create_connection=_sono_compatibili,
        try_create_connection=lambda a, b: (
            a.connected.append(b), b.connected.append(a), True
        )[2],
        break_pin_links=_stacca,
    )

    # ---- materiali
    def create_material_expression(materiale, cls, x, y):
        nodo = cls()
        materiale._props.setdefault("_expressions", []).append(nodo)
        return nodo

    def connect_material_property(nodo, pin, proprieta):
        nodo.set_editor_property("_connected_to", proprieta)
        state.setdefault("material_links", []).append(str(proprieta))
        return True

    module.MaterialEditingLibrary = types.SimpleNamespace(
        create_material_expression=create_material_expression,
        connect_material_property=connect_material_property,
        recompile_material=lambda m: state["saved"].append("recompiled-material"),
        set_material_instance_parent=lambda inst, parent: inst.set_editor_property("parent", parent),
        set_material_instance_scalar_parameter_value=lambda i, n, v: i.set_editor_property(str(n), v),
        set_material_instance_vector_parameter_value=lambda i, n, v: i.set_editor_property(str(n), v),
        set_material_instance_texture_parameter_value=lambda i, n, v: i.set_editor_property(str(n), v),
        set_material_instance_static_switch_parameter_value=lambda i, n, v: i.set_editor_property(str(n), v),
    )

    # ---- animazione (fase 4): AnimationLibrary legge i dati messi sull'oggetto
    # AnimSequence finto direttamente (notify_track_names, curve_names, ...),
    # popolati dal test prima della chiamata.
    module.RawCurveTrackTypes = types.SimpleNamespace(RCT_FLOAT="RCT_FLOAT", RCT_TRANSFORM="RCT_TRANSFORM")
    module.AnimationLibrary = types.SimpleNamespace(
        get_sequence_length=lambda seq: seq.length,
        get_num_frames=lambda seq: seq.num_frames,
        get_animation_notify_track_names=lambda seq: [Name(n) for n in seq.notify_track_names],
        get_animation_notify_event_names=lambda seq: [Name(n) for n in seq.notify_event_names],
        get_unique_marker_names=lambda seq: [Name(n) for n in seq.sync_marker_names],
        get_animation_curve_names=lambda seq, track_type: [Name(n) for n in seq.curve_names],
        get_montage_slot_names=lambda montage: [Name(n) for n in montage.slot_names],
    )

    # ---- Niagara (fase 5): stessi dati messi sull'oggetto NiagaraSystem
    # finto (emitters, user_parameters), popolati dal test.
    module.NiagaraFunctionLibrary = types.SimpleNamespace(
        get_all_emitters=lambda sys_: list(sys_.emitters),
        get_all_user_parameters=lambda sys_: list(sys_.user_parameters),
    )

    # ---- screenshot
    def take_high_res_screenshot(width, height, filename):
        state.setdefault("screenshots", []).append(filename)
        import os as _os

        _os.makedirs(_os.path.dirname(filename), exist_ok=True)
        with open(filename, "wb") as handle:
            handle.write(b"\x89PNG fake")

    module.AutomationLibrary = types.SimpleNamespace(
        take_high_res_screenshot=take_high_res_screenshot
    )
    module.BlueprintVariableReplication = types.SimpleNamespace(
        NONE="NONE", REPLICATED="REPLICATED", REP_NOTIFY="REP_NOTIFY"
    )
    module.GuidLibrary = types.SimpleNamespace(new_guid=lambda: Guid(str(uuid.uuid4())))

    def _forward(rotatore):
        """Vettore in avanti, calcolato come lo calcola il motore."""
        import math as _math

        pitch = _math.radians(rotatore.pitch)
        yaw = _math.radians(rotatore.yaw)
        return Vector(
            _math.cos(pitch) * _math.cos(yaw),
            _math.cos(pitch) * _math.sin(yaw),
            _math.sin(pitch),
        )

    module.MathLibrary = types.SimpleNamespace(get_forward_vector=_forward)
    module.AttachmentRule = types.SimpleNamespace(
        KEEP_RELATIVE="KEEP_RELATIVE", KEEP_WORLD="KEEP_WORLD", SNAP_TO_TARGET="SNAP_TO_TARGET"
    )
    module.DetachmentRule = types.SimpleNamespace(
        KEEP_RELATIVE="KEEP_RELATIVE", KEEP_WORLD="KEEP_WORLD"
    )

    # ---- varie
    def execute_console_command(world, cmd):
        state.setdefault("console", []).append(cmd)
        # I comandi di console non restituiscono niente: scrivono nel log. Il
        # finto ne stampa una riga, che è quello che mcp_console_command rilegge.
        log = tmp_path / "Saved" / "Logs" / "MyGame.log"
        with open(log, "a", encoding="utf-8") as handle:
            handle.write("[0]LogConsoleResponse: eseguito %s\n" % cmd)
        # Live Coding scrive l'esito nel log dell'editor: lo simuliamo.
        if cmd == "LiveCoding.Compile":
            log = tmp_path / "Saved" / "Logs" / "MyGame.log"
            with open(log, "a", encoding="utf-8") as handle:
                handle.write("[0]LogLiveCoding: Display: Starting Live Coding compile.\n")
                handle.write("[0]LogLiveCoding: Display: Live coding succeeded\n")

    def line_trace_single(
        world_context_object,
        start,
        end,
        trace_channel,
        trace_complex,
        actors_to_ignore,
        draw_debug_type,
        ignore_self,
        trace_color=None,
        trace_hit_color=None,
        draw_time=0.0,
    ):
        """Un pavimento a z=0 sotto tutto: basta a `mcp_foliage_scatter`.

        Come nel motore, restituisce None quando non colpisce niente — qui
        quando il segmento non attraversa lo zero.
        """
        if start.z >= 0.0 >= end.z:
            return HitResult(True, Vector(start.x, start.y, 0.0))
        return None

    module.SystemLibrary = types.SimpleNamespace(
        get_engine_version=lambda: "5.8.0-fake+++UE5",
        execute_console_command=execute_console_command,
        line_trace_single=line_trace_single,
    )
    module.TraceTypeQuery = types.SimpleNamespace(TRACE_TYPE_QUERY1="TraceTypeQuery1")
    module.DrawDebugTrace = types.SimpleNamespace(NONE="None")

    # ---- fase 14a: foliage
    #
    # `InstancedFoliageActor.add_instances` è una UFUNCTION *statica* nel
    # motore: qui è una staticmethod che crea l'attore del livello alla prima
    # chiamata, come fa Unreal.
    def _foliage_actor():
        for attore in state["actors"]:
            if isinstance(attore, InstancedFoliageActor):
                return attore
        attore = InstancedFoliageActor()
        state["actors"].append(attore)
        return attore

    def _foliage_component(mesh):
        attore = _foliage_actor()
        for componente in attore._components:
            if componente.get_editor_property("static_mesh") is mesh:
                return componente
        componente = FoliageInstancedStaticMeshComponent(
            mesh, "FoliageInstancedStaticMeshComponent_%d" % len(attore._components)
        )
        attore._components.append(componente)
        return componente

    def _foliage_add_instances(world_context_object, foliage_type, transforms):
        mesh = foliage_type.get_editor_property("mesh")
        _foliage_component(mesh).istanze.extend(transforms)

    def _foliage_remove_all(world_context_object, foliage_type):
        mesh = foliage_type.get_editor_property("mesh")
        _foliage_component(mesh).istanze = []

    InstancedFoliageActor.add_instances = staticmethod(_foliage_add_instances)
    InstancedFoliageActor.remove_all_instances = staticmethod(_foliage_remove_all)

    module.ProceduralFoliageEditorLibrary = types.SimpleNamespace(
        resimulate_procedural_foliage_volumes=lambda volumes: state.setdefault(
            "foliage_simulated", []
        ).extend(v.get_actor_label() for v in volumes),
        clear_procedural_foliage_volumes=lambda volumes: state.setdefault(
            "foliage_cleared", []
        ).extend(v.get_actor_label() for v in volumes),
    )

    # ---- fase 14b: la finestra del Sequencer
    module.LevelSequenceEditorBlueprintLibrary = types.SimpleNamespace(
        open_level_sequence=lambda sequence: state.setdefault("sequencer_open", []).append(
            sequence.get_path_name()
        ),
        close_level_sequence=lambda: state.setdefault("sequencer_open", []).append(None),
    )

    class UnrealEditorSubsystem:
        def get_editor_world(self):
            return types.SimpleNamespace(get_name=lambda: state["current_level"])

        def get_game_world(self):
            return None

        def get_level_viewport_camera_info(self):
            return state["camera"]["location"], state["camera"]["rotation"]

        def set_level_viewport_camera_info(self, location, rotation):
            state["camera"] = {"location": location, "rotation": rotation}

    module.UnrealEditorSubsystem = UnrealEditorSubsystem
    config_dir = tmp_path / "Config"
    log_dir = tmp_path / "Saved" / "Logs"
    config_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "MyGame.log").write_text(
        "LogInit: avvio\nLogBlueprint: Error: variabile mancante\nLogTemp: fine\n",
        encoding="utf-8",
    )
    def _relativo(assoluto):
        """Come li restituisce il motore vero: relativi ai binari del motore.

        Il finto li dava assoluti, ed è il motivo per cui uno screenshot che
        tornava con `captured: false` su Unreal vero passava qui senza problemi:
        il percorso non era sbagliato nel test, lo era solo nella realtà.
        """
        return "../../../../.." + str(assoluto)

    module.Paths = types.SimpleNamespace(
        get_project_file_path=lambda: _relativo(tmp_path / "MyGame.uproject"),
        project_content_dir=lambda: _relativo(tmp_path / "Content"),
        project_config_dir=lambda: _relativo(config_dir),
        project_log_dir=lambda: _relativo(log_dir),
        project_saved_dir=lambda: _relativo(tmp_path / "Saved"),
        # Il motore la espone per riportarli in forma assoluta: qui basta
        # togliere il prefisso che abbiamo aggiunto sopra.
        convert_relative_path_to_full=lambda p: str(p).replace("../../../../..", "", 1),
    )

    _defaults = {}

    #: Default di rete di `AActor` nel motore: `mcp_net_info` li rilegge tutti,
    #: e senza valori iniziali plausibili il finto direbbe "non leggibile" dove
    #: l'editor vero risponde con un numero (fase 8).
    _NET_DEFAULTS = {
        "net_update_frequency": 100.0,
        "min_net_update_frequency": 2.0,
        "net_priority": 1.0,
        "net_cull_distance_squared": 225000000.0,
        "only_relevant_to_owner": False,
        "net_use_owner_relevancy": False,
        "net_load_on_client": True,
        "net_dormancy": "DORM_AWAKE",
        "replicates": False,
        "replicate_movement": False,
        "always_relevant": False,
    }

    def get_default_object(cls):
        key = getattr(cls, "__name__", str(cls))
        if key not in _defaults:
            parent = state.get("bp_parent_class", {}).get(key)
            cdo = parent() if isinstance(parent, type) else FakeObject()
            for nome, valore in _NET_DEFAULTS.items():
                cdo._props.setdefault(nome, valore)
            _defaults[key] = cdo
        return _defaults[key]

    module.get_default_object = get_default_object
    module.load_class = lambda outer, path: getattr(module, str(path).rsplit(".", 1)[-1], None)

    play_settings = FakeObject(PlayNumberOfClients=1, RunUnderOneProcess=True, PlayNetMode=0)
    state["assets"]["/Script/UnrealEd.Default__LevelEditorPlaySettings"] = play_settings
    module.load_object = lambda outer, path: state["assets"].get(path)

    module.find_object = _find_object
    module.ClassIterator = ClassIterator
    module.StructIterator = StructIterator
    module.CollisionChannel = CollisionChannel

    # ---- fase 12: le classi concrete dei widget, risolte per nome da
    # mcp_resolve_class. I pannelli derivano da PanelWidget, così l'isinstance
    # che distingue un contenitore da una foglia dice la verità.
    def _classe_widget(nome, base):
        def costruisci(self, name=None):
            base.__init__(self, name or "%s_0" % nome, nome)

        return type(nome, (base,), {"__init__": costruisci, "__unreal_name__": nome})

    for nome_widget in ("CanvasPanel", "VerticalBox", "HorizontalBox", "Overlay", "Border"):
        setattr(module, nome_widget, _classe_widget(nome_widget, PanelWidget))
    for nome_widget in ("TextBlock", "Button", "Image", "ProgressBar", "EditableTextBox"):
        setattr(module, nome_widget, _classe_widget(nome_widget, Widget))

    # ---- fase 9: landscape
    module.TextureRenderTargetFormat = types.SimpleNamespace(
        RTF_R8="RTF_R8",
        RTF_R16f="RTF_R16f",
        RTF_R32f="RTF_R32f",
        RTF_RGBA8="RTF_RGBA8",
        RTF_RGBA16f="RTF_RGBA16f",
        RTF_RGBA32f="RTF_RGBA32f",
    )

    class RenderingLibrary:
        @staticmethod
        def import_file_as_texture2d(world, file_path):
            state["textures_importate"].append(str(file_path))
            larghezza, altezza = state.get("texture_size", (64, 64))
            return Texture2D(larghezza, altezza, _path=str(file_path))

        @staticmethod
        def create_render_target2d(world, width, height, format=None):
            rt = TextureRenderTarget2D(width, height, format)
            state["render_targets"].append(rt)
            return rt

        @staticmethod
        def begin_draw_canvas_to_render_target(world, render_target):
            return FakeCanvas(render_target), Vector2D(render_target.size_x, render_target.size_y), object()

        @staticmethod
        def end_draw_canvas_to_render_target(world, context):
            return None

        @staticmethod
        def export_render_target(world, render_target, file_path, file_name):
            state["render_target_esportati"].append((str(file_path), str(file_name)))

    module.RenderingLibrary = RenderingLibrary

    # ---- fase 8: networking esteso
    module.NetDormancy = types.SimpleNamespace(
        DORM_AWAKE="DORM_AWAKE",
        DORM_DORMANT_ALL="DORM_DORMANT_ALL",
        DORM_DORMANT_PARTIAL="DORM_DORMANT_PARTIAL",
        DORM_INITIAL="DORM_INITIAL",
        DORM_NEVER="DORM_NEVER",
    )

    # ---- fase 6: gameplay (fisica/navmesh/blackboard/behavior tree)
    def new_object(cls, outer=None, name=None):
        # Fase 12: i widget vanno registrati con il loro outer, perché è
        # scorrendo gli oggetti per outer che `_mcp_umg_widgets` ritrova
        # l'albero — la proprietà `AllWidgets` è protetta anche nel motore.
        if isinstance(cls, type) and issubclass(cls, Widget):
            widget = cls(name) if name else cls()
            widget._outer = outer
            state["umg_oggetti"].append((widget, outer))
            return widget
        return cls() if callable(cls) else FakeObject()

    module.new_object = new_object

    class ObjectIterator:
        """Scorre gli oggetti creati con `new_object`, filtrati per classe."""

        def __init__(self, cls=None):
            self._cls = cls

        def __iter__(self):
            for oggetto, _outer in list(state["umg_oggetti"]):
                if self._cls is None or isinstance(oggetto, self._cls):
                    yield oggetto

    module.ObjectIterator = ObjectIterator

    module.CollisionEnabled = types.SimpleNamespace(
        NO_COLLISION="NoCollision",
        QUERY_ONLY="QueryOnly",
        PHYSICS_ONLY="PhysicsOnly",
        QUERY_AND_PHYSICS="QueryAndPhysics",
        QUERY_AND_PROBE="QueryAndProbe",
        PROBE_ONLY="ProbeOnly",
    )

    class _NavSys:
        def is_navigation_being_built(self, world):
            return False

        def get_random_reachable_point_in_radius(self, world, origin, radius):
            return state.get("nav_random_point")

        def find_path_to_location_synchronously(self, world, start, end):
            return state.get("nav_path")

    _nav_instance = _NavSys()
    module.NavigationSystemV1 = types.SimpleNamespace(
        get_navigation_system=lambda world: _nav_instance
    )

    # ---- fase 7: GAS (enum usati da _mcp_gas_enum_member, non da import_text)
    module.GameplayAbilityInstancingPolicy = types.SimpleNamespace(
        NON_INSTANCED="NonInstanced",
        INSTANCED_PER_ACTOR="InstancedPerActor",
        INSTANCED_PER_EXECUTION="InstancedPerExecution",
    )
    module.GameplayAbilityNetExecutionPolicy = types.SimpleNamespace(
        LOCAL_PREDICTED="LocalPredicted",
        LOCAL_ONLY="LocalOnly",
        SERVER_INITIATED="ServerInitiated",
        SERVER_ONLY="ServerOnly",
    )
    module.GameplayEffectDurationType = types.SimpleNamespace(
        INSTANT="Instant",
        HAS_DURATION="HasDuration",
        INFINITE="Infinite",
    )

    return module

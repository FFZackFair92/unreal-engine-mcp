# Tool reference

143 tools, split across two layers. Works on UE 5.0+ — version-dependent tools are marked; `ue_status` reports the running engine's `capabilities`.

**Local layer** — runs as a process on your machine. Finds engines, creates
projects, opens and closes the editor, compiles C++, packages the game,
downloads assets. Works with the editor closed.

**Editor layer** — talks to a *running* editor over the Remote Control API
(`http://127.0.0.1:30010`), executing Python inside it. Needs the editor open.

Positions are in centimetres (1 Unreal unit = 1 cm), Z is up. Asset paths use
the Unreal convention (`/Game/...`).

---

## Engine and projects (local)

| Tool | Parameters | What it does |
|---|---|---|
| `ue_engine_list` | — | Engine installs found via `UE_MCP_ENGINE_DIRS`, the Epic Launcher manifest and the Windows registry (including hand-registered builds keyed by GUID). |
| `ue_engine_templates` | `engine_version`, `engine_root` | Official templates shipped with the engine (`TP_Blank`, `TP_ThirdPerson`, …). |
| `ue_project_create` | `name`, `directory`, `engine_version`, `template`, `blueprint_only`, `plugins`, `default_map`, `default_game_mode`, `description`, `force`, `engine_root` | Creates a project already wired for the bridge: `.uproject` with the required plugins, `DefaultRemoteControl.ini` with the two security flags, and `Content/Python/init_unreal.py`. Optionally copies an engine template, dropping its `Source/` for a Blueprint-only project. |
| `ue_project_find` | `directory`, `max_depth` | Finds `.uproject` files under a folder. |
| `ue_project_info` | `uproject` | Engine association, enabled plugins, whether the bridge is ready, whether the project has C++. |
| `ue_project_set_plugins` | `uproject`, `enable`, `disable` | Enables/disables plugins by editing the `.uproject`. Requires an editor restart. |

## Editor lifecycle (local)

| Tool | Parameters | What it does |
|---|---|---|
| `ue_editor_open` | `uproject`, `engine_version`, `wait_seconds`, `extra_args`, `engine_root`, `skip_module_check` | Launches the editor and waits for the bridge. `wait_seconds` defaults to 50 because most MCP clients abandon a request at 60 — a longer wait made the call report a timeout while the editor was starting fine; poll `ue_editor_status` instead. Refuses to launch when the project's compiled C++ modules do not match the engine: that mismatch does not produce an error, it opens a *rebuild?* dialog **behind the splash screen**, and all you see is an editor stuck at `0% - Initializing..` forever. |
| `ue_editor_status` | — | Whether the editor process is alive (including one you started by hand), whether Live Coding is running, whether the bridge answers. |
| `ue_editor_close` | `save_all`, `force` | Clean shutdown: saves, then `quit_editor` over the bridge. Falls back to killing the process. |

## C++ (local)

| Tool | Parameters | What it does |
|---|---|---|
| `ue_cpp_class_create` | `uproject`, `class_name`, `parent_class`, `module`, `properties`, `functions`, `with_tick`, `force` | Generates a compilable class with the Unreal boilerplate written correctly: `UCLASS`, `GENERATED_BODY`, the `MODULE_API` macro, the right parent header (`AIController.h` is not under `GameFramework/`), and the `A`/`U` prefix by hierarchy. Replicated properties also get `GetLifetimeReplicatedProps` and their `DOREPLIFETIME` entries — forget those and replication silently does nothing. If the project is Blueprint-only, the entire C++ module is created. |

`properties` entries take `name`, `type`, and optionally `category`, `default`,
`replicated`, `rep_notify`, `read_only`. `functions` entries take `name`,
`return_type`, `params`, `specifiers` (default `BlueprintCallable`) and `body`
— a `BlueprintCallable` function becomes callable from Blueprint graphs, which
is how generated logic reaches a designer's hands.

This is the workaround for Blueprint graphs not being scriptable. Full flow:

```
ue_cpp_class_create → ue_editor_close → ue_build_start
→ ue_build_status (until running=false) → ue_editor_open → ue_reparent_blueprint
```

## Compiling and packaging (local)

| Tool | Parameters | What it does |
|---|---|---|
| `ue_build_start` | `uproject`, `engine_version`, `target`, `configuration`, `engine_root` | Compiles the C++ module in the background. **Editor must be closed.** Kills `LiveCodingConsole.exe` first — it outlives the editor and keeps a lock on the DLLs. |
| `ue_build_status` | `tail_lines`, `uproject`, `wait_seconds` | Running or finished, exit code, parsed compiler errors and warnings, log tail. State is kept per project, so parallel builds do not overwrite each other. With `wait_seconds > 0` it polls inside the call and reports progress instead of returning immediately. |
| `ue_live_compile` | `max_wait_seconds` | Recompiles **with the editor open**, via Live Coding. Only patches function bodies: adding or changing `UCLASS`/`UFUNCTION`/`UPROPERTY` changes reflection data and still needs `ue_build_start`. |
| `ue_package_start` | `uproject`, `configuration`, `maps`, `output_dir`, `dedicated_server`, `engine_version`, `engine_root`, `target_platform` | Cook + build + stage + pak via `RunUAT BuildCookRun`. Produces a standalone executable. **Editor must be closed.** |
| `ue_package_status` | `tail_lines`, `uproject`, `wait_seconds` | Current phase (Cook, Stage, Package, Archive), errors, and the path of the produced `.exe`. Same per-project state and `wait_seconds` behaviour as the build status. |

Neither build nor package waits for completion inside the *start* call: they
would blow past the MCP timeout. They start and you poll the status tool — or
call it once with `wait_seconds`, which polls internally and emits MCP progress
notifications, so a long build costs one round trip instead of twenty.

## Diagnostics (editor)

| Tool | Parameters | What it does |
|---|---|---|
| `ue_status` | — | Engine version, open project, current level, actor count. **Call this first in any session.** |
| `ue_read_log` | `lines`, `only_errors` | Tail of the Unreal log, optionally filtered to errors and warnings. |
| `ue_exec_python` | `code` | Arbitrary Python inside the editor. The escape hatch: `unreal` and every `mcp_*` helper are in scope; assign to `result` to return a value. |
| `ue_save_all` | — | Saves the current level and every dirty asset. |

## Assets and levels (editor)

| Tool | Parameters | What it does |
|---|---|---|
| `ue_import_assets` | `files`, `destination`, `replace_existing`, `import_as_skeletal` | Imports `.glb`/`.gltf`/`.fbx`/`.wav` through the Interchange framework. |
| `ue_import_audio` | `files`, `destination` | Imports `.wav` files as SoundWave assets. |
| `ue_list_assets` | `path`, `recursive`, `class_filter` | Lists assets under a Content Browser path, filterable by class name. |
| `ue_new_level` | `path`, `template` | Creates a level and opens it. |
| `ue_open_level` | `path` | Opens an existing level. |
| `ue_delete_asset` | `path`, `force` | Deletes an asset or a folder. Refuses by default when something references it — deleting anyway leaves broken references in levels and Blueprints — and lists the referencers so you can decide. |
| `ue_rename_asset` | `path`, `new_path` | Moves or renames an asset, updating references. |
| `ue_duplicate_asset` | `path`, `new_path` | Duplicates an asset: the quick way to make a variant. |
| `ue_make_folder` | `path` | Creates a Content Browser folder. Idempotent. |

## Actors (editor)

| Tool | Parameters | What it does |
|---|---|---|
| `ue_spawn_actor` | `class_ref`, `location`, `rotation`, `scale`, `label` | Spawns from a class name, a `/Script/...` path, a Blueprint path, or a static mesh asset. The `label` is how other tools find it again. |
| `ue_spawn_many` | `actors` | Spawns a list of actors in **one round trip and one undo transaction**. Each entry is `{class_ref, location, rotation, scale, label}`. A failing entry is reported without losing the rest. |
| `ue_list_actors` | `name_contains`, `class_contains` | Lists level actors with optional filters. |
| `ue_set_actor_transform` | `label`, `location`, `rotation`, `scale` | Moves, rotates and scales an actor by label. |
| `ue_set_actor_property` | `label`, `properties`, `component` | Sets properties on a **placed** actor or one of its components — a mesh, a light's intensity, a trigger radius. Vectors are `{"x":…}`, colours `{"r":…}`, and `/Game/...` strings are loaded as assets. |
| `ue_list_actor_components` | `label` | Components of a placed actor, with name and class — tells you what to pass as `component` above. |
| `ue_delete_actor` | `label` | Removes an actor from the level. |
| `ue_attach_actor` | `child_label`, `parent_label`, `socket`, `attach_rule` | Attaches one actor to another, so moving the parent moves the child. `attach_rule` is `KEEP_WORLD`, `KEEP_RELATIVE` or `SNAP_TO_TARGET`. |
| `ue_detach_actor` | `label`, `keep_world` | Detaches an actor from its parent. |
| `ue_actor_hierarchy` | `label` | Parent/child tree of the level actors, or of one subtree. |

Actor edits are wrapped in `ScopedEditorTransaction`, so everything above is
undoable with Ctrl+Z by whoever is watching the editor.

## Blueprints (editor)

| Tool | Parameters | What it does |
|---|---|---|
| `ue_create_blueprint` | `package_path`, `name`, `parent_class` | New Blueprint with the given parent (`Actor`, `Character`, `GameModeBase`, or a full path). Idempotent. |
| `ue_add_component` | `blueprint_path`, `component_class`, `name` | Adds a component through `SubobjectDataSubsystem` and recompiles. |
| `ue_add_variable` | `blueprint_path`, `var_name`, `var_type`, `sub_type`, `replicated`, `instance_editable`, `default_value` | Typed member variable with replication and instance-editable flags. Types: `bool`, `int`, `int64`, `float`, `string`, `name`, `text`, `byte`, `struct`, `object`, `class`. **UE 5.4+** — earlier engines have no Python API for this; the tool fails with an explicit message. |
| `ue_set_class_defaults` | `blueprint_path`, `properties` | Writes Class Defaults on the CDO. |
| `ue_reparent_blueprint` | `blueprint_path`, `new_parent`, `remove_unused_variables` | Reassigns the parent class, typically to a generated C++ class. Variables matching a `UPROPERTY` on the new parent are absorbed; the rest survive renamed with `_0`. Reports which were absorbed. |
| `ue_compile_blueprint` | `blueprint_path` | Compiles and saves. |
| `ue_set_replication` | `blueprint_path`, `replicates`, `replicate_movement`, `always_relevant` | Networking flags on the CDO. See [Networking](#networking-editor) for the rest. |

**Not supported: authoring Blueprint node graphs.** UE 5.8 does not expose it to
Python — `EdGraph.Nodes` is protected, pin types are not exposed and there is no
API to link pins. These tools cover variables, components and defaults; the logic
itself belongs in C++ or in the editor by hand. See [UNREAL-NOTES.md](UNREAL-NOTES.md).

## Materials (editor)

Unlike Blueprint graphs, **material graphs are fully scriptable** — these tools
really do create and wire the nodes.

| Tool | Parameters | What it does |
|---|---|---|
| `ue_create_material` | `package_path`, `name`, `textures`, `scalars`, `two_sided` | Creates a material and connects textures to PBR channels (`base_color`, `normal`, `roughness`, `metallic`, `ambient_occlusion`, `emissive`, `opacity`). Use the key `"auto"` to infer the channel from the filename — this matches the ambientCG and Poly Haven naming, so downloaded assets wire themselves up. Normal maps get sRGB off and the right sampler type. |
| `ue_create_material_instance` | `package_path`, `name`, `parent_path`, `parameters` | Material Instance from a parent material. A number is a scalar, `{"r","g","b"}` a colour, a bool a static switch, a `/Game/...` path a texture. |
| `ue_assign_material` | `label`, `material_path`, `slot`, `component` | Assigns a material to a placed actor. |

## Console and rendering

| Tool | Parameters | What it does |
|---|---|---|
| `ue_console_command` | `command`, `wait_seconds` | Runs an editor console command and returns **what it printed**. Console commands do not return values, they write to the log, so the tool diffs the log around the call; a command that prints nothing says so instead of leaving it ambiguous. Goes through the editor's Python interpreter, not the `bAllowConsoleCommandRemoteExecution` gate — same caveat as `ue_exec_python`. |
| `ue_render_sequence` | `uproject`, `sequence`, `config`, `map_path`, `output_dir`, `resolution`, `force` | Renders a Level Sequence through the Movie Render Queue in a headless `UnrealEditor-Cmd` process. In-editor MRQ is asynchronous and would hold the editor for the whole render; a separate process is followed like a build. `config` (a saved Movie Pipeline preset) is how format, resolution and output directory are chosen — without one, MRQ uses project defaults and may write nothing. |
| `ue_render_status` | `tail_lines`, `uproject`, `wait_seconds` | Progress of the render. `succeeded` is decided by **files produced**, not the exit code: a headless MRQ run can exit 0 having rendered zero frames. New files are found by diffing the output directory against a snapshot taken at start, not by modification time — comparing a process clock against a filesystem's finds nothing on a network share. |

> These two are the least verified tools in the project. Their pure parts are
> tested — format mapping, command construction, path validation, output
> collection — but no test here has ever rendered a frame with a real engine.

## Viewport camera (editor)

| Tool | Parameters | What it does |
|---|---|---|
| `ue_get_camera` | — | Where the editor viewport camera is and what it is looking at. Worth calling **before spawning**: real levels are often built thousands of units from `[0,0,0]`, so an actor placed at the origin can be off-screen and invisible. |
| `ue_set_camera` | `location`, `rotation` | Moves the camera. Either argument alone keeps the other as it was. |
| `ue_focus_actor` | `label`, `distance` | Frames an actor, like pressing F in the editor. The complement of `ue_screenshot` — without it you photograph whatever the camera happened to be pointing at. |

## Screenshots (editor)

| Tool | Parameters | What it does |
|---|---|---|
| `ue_screenshot` | `filename`, `width`, `height`, `return_image` | Captures the editor viewport and **returns the image itself** as MCP `ImageContent`, so the model actually sees it — a path alone leaves the agent exactly as blind as it was. The PNG also stays on disk under `<Project>/Saved/Screenshots/MCP`. Capture happens a frame or two later, so the tool waits for the file and says so if it never appeared. |

The default resolution is deliberately modest (960×540): the PNG travels
base64-encoded inside the response, and at 1280×720 it often costs more
context than the picture saves. Above `UE_MCP_MAX_SCREENSHOT` (1.5 MB) the
image is not attached and the tool says why. `return_image=False` goes back to
returning just the path.

## Play In Editor (editor)

| Tool | Parameters | What it does |
|---|---|---|
| `ue_configure_pie` | `num_players`, `net_mode`, `one_process` | Client count and net mode (`standalone`, `listen_server`, `client`) for local multiplayer testing. |
| `ue_start_pie` / `ue_stop_pie` | — | Starts and stops the session. |
| `ue_set_project_setting` | `section`, `key`, `value`, `config` | Writes into `Config/Default<config>.ini`. Some settings need an editor restart. |

## Audio (editor)

| Tool | Parameters | What it does |
|---|---|---|
| `ue_create_metasound_source` | `package_path`, `name` | Empty MetaSound Source asset (needs the MetaSound plugin). |
| `ue_create_sound_cue` | `package_path`, `name`, `wave_path` | Sound Cue, optionally already wired to an imported SoundWave. |

## Reflection (editor)

UE's Python API has no generic way to list a class's properties/functions —
`get_editor_property(name)` needs the name already known. These tools cover
what the API *does* expose: walking the class/struct hierarchy and reading
native enum values.

| Tool | Parameters | What it does |
|---|---|---|
| `ue_find_classes` | `parent`, `name_contains`, `limit` | Classes (native and Blueprint) derived from `parent`, `parent` included. Project Blueprints show up with their generated name (`BP_PlayerCharacter_C`). |
| `ue_find_structs` | `parent`, `name_contains`, `limit` | Structs derived from `parent` (`ScriptStruct`), `parent` included. |
| `ue_reflect_enum` | `enum_name` | Name, numeric value and display name of every entry in a native engine enum (e.g. `"CollisionChannel"`, without the `E` prefix). Does not cover Blueprint-defined enums (`UserDefinedEnum` assets in `/Game/...`) — those need `ue_exec_python` + `unreal.load_asset(path)`. |

## UMG (editor)

| Tool | Parameters | What it does |
|---|---|---|
| `ue_create_widget_blueprint` | `package_path`, `name`, `parent_class`, `editor_utility` | Empty Widget Blueprint asset (or Editor Utility Widget with `editor_utility=True`). `parent_class` accepts a project C++ class, for the BindWidget path. **The tree it creates has no root** — see below. |

For logic, the C++ workaround still applies: give the widget a C++ parent
class with `BindWidget` properties (`ue_cpp_class_create` →
`ue_reparent_blueprint`), where the property names match the widget names.

## UMG layout (editor)

This section corrects an earlier claim too. `WidgetTree` *is* a protected
property — but the object behind it is a subobject of the Widget Blueprint,
and you get it by name: `find_object(wbp, "WidgetTree")`. From there the
layout is authored with the widgets' own public API (`PanelWidget.add_child`
and friends are real UFUNCTIONs, and they work on editor templates, which
nobody had tried). Verified live on UE 5.8: CanvasPanel → VerticalBox →
TextBlock + Button, with text, colour, padding and position, saved and
re-read from scratch — hierarchy and values intact, widget names present in
the `.uasset`.

**One limit is real and remains.** `WidgetTree.RootWidget` is protected for
writing too, and no UFUNCTION anywhere sets it (searched across every exposed
class). So the *first* widget of an empty tree cannot be created from Python.
A Widget Blueprint made in the Widget Designer has a root; one made by
`ue_create_widget_blueprint` does not. The practical route is
`ue_duplicate_asset` on a Widget Blueprint that already has a root, then
emptying it with `ue_umg_remove_widget`.

Widgets are addressed by name (`Titolo`, `CanvasPanel_0`) — unique within a
tree, and the same names shown in the Hierarchy panel.

| Tool | Parameters | What it does |
|---|---|---|
| `ue_umg_tree_info` | `widget_blueprint_path` | The widget hierarchy: names, classes, children, slot class. `root: null` means an empty tree. |
| `ue_umg_add_widget` | `widget_blueprint_path`, `widget_class`, `parent?`, `name?`, `slot?` | Creates a widget and parents it under a panel (the root by default). |
| `ue_umg_set_widget_property` | `widget_blueprint_path`, `widget`, `properties` | Text, colour, visibility, brush… Strings are converted to `FText` where the property needs one. Returns `applied` and `failed` separately. |
| `ue_umg_set_slot` | `widget_blueprint_path`, `widget`, `properties` | Layout inside the parent panel. |
| `ue_umg_remove_widget` | `widget_blueprint_path`, `widget` | Removes a widget and everything under it. |

**Slot keys depend on the parent panel**, and `ue_umg_tree_info` reports
`slot_class` so you know which you're dealing with:

- `CanvasPanelSlot` — `position` `[x, y]`, `size` `[x, y]`, `z_order`,
  `alignment`, `auto_size`.
- `VerticalBoxSlot` / `HorizontalBoxSlot` — `padding`, `horizontal_alignment`,
  `vertical_alignment`.

`padding` and `position` both arrive as lists over MCP but the engine wants
two different structs: a 2-element list becomes a `Vector2D`, a 4-element one
(or a dict with `left`/`top`/`right`/`bottom`) becomes a `Margin`.

```
ue_duplicate_asset("/Engine/Sequencer/DefaultBurnIn", "/Game/UI/WBP_Menu")
ue_umg_tree_info("/Game/UI/WBP_Menu")            # find the root, empty it
ue_umg_add_widget("/Game/UI/WBP_Menu", "VerticalBox", name="Column",
                  slot={"position": [80, 80], "size": [500, 300]})
ue_umg_add_widget("/Game/UI/WBP_Menu", "TextBlock", parent="Column", name="Title")
ue_umg_set_widget_property("/Game/UI/WBP_Menu", "Title", {"Text": "Main menu"})
ue_umg_set_slot("/Game/UI/WBP_Menu", "Title", {"padding": [8, 8, 8, 12]})
```

## Blueprint graph (editor)

Three shortcuts for specific jobs. Authoring the nodes themselves lives in
[the next section](#blueprint-graph-authoring-editor).

| Tool | Parameters | What it does |
|---|---|---|
| `ue_bp_list_graphs` | `blueprint_path` | Names of a Blueprint's graphs (EventGraph, UserConstructionScript, functions...). |
| `ue_bp_list_events` | `blueprint_path` | Events visible on a Blueprint (custom, inherited overridable, interface), each with `is_implemented`. |
| `ue_bp_add_event_override` | `blueprint_path`, `event_name`, `x`, `y` | Adds (or finds) the node for an inherited overridable event; returns its path and pins. |
| `ue_bp_add_function_graph` | `blueprint_path`, `func_name` | Empty function graph with default Entry/Return nodes. |

## Blueprint graph authoring (editor)

**UE 5.8+.** `ue_status` reports it as `capabilities.blueprint_graph_authoring`;
on engines without it these tools fail with an explanation, and the answer is
still a C++ parent class.

This section corrects an earlier claim in this file. The wall was real —
`EdGraph.Nodes` is a protected property, and still is — but the conclusion
drawn from it was wrong: you never need to touch `Nodes`, because
`unreal.BlueprintGraphEditor` manipulates the graph from the outside, the way
the editor itself does. Verified live on UE 5.8: BeginPlay → PrintString →
Branch with a bool variable driving `Condition`, exec wires connected, string
literal written on `InString`, Blueprint compiling `BS_UP_TO_DATE` with no
errors or warnings, saved and re-read from scratch with the connections still
in place.

A node is addressed by its **object name** (`K2Node_CallFunction_0`), which
every tool returns when it creates one. Node *titles* follow the editor's
language ("Ramo" for Branch) and must not be used as keys. Event nodes, which
already exist in the graph, have the alias `event:<MemberName>` — e.g.
`event:ReceiveBeginPlay`.

| Tool | Parameters | What it does |
|---|---|---|
| `ue_bp_graph_info` | `blueprint_path`, `graph_name?` | Nodes, pins, connections and compile errors. The starting point: it gives you the object names everything else needs. |
| `ue_bp_add_call_function` | `blueprint_path`, `function_path`, `graph_name?`, `position?` | Function call node. `function_path` is `/Script/<Module>.<Class>:<Function>`, e.g. `/Script/Engine.KismetSystemLibrary:PrintString`. |
| `ue_bp_add_branch` | `blueprint_path`, `graph_name?`, `position?` | Branch node — `Condition` in, `then`/`else` out. |
| `ue_bp_add_custom_event` | `blueprint_path`, `event_name`, `graph_name?`, `position?` | Custom Event. Event graphs only; a function can't hold one. |
| `ue_bp_add_variable_node` | `blueprint_path`, `variable_name`, `mode`, `graph_name?`, `position?`, `class_path?` | Get or Set node for a member variable (create it first with `ue_add_variable`). |
| `ue_bp_add_node_by_name` | `blueprint_path`, `node_name`, `graph_name?`, `position?` | Any palette node by `Category\|Name`. Last resort — see the localisation trap below. |
| `ue_bp_list_palette` | `blueprint_path`, `graph_name?`, `contains?`, `limit?` | Searches the addable-node palette by substring. |
| `ue_bp_connect` | `blueprint_path`, `from_node`, `from_pin`, `to_node`, `to_pin`, `graph_name?` | Wires an output pin to an input pin. Incompatible types are reported with both type names. |
| `ue_bp_break_pin` | `blueprint_path`, `node`, `pin`, `graph_name?` | Breaks every link on a pin, and says how many there were. |
| `ue_bp_set_pin_value` | `blueprint_path`, `node`, `pin`, `value`, `graph_name?` | Literal value of an unconnected input pin. |
| `ue_bp_remove_node` | `blueprint_path`, `node`, `graph_name?` | Deletes a node and its links. |

**Two traps, both found live.**

*The palette is localised.* On an Italian editor, Branch is
`Utilità|ControlloDiFlusso|Ramo` — passing `Utilities|FlowControl|Branch`
returns nothing. That's why the typed tools (`ue_bp_add_branch`,
`ue_bp_add_call_function`, `ue_bp_add_variable_node`) are the main road and
`ue_bp_add_node_by_name` is the escape hatch; when you do need it, find the
exact string with `ue_bp_list_palette` first.

*Unreal does not validate pin values.* Writing `"non_un_bool"` onto a boolean
pin is accepted and stored verbatim. `ue_bp_set_pin_value` therefore always
reads the pin back and returns what's actually there — check that, rather than
trusting the call succeeded.

A worked example, from nothing to a working graph:

```
ue_create_blueprint("/Game/MyGame", "BP_Door", "Actor")
ue_add_variable("/Game/MyGame/BP_Door", "IsOpen", "bool")
n = ue_bp_add_call_function("/Game/MyGame/BP_Door",
                            "/Script/Engine.KismetSystemLibrary:PrintString")
ue_bp_connect("/Game/MyGame/BP_Door", "event:ReceiveBeginPlay", "then", n.node, "execute")
ue_bp_set_pin_value("/Game/MyGame/BP_Door", n.node, "InString", "Door ready")
ue_bp_graph_info("/Game/MyGame/BP_Door")     # check errors == []
```

## Animation (editor)

Unlike UMG and the Blueprint graph, writing here genuinely works: a
BlendSpace's `BlendParameters`/`SampleData` are ordinary struct arrays, not
protected — confirmed live by creating an asset, filling it, saving, and
reloading from scratch on a real project (Remy_Skeleton,
BS_Remy_Locomozione). An Anim Blueprint's AnimGraph is still an EdGraph like
any other, though — same wall, so `ue_create_anim_blueprint` only creates
the asset.

| Tool | Parameters | What it does |
|---|---|---|
| `ue_skeleton_info` | `skeleton_path` | Bones and sockets from a Skeleton's reference pose. |
| `ue_anim_sequence_info` | `anim_path` | Length, frame count, notify tracks/events, sync markers and curve names of an AnimSequence. |
| `ue_create_blend_space_1d` | `package_path`, `name`, `skeleton_path`, `axis_name`, `axis_min`, `axis_max`, `grid_num`, `samples` | Creates a BlendSpace1D with one axis and, optionally, its samples (`[{"value": float, "animation": path}, ...]`). 1D only for now — BlendSpace (2D) uses the same data shape but wasn't verified live in this phase. |
| `ue_create_anim_montage` | `package_path`, `name`, `source_animation_path` | AnimMontage wrapping an existing AnimSequence, with its default slot. |
| `ue_create_anim_blueprint` | `package_path`, `name`, `skeleton_path`, `parent_class` | Empty Anim Blueprint asset bound to a Skeleton. The AnimGraph itself has to be drawn by hand in the Anim Blueprint Editor. |

## Niagara / VFX (editor)

Same wall again: `EmitterHandles` on `NiagaraSystem` is protected — no
adding emitters or modules from Python, confirmed live even on populated
templates from the engine's own system library. Reading an *existing*
system's emitters and exposed user parameters works, though, and at the
asset level — no running PIE or instantiated component needed.

| Tool | Parameters | What it does |
|---|---|---|
| `ue_create_niagara_system` | `package_path`, `name` | Empty Niagara System asset. The emitter stack has to be built by hand in the Niagara Editor. |
| `ue_niagara_system_info` | `system_path` | Emitters (name, enabled, lightweight) and exposed user parameters (name, type) of an existing Niagara System. |

## Gameplay: physics, navmesh, AI (editor)

Physics/collision and the navmesh are fully scriptable — confirmed live.
Blackboard and Behavior Tree break the "graphs are protected" pattern from
UMG/Blueprint/Niagara: their tree (`RootNode`, `Children`, `Decorators`,
`Services`) really is writable from Python, because they aren't a real
`EdGraph` under the hood, just plain `UObject`s and structs. EQS is blocked
the same way UMG/Blueprint/Niagara are — only the empty asset is scriptable.
AI Perception adds through the generic `ue_add_component`; its `SensesConfig`
has to be set by hand in the Blueprint's Details panel (`EditDefaultsOnly`,
not reliably reachable from the component template via Python).

Behavior Tree nodes are addressed by a dot-path of child indices from the
root: `"root"` is the root node itself, `"0"` its first child, `"0.1"` the
second child of that first child, and so on. `ue_bt_add_node` returns the
path of the node it just created, meant to be reused in later calls.

| Tool | Parameters | What it does |
|---|---|---|
| `ue_set_component_physics` | `actor`, `component`, `simulate_physics?`, `collision_enabled?`, `collision_profile?` | Physics simulation and collision on a component. `collision_enabled` accepts `NoCollision`/`QueryOnly`/`PhysicsOnly`/`QueryAndPhysics`/`QueryAndProbe`/`ProbeOnly`, case/underscore-insensitive. |
| `ue_component_physics_info` | `actor`, `component` | Current simulate/collision-enabled/collision-profile state. |
| `ue_nav_rebuild` | — | Rebuilds the navmesh (`RebuildNavigation` console command). Needs at least one `NavMeshBoundsVolume` in the level — spawn one with `ue_spawn_actor`. |
| `ue_nav_query_point` | `origin`, `radius` | A random reachable point on the navmesh within a radius of an origin. |
| `ue_nav_find_path` | `start`, `end` | Synchronous pathfinding on the navmesh — no running PIE needed. |
| `ue_create_blackboard` | `package_path`, `name` | Empty Blackboard Data asset. |
| `ue_blackboard_add_key` | `blackboard_path`, `key_name`, `key_type` | Adds a key (`object`/`class`/`bool`/`int`/`float`/`string`/`name`/`vector`/`rotator`/`enum`). |
| `ue_blackboard_info` | `blackboard_path` | Lists the keys of an existing Blackboard Data. |
| `ue_create_behavior_tree` | `package_path`, `name`, `blackboard_path?`, `root_composite?` | Creates a Behavior Tree with a root composite node already set (Selector by default), optionally linked to a Blackboard. |
| `ue_bt_add_node` | `bt_path`, `parent_path`, `node_class`, `index?` | Adds a composite or task node (inferred from the base class) as a child of an existing composite. |
| `ue_bt_add_decorator` | `bt_path`, `node_path`, `decorator_class` | Adds a decorator to a node's child link (not valid on `"root"`). |
| `ue_bt_add_service` | `bt_path`, `node_path`, `service_class` | Adds a service to a composite node (Selector/Sequence only, not tasks). |
| `ue_bt_set_node_property` | `bt_path`, `node_path`, `property_name`, `value` | Sets a property on a node. Auto-handles blackboard-bindable fields (`FValueOrBBKey_*` structs, e.g. `BTTask_Wait.WaitTime`) by writing the fixed default value. |
| `ue_bt_info` | `bt_path` | Recursive dump of the tree: nodes, decorators, services, paths. |
| `ue_create_eqs_asset` | `package_path`, `name` | Empty Environment Query asset. `Options` is protected — build the query by hand in the EQS Editor. |

## GAS: Gameplay Ability System (editor)

Requires the `GameplayAbilities` plugin — enable it with `ue_project_set_plugins`
and restart the editor before these classes exist in Python.

GameplayEffect and AttributeSet are plain Blueprintable classes: they already
work with the generic `ue_create_blueprint` (`parent_class="GameplayEffect"`
or `"AttributeSet"`), no dedicated tool needed. GameplayAbility has its own
asset type instead (`GameplayAbilityBlueprint`, not a plain `Blueprint`),
which is why `ue_create_gameplay_ability` exists. Adding an attribute
(`GameplayAttributeData`) to an AttributeSet already works with
`ue_add_variable` — pass the full struct path as `sub_type`
(`/Script/GameplayAbilities.GameplayAttributeData`, not in the short-name
whitelist). The ability's actual logic (`ActivateAbility` and its nodes) is
still a Blueprint graph — not scriptable, same as everywhere else.

**The wall, and how it's worked around**: the normal way to build a
modifier — `GameplayModifierInfo.Attribute`/`.ModifierOp` via
`set_editor_property` — is blocked ("cannot be edited on instances"), and
`GameplayAttribute.AttributeName` is read-only. `ue_ge_add_modifier` works
around it by building the whole `GameplayModifierInfo` struct at once via
`import_text` — the same technique already used in this file for
`EdGraphPinType`. Verified live end-to-end: built a modifier pointing at a
real attribute on a real AttributeSet Blueprint, saved the GameplayEffect,
reloaded the asset from scratch — the modifier was really there, values
included. Not verified in PIE, only that it persists on the asset.

| Tool | Parameters | What it does |
|---|---|---|
| `ue_create_gameplay_ability` | `package_path`, `name`, `instancing_policy?`, `net_execution_policy?` | Creates a GameplayAbility Blueprint (dedicated asset type). Optionally sets InstancingPolicy/NetExecutionPolicy. |
| `ue_create_gameplay_effect` | `package_path`, `name`, `duration_policy?`, `period?` | Creates a GameplayEffect Blueprint (plain Blueprint, GameplayEffect parent). Optionally sets DurationPolicy and the periodic-application interval. |
| `ue_ge_add_modifier` | `ge_path`, `attribute_set_path`, `attribute_name`, `modifier_op`, `magnitude` | Adds a modifier linking an attribute on an existing AttributeSet Blueprint, an operation (`add`/`add_final`/`multiply`/`divide`/`multiply_compound`/`override`), and a fixed magnitude. Constant `ScalableFloat` only — no curves or attribute-based magnitude yet. |
| `ue_ge_add_component` | `ge_path`, `component_class` | Adds a `GameplayEffectComponent` (e.g. `AssetTagsGameplayEffectComponent`) to a GameplayEffect. Only the addition — configuring tags/conditions inside it isn't covered, use `ue_exec_python` or the editor. |
| `ue_ge_info` | `ge_path` | Duration policy, period, modifiers (attribute/op/magnitude), and GameplayEffectComponents of an existing GameplayEffect. |

## Networking (editor)

`ue_set_replication` decides *whether* an actor replicates; these decide *how
much bandwidth it costs*. All of it is plain `get/set_editor_property` on the
Blueprint CDO — no protected properties here, verified live on UE 5.8 by
reading the property names back out of the saved `.uasset`.

| Tool | Parameters | What it does |
|---|---|---|
| `ue_set_net_config` | `blueprint_path`, `dormancy?`, `net_update_frequency?`, `min_net_update_frequency?`, `net_priority?`, `net_cull_distance?`, `only_relevant_to_owner?`, `net_use_owner_relevancy?`, `net_load_on_client?` | Dormancy (`awake`/`initial`/`dormant_all`/`dormant_partial`/`never`), update rates, replication priority and relevancy. Incremental: only the parameters you pass are touched. `net_cull_distance` is in cm and is squared for you, because that's how Unreal stores it. |
| `ue_net_info` | `blueprint_path` | Everything above plus `replicates`/`replicate_movement`/`always_relevant`, and which components replicate. |
| `ue_set_component_replication` | `blueprint_path`, `component_name`, `replicates` | A replicated actor does *not* replicate its components automatically — this is the switch. The `_GEN_VARIABLE` suffix Unreal adds to component templates is handled for you. |
| `ue_set_component_default` | `blueprint_path`, `component_name`, `property_name`, `value` | Writes a property on a component *template*, not on a placed instance. |

`ue_set_component_default` closes a gap left open by the gameplay phase: an
`EditDefaultsOnly` property such as `SensesConfig` on an AIPerceptionComponent
is rejected on a spawned actor ("cannot be edited on instances"), and reaching
the Blueprint's component template looked unavailable. It isn't:
`SubobjectDataBlueprintFunctionLibrary.get_object` turns a subobject handle
into the template. Verified live — a Sight sense config with custom radii
written from Python, saved, and found again in the `.uasset`.

## Landscape (editor)

**You cannot create a landscape from Python.** Verified live on UE 5.8:
spawning `Landscape` gives you a `LandscapePlaceholder` — an empty actor with
no components, no target layers, not even `ALandscape`'s methods. The classes
that really create one (`LandscapeSubsystem`, `LandscapeEditorObject`,
`ActorFactoryLandscape`) exist in the engine but are not exposed to its
Python. Create the terrain once with Landscape Mode in the editor; everything
after that is scriptable from here.

| Tool | Parameters | What it does |
|---|---|---|
| `ue_landscape_list` | — | Landscapes in the current level. Empty means there is nothing to work on. |
| `ue_landscape_info` | `label?` | Components, material, paint target layers, edit layers, grass. `label` is optional when the level has exactly one landscape. |
| `ue_landscape_import_heightmap` | `image_path`, `label?`, `rt_format?`, `from_rg_channel?` | Overwrites the heightmap from an image file on disk. Bring the image to the landscape's resolution first — nothing rescales it. |
| `ue_landscape_import_weightmap` | `layer_name`, `image_path`, `label?`, `rt_format?` | Paints a target layer from a greyscale image. The layer must already exist — target layers come from the landscape material. |
| `ue_landscape_export_heightmap` | `output_dir`, `file_name`, `label?`, `resolution?`, `rt_format?`, `into_rg_channel?` | Exports the heightmap to disk. `RGBA8` writes a PNG, float formats write HDR. |
| `ue_landscape_set_material` | `material_path`, `label?` | Assigns the landscape material — which is also what defines the paintable layers. |
| `ue_landscape_set_grass` | `enabled`, `label?` | Turns the procedural grass system on or off. |

Unreal only accepts a heightmap as a `TextureRenderTarget2D`, so these tools
build one for you: `import_file_as_texture2d` → `begin_draw_canvas_to_render_target`
→ `Canvas.draw_texture`. That chain is verified live up to and including the
render target (a 64×64 gradient PNG read back pixel by pixel with the right
values); the final `landscape_import_heightmap_from_render_target` call is
**not** verified, because there was no landscape to try it on and Python
cannot make one.

`RGBA8` gives 256 height levels. For a real 16-bit heightmap use `RGBA16f` or
`RGBA32f` with `from_rg_channel=True`, which is how Unreal packs 16 bits of
height into two channels.

## PCG: Procedural Content Generation (editor)

The surprise of the parity roadmap. After Blueprint, UMG and Niagara all hit
the same protected-graph wall, PCG turns out to be **fully scriptable** —
nodes, edges, positions and node properties. Verified live on UE 5.8 by
building Input → SurfaceSampler → StaticMeshSpawner, saving, and reloading the
asset from scratch: nodes, edges and `points_per_squared_meter` were all still
there, and the node names show up in the `.uasset`.

The reason is the same as Behavior Trees: a PCG graph is a graph of real data
objects (`UPCGNode` + `UPCGEdge`), not a `UEdGraph` of K2 nodes with the actual
content hidden behind a protected property. `UPCGEditorGraph` is only its
visual representation, and you never need to touch it.

Requires the PCG plugin — enable it with `ue_project_set_plugins`.

| Tool | Parameters | What it does |
|---|---|---|
| `ue_create_pcg_graph` | `package_path`, `name` | Empty PCGGraph asset, with its Input and Output nodes already in it. |
| `ue_pcg_add_node` | `graph_path`, `settings_class`, `position?` | Adds a node and returns its name and pins. In PCG a node's *type is* its settings class: `PCGSurfaceSamplerSettings`, `PCGStaticMeshSpawnerSettings`, `PCGCreatePointsGridSettings`… |
| `ue_pcg_connect` | `graph_path`, `from_node`, `from_pin`, `to_node`, `to_pin` | Links an output pin to an input pin. `"input"` and `"output"` are aliases for the graph's own two nodes. Wrong pin names are rejected with the real list. |
| `ue_pcg_disconnect` | `graph_path`, `from_node`, `from_pin`, `to_node`, `to_pin` | Removes a link, and says whether one was actually there. |
| `ue_pcg_remove_node` | `graph_path`, `node` | Removes a node and its links. |
| `ue_pcg_set_node_property` | `graph_path`, `node`, `property_name`, `value` | Writes a property on the node's settings (`points_per_squared_meter`, `seed`, a mesh path…). |
| `ue_pcg_graph_info` | `graph_path` | Nodes (name, settings class, pins, position) and edges. This is how you learn the pin names before wiring anything. |
| `ue_pcg_spawn_volume` | `graph_path`, `label?`, `location?`, `size?` | Places a PCGVolume with the graph attached. `size` is in cm; the default brush is 200 cm a side, so it becomes actor scale. |
| `ue_pcg_generate` | `label`, `force` | Regenerates the PCG on an actor. Call it after editing the graph. |
| `ue_pcg_cleanup` | `label`, `remove_components` | Deletes what PCG generated, leaving graph and volume in place. |

Pin names are the visible labels, spaces included — `"Bounding Shape"`, not
`BoundingShape`. Ask `ue_pcg_graph_info` rather than guessing.

## Free asset downloads (local)

Downloads land in `UE_MCP_LIBRARY` (default `~/UnrealAssetLibrary`), are verified
against the published md5 where available, are size-capped by
`UE_MCP_MAX_DOWNLOAD`, and archives are extracted with path-traversal protection.

| Tool | Source | Licence |
|---|---|---|
| `preset_search_polyhaven` / `preset_download_polyhaven` | [Poly Haven](https://polyhaven.com) — HDRIs, PBR textures, models. glTF downloads pull their `.bin` and textures too. | CC0 |
| `preset_search_ambientcg` / `preset_download_ambientcg` | [ambientCG](https://ambientcg.com) — PBR materials, HDRIs, models. | CC0 |
| `preset_download_kenney` | [kenney.nl](https://kenney.nl) — low-poly packs. No API: the link is resolved from the page. | CC0 |
| `preset_download_url` | Any direct URL (zip/glb/fbx/wav), extracted automatically. | depends |
| `preset_extract_archive` | A zip/tar already on disk. `.rar` is not supported by the standard library. | — |
| `preset_library_list` | Lists the local library, ready to feed `ue_import_assets`. | — |
| `preset_fab_list_vault` / `preset_fab_download` | Purchased Fab/Marketplace content. | your Epic licence |

> **Fab caveat.** Purchased content sits behind an Epic login with no public API.
> These two tools shell out to the community client
> [`legendary`](https://github.com/derrod/legendary) (`pip install legendary-gl`,
> then `legendary auth`). Without it, the tool explains how to download from the
> Epic Games Launcher instead. This is third-party software, not an official route.

## Transports

The server reaches the editor two ways, and `ue_status` reports which one is
live.

| Transport | Unreal-side setup | When |
|---|---|---|
| `pyremote` | *Python Editor Script Plugin* + **Enable Remote Execution** | Default. The engine's own remote-execution channel: UDP multicast discovery on `239.0.0.1:6766`, then a TCP command channel that **the editor opens back towards us** — which is why a firewall shows up as "responded to discovery but never connected". |
| `remotecontrol` | Both plugins + `DefaultRemoteControl.ini` | Editor on another machine, or a network that swallows multicast. |

`UE_MCP_TRANSPORT=auto` (the default) tries the native channel and falls back to
HTTP, so a project configured either way just works. The choice is made once and
kept: swapping transports mid-session would mean reinstalling the in-editor
helpers every call.

## Resources

Resources cost less than a tool call: the client can keep them up to date by
itself and attach them to the context, instead of the model spending a turn
asking "what does the editor look like right now?".

| URI | Contents |
|---|---|
| `unreal://status` | Engine version, project, current level, actor count, detected capabilities. |
| `unreal://log` | Last 200 lines of the editor log. |
| `unreal://actors` | Actors in the currently open level. |
| `unreal://assets` | Everything under `/Game`. For sub-paths use `ue_list_assets`. |
| `unreal://engines` | Engine installs found on this machine. Works with the editor closed. |

A resource that raises disappears from the client, so with the editor closed
they answer `{"available": false, "reason": …}` instead of failing.

## Extending it with project-specific tools

Any `local_tools.py` placed next to `server.py` is imported automatically at
startup and can register extra tools with `@mcp.tool()`:

```python
from .server import lit, mcp, run

@mcp.tool()
async def mygame_bootstrap(root: str = "/Game/MyGame") -> dict:
    """Compose the primitives into a one-call workflow for your game."""
    return await run(f"result = mcp_create_blueprint({lit(root + '/Blueprints')}, 'BP_MyGameMode', 'GameModeBase')")
```

The file is listed in `.gitignore`, so per-project workflows (folder structures,
Blueprint sets with replication and typed variables, level population) stay on
your machine and out of the public repo. `tests/test_local_tools.py` gets the
same treatment for their tests.

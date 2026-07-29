# Tool reference

58 tools, split across two layers. Works on UE 5.0+ — version-dependent tools are marked; `ue_status` reports the running engine's `capabilities`.

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
| `ue_editor_open` | `uproject`, `engine_version`, `wait_seconds`, `extra_args`, `engine_root` | Launches the editor and **waits until the bridge answers**, so the next call is safe. First launch compiles shaders and can take minutes. |
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
| `ue_set_replication` | `blueprint_path`, `replicates`, `replicate_movement`, `always_relevant` | Networking flags on the CDO. |

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

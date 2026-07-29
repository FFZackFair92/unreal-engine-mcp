# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.3.0] — 2026-07-29

### Added

- **`ue_screenshot` now returns the image itself**, as MCP `ImageContent`,
  instead of a filesystem path. The tool existed so the agent could *see* what
  it had built; handing back a string left it exactly as blind as before.
  Default resolution drops to 960×540 because the PNG travels base64-encoded
  inside the response and at 1280×720 usually costs more context than the
  picture saves. `UE_MCP_MAX_SCREENSHOT` (1.5 MB) caps the attachment,
  `return_image=False` restores the old behaviour, and an editor on another
  machine is reported rather than silently failing.
- **Asset management** — `ue_delete_asset`, `ue_rename_asset`,
  `ue_duplicate_asset`, `ue_make_folder`. An agent could create and import but
  not clean up after a bad import. `ue_delete_asset` refuses by default when
  something references the asset, and lists the referencers: deleting anyway
  leaves broken references in levels and Blueprints.
- **Actor hierarchy** — `ue_attach_actor`, `ue_detach_actor`,
  `ue_actor_hierarchy`. This is how a scene is composed (lights on a lamppost,
  crates on a pallet) instead of leaving loose objects to be repositioned one
  at a time.
- **MCP resources** — `unreal://status`, `unreal://log`, `unreal://actors`,
  `unreal://assets`, `unreal://engines`. Cheaper than a tool call: the client
  refreshes them itself. With the editor closed they answer
  `{"available": false, "reason": …}`, because a resource that raises
  disappears from the client while one that explains stays useful.
- **Progress notifications.** `ue_build_status` and `ue_package_status` take
  `wait_seconds`: above zero they poll inside a single call and emit MCP
  progress, so a build that takes minutes costs one round trip instead of
  twenty. `ue_editor_open` reports progress while waiting for the bridge.
- **Release workflow** with PyPI trusted publishing, so the install is
  `pip install unreal-mcp` instead of a clone.

### Security

- **Build and packaging arguments are now validated against an allowlist.**
  `target`, `platform`/`target_platform`, `configuration`, `maps` and
  `output_dir` are interpolated into the body of the generated `.bat`/`.sh`
  launch script, so a value like `configuration="Development & whoami"` was not
  a build with an odd name — it was an extra command run by the shell. An
  allowlist is both simpler and more robust than trying to quote portably
  across `cmd.exe` and `sh`. Paths containing quotes or newlines are rejected
  for the same reason.
- **`extract_archive` no longer trusts tar link members.** The existing name
  filter caught `..` and absolute paths, but a symlink or hardlink member can
  point outside the destination while having a perfectly innocuous name. Now
  uses `filter="data"` on Python 3.12+ and drops link/device members on 3.10
  and 3.11, where that argument does not exist.
- **`download_file` sanitises the destination filename.** A `filename` of
  `../../x` escaped the library directory; both the explicit argument and the
  name derived from the URL now go through `Path(...).name`.

### Fixed

- **Build and package state is keyed by project.** `~/.unreal-mcp/build.json`
  and `package.json` held a single slot, so two projects built in parallel — or
  two MCP clients on the same machine — overwrote each other's state and
  `ue_build_status` answered about the wrong build. Both files now store one
  entry per `.uproject`; `ue_build_status` and `ue_package_status` accept an
  optional `uproject` argument and default to the most recently started job.
  The previous single-entry format is still read.

## [0.2.0] — 2026-07-29

### Added

- **`ue_cpp_class_create`** — generates a compilable C++ class in the project
  module, with the Unreal boilerplate written correctly: `UCLASS`,
  `GENERATED_BODY`, the `_API` macro, and the right parent header (`AIController.h`
  is not under `GameFramework/`; `ActorComponent` starts with an `A` but the
  class is `UActorComponent`). Replicated properties also get
  `GetLifetimeReplicatedProps` and their `DOREPLIFETIME` entries, which are
  silent no-ops when forgotten, and pointer types get forward declarations,
  without which the header does not compile. If the project is Blueprint-only,
  the whole module is created (`Build.cs`, `Target.cs`,
  `IMPLEMENT_PRIMARY_GAME_MODULE`, and the `Modules` entry in the `.uproject`).
- **`ue_reparent_blueprint`** — reassigns a Blueprint's parent, typically to a
  generated C++ class. Together with the tool above this is the answer to the
  "Blueprint graphs are not scriptable" limit: logic lives in the C++ parent,
  the Blueprint stays the container for components and values.
- **Material tools** — `ue_create_material`, `ue_create_material_instance` and
  `ue_assign_material`. Material graphs *are* fully scriptable from Python, so
  nodes are really created and wired. Texture channels can be inferred from the
  filename, matching the ambientCG and Poly Haven naming conventions.
- **`ue_screenshot`** — captures the editor viewport to a PNG, so an agent can
  look at what it built instead of inferring it from coordinates.
- **`ue_set_actor_property`** and **`ue_list_actor_components`** — set
  properties on *placed* actors and their components (a mesh, a light's
  intensity, a trigger radius). Vectors and colours arrive as JSON dicts and
  asset paths are resolved automatically.
- **`ue_spawn_many`** — spawns a whole list of actors in one round trip and one
  undo transaction. A failure on one entry does not lose the others.
- `engine_root` is now accepted by `ue_project_create`, `ue_engine_templates`,
  `ue_editor_open`, `ue_build_start` and `ue_package_start`. The README
  documented it as the primary escape hatch, but no tool exposed it.
- `target_platform` on `ue_package_start`.
- Tests covering the MCP surface itself: that the server instructions reach the
  model, that the name is explicit, and that every tool carries a description
  and an input schema. These are what an agent reads before deciding what to
  call, and losing them breaks nothing visibly.

### Changed

- **Editor-side helpers are installed once per editor session** instead of
  being prepended to every snippet. Each call used to ship and re-execute
  ~750 lines of Python; now it imports a cached module, and the bridge
  reinstalls automatically when the editor restarts or the helpers change.
- **Actor edits are wrapped in `ScopedEditorTransaction`**, so spawning,
  moving, deleting and property changes are undoable with Ctrl+Z.
- **All JSON result keys are English.** Some were Italian (`nota`, `riuscito`,
  `fallito`, `in_corso`, `motivo`, `avviato`) — these are read by a model, so
  the mixed vocabulary was a real inconsistency. `ue_live_compile` now returns
  `started`/`succeeded`/`failed`/`in_progress`/`note`.
- **Linux and macOS support completed** for the local layer: `Build.sh` and
  `RunUAT.sh` are used where appropriate, process detection goes through
  `pgrep`/`pkill` instead of returning `False`, and the build platform defaults
  to the host. Previously the "is the editor closed?" guard silently passed on
  non-Windows and the build failed later.
- **Stays on the `mcp` 1.x line, deliberately.** v2 renamed `FastMCP` to
  `MCPServer` and moved it to `mcp.server.mcpserver`, so the import here would
  raise `ModuleNotFoundError` on it. 1.x is not legacy — 1.29.0 shipped the same
  day as 2.0.0 — so the pin costs nothing today. Porting is a three-line change
  when the ecosystem settles.

  One thing to know before making it: v2 inserted `title` and `description`
  *before* `instructions` in the constructor's positional parameters. A call
  passing instructions positionally keeps working while the text silently lands
  in the title and the model stops receiving it. They are passed by keyword
  here, and a test asserts they reach the model.
- The HTTP client is now closed when the server shuts down.
- Error messages for a missing actor or component list what *is* available.

### Security

- **`bAllowConsoleCommandRemoteExecution` is now `False`** in generated projects
  and in the starter project. Epic documents it as *"Enable calling
  'ExecuteConsoleCommand' through the web api"* — it gates one HTTP route, and
  the bridge never uses it. The console commands it does need
  (`LiveCoding.Compile`, `WebControl.StartServer`, `HighResShot`) are issued
  from inside Python via `unreal.SystemLibrary.execute_console_command`, which
  is unaffected. Existing projects can flip it without losing anything.
- `SECURITY.md` spells out what the server can actually do — it executes
  arbitrary Python inside your editor by design — and how to reach the editor
  from another machine safely, if you must.

### Fixed

- **A failed connection now always explains itself.** `httpx.ConnectTimeout`
  does not inherit from `ConnectError` — it is a `TimeoutException` — so it
  fell through the handler and reached the user as a raw httpx traceback. A
  closed port usually *refuses* the connection, but a firewall that drops
  packets instead produces a timeout, which is precisely the case where the
  diagnosis is most needed. Caught in the first CI run on Windows.
- **The test suite no longer reads the machine's real Unreal installs.**
  `find_engines()` queries the Windows registry and the Epic Launcher manifest,
  so the suite passed on a clean runner and failed on the developer's own
  machine — where it matters most. Isolation is now an autouse fixture, applied
  to every test rather than one at a time.
- `USER_AGENT` said `0.2` while the package version was `0.1.0`.

### Repository

- GitHub Actions CI: pytest on Python 3.10–3.13, on Linux and Windows, plus
  ruff. The suite needs no Unreal install, so it runs anywhere.
- ruff configured, with the deliberate exceptions documented inline.
- `pyproject.toml` carries the metadata needed to publish to PyPI, and the
  sdist now ships `StarterProject/` and `README.it.md`, both linked from the
  README.
- Issue templates and Dependabot, with major `mcp` bumps ignored on purpose.
- Removed `unreal_project_files/`, a stale Italian copy of the `init_unreal.py`
  that already ships in `StarterProject/`.

## [0.1.0]

Initial release: 49 tools over the Remote Control API and in-editor Python,
UE 5.0+, no C++ plugin to compile.

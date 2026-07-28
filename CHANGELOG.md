# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

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

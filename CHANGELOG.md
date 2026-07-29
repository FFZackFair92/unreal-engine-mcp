# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.5.4] — 2026-07-29

### Fixed

- **Paths coming out of the editor are now absolute.** `unreal.Paths.*` returns
  paths relative to the *engine binaries* directory, not the project:
  `project_saved_dir()` arrives as
  `../../../../../../Users/…/Saved/`. Inside the editor that resolves fine —
  its working directory is the one they are relative to — but this server hands
  them to a process living somewhere else, which then cannot find anything.

  `ue_screenshot` returned `captured: false`, and no image, for a PNG the
  engine had written correctly. `ue_status` reported a `project_file` nobody
  could open. Everything is now passed through
  `Paths.convert_relative_path_to_full` and normalised, separators included —
  `os.path.join` on Windows was adding backslashes to a string that already
  had a mix.

  The fake engine used in tests returned absolute paths, which is why 265 tests
  went green over a bug that appears on the first contact with a real editor.
  It now returns relative ones, like the engine does, and a test asserts the
  paths that come back are absolute.

## [0.5.3] — 2026-07-29

### Fixed

- **A transport that starts failing is re-evaluated instead of trusted
  forever.** With `UE_MCP_TRANSPORT=auto` the choice was made on the first call
  and frozen. That first call is usually the least informed one: `ue_editor_open`
  probes while the editor is still loading, so the native channel has not come
  up yet, HTTP answers, and `remotecontrol` is pinned for the rest of the
  session — including after the native channel becomes available a minute
  later, and including when the HTTP path cannot actually do anything because
  the project has no `DefaultRemoteControl.ini` and every call comes back

      Object Default__PythonScriptLibrary cannot be accessed remotely

  Now a failure on the pinned transport clears the choice and tries the other
  one once. An explicitly configured `transport` is never second-guessed: asking
  for `remotecontrol` means wanting `remotecontrol`, not a surprise.

  Found by driving a real editor: the build compiled, the editor opened, the
  bridge reported ready, and every tool still failed on a gate that the default
  transport was designed to make irrelevant.

## [0.5.2] — 2026-07-29

### Fixed

- **Builds, packaging and renders now run with a usable `TMP`.** This is the
  real cause of the build that could never start, and it was never a lock at
  all.

  An MCP client does not launch a server with the user's environment; it passes
  a reduced allowlist, and the default one on Windows contains `TEMP` but
  **not `TMP`**. `Engine/Build/BatchFiles/Build.bat` builds its lock file with

      set LockFile=%tmp%\%LockFile::=%.lock

  so with `TMP` missing the path collapses to the root of the drive, where a
  standard user cannot write. The exclusive open on handle 9 fails every single
  time, the script falls into its error branch and prints *"is already running,
  waiting for existing script to terminate..."* forever.

  Every symptom followed from that one missing variable: it failed instantly
  and always, no process ever held the lock, deleting `%TMP%\…​.lock` changed
  nothing because that was not the file being opened, and the identical
  generated script compiled in fifteen seconds when run from a normal shell.

  Child processes now get an environment with `TMP` and `TEMP` both pointing at
  a directory that exists — for builds, packaging, renders and the editor
  launch alike. Tests assert that `Popen` actually receives it, not merely that
  the helper exists.

## [0.5.1] — 2026-07-29

### Added

- **`ue_build_unblock`** — finds, and on request terminates, the processes
  holding the build lock.

  0.4.4 learned to *report* a blocked build but still told you to go hunting in
  Task Manager, and the advice did not work: it searched by image name. On
  UE 5, UnrealBuildTool is a .NET assembly running inside `dotnet.exe` and
  Epic's scripts run inside `cmd.exe`, so `taskkill /IM UnrealBuildTool.exe`
  matches nothing and the lock looks impossible to clear. This searches the
  **command line** instead, which is where those names actually appear.

  `dry_run=True` by default: matching on command line can catch a `dotnet.exe`
  doing something else entirely, so the list comes first and the killing is a
  second, explicit call. `UnrealEditor.exe` and `UnrealEditor-Cmd.exe` are never
  matched — they use the build, they do not block it.

  `ue_build_unblock` also inspects the **lock file**, which is the half of the
  problem that processes do not explain. `Build.bat` does not use a system
  mutex: lines 18-20 build a filename from its own full path (backslashes to
  dashes, colons stripped) under `%TMP%`, then open it exclusively on handle 9.
  The lock *is* that open handle. If the open fails the script prints
  "is already running" and loops forever — and if the file is left unopenable
  rather than held, there is no process to kill and the build waits for eternity.
  The filename is built with `ntpath` so it comes out right even when the server
  runs on Linux against a Windows engine path.

  `ue_build_status` now points at this tool instead of at Task Manager.

- **Each build gets its own log file.** They all wrote to `mcp_build.log`, so
  several accidental concurrent builds produced one unreadable mixture — the
  only line visible was some waiter's "already running", while the run that
  actually held the lock was invisible. This one cost real time to diagnose.

## [0.5.0] — 2026-07-29

### Added

- **`ue_console_command`** — runs an editor console command and returns what it
  printed. Console commands do not return values, they write to the log, so the
  tool measures the log before and after and hands back only the new lines;
  otherwise the answer would be "done" and nothing else, which tells an agent
  nothing. When a command prints nothing the response says so rather than
  leaving it ambiguous. Goes through the editor's Python interpreter, not the
  `bAllowConsoleCommandRemoteExecution` gate of the Remote Control API, which
  stays off — the same caveat as `ue_exec_python` applies.

- **`ue_render_sequence` and `ue_render_status`** — Movie Render Queue renders
  of a Level Sequence, in a headless `UnrealEditor-Cmd` process rather than in
  the open editor. In-editor MRQ is asynchronous and would hold the editor for
  the whole render with no clean way to await it over the bridge; a separate
  process starts, writes and finishes, and is followed exactly like a build.

  `config` — a saved Movie Pipeline preset — is how output format, resolution
  and directory are chosen; without one, MRQ falls back to the project defaults
  and may write nothing at all. Which is why `succeeded` is decided by the
  files produced, not by the exit code: a headless MRQ run can exit 0 having
  rendered zero frames. Produced files are identified by diffing the output
  directory against a snapshot taken at start, not by modification time —
  comparing a process clock against a filesystem's finds nothing at all on a
  network share or a mount with a skewed clock.

  Content paths are validated before they reach the command line: the engine
  re-tokenizes its own command line on whitespace, so a path containing a space
  and a dash would become another switch.

### Note on what is verified

The render tools are covered by tests of the pure parts — format mapping,
command construction, path validation, output collection, status logic — with a
faked process. **No test in this project has ever rendered a frame with a real
engine.** What remains unverified is whether this command line produces output
against a live Unreal installation.

## [0.4.4] — 2026-07-29

Three failures from one afternoon of driving a real editor, all variations on
the same theme: something was stuck and the tools reported it as progress.

### Added

- **A build blocked on a lock is no longer reported as a build in progress.**
  Epic's batch files take a global lock; when a previous instance is orphaned —
  typically a child of an editor killed with `taskkill`, which `/T` does not
  always reach — it never releases it. `Build.bat` waits forever, the log stays
  one line long, and `ue_build_status` said `running: true` indefinitely while
  suggesting a longer `wait_seconds`, which is precisely the advice that cannot
  work. There is now a `blocked` flag and a `reason` naming the processes to
  look for, including the `dotnet.exe` that runs UnrealBuildTool on UE 5 and so
  never appears under its own name.
- **`ue_build_start` refuses to start a second build of the same project.**
  Both take the same lock, so the new one queues behind the stuck one and the
  symptom becomes "the build never starts". `force=True` after cleaning up the
  leftovers.
- **`ue_editor_close(force=True)` cleans up the processes it orphans.** The
  forced kill is what creates them, so that is where they get cleared;
  `orphans_cleaned` reports what was terminated.

## [0.4.3] — 2026-07-29

### Fixed

- **`ue_editor_open` no longer times out on a launch that worked.** The default
  `wait_seconds` was 240, well past the 60-second request timeout most MCP
  clients apply, so the call came back `Request timed out` while the editor was
  starting perfectly well — `ue_editor_status` showed the pid alive the whole
  time. The default is now 50, and when the bridge is not up yet the answer
  says so and points at `ue_editor_status` for polling. Raising `wait_seconds`
  past the client timeout brings the old behaviour back, so a test now pins the
  default below 60.

### Added

- **Stale C++ modules are caught before the editor is launched.** When the
  project's compiled modules do not match the engine opening them, Unreal does
  not fail and does not log anything: it opens a *"the following modules are
  missing or built with a different engine version"* dialog **behind the splash
  screen**. From outside, all you see is an editor stuck at
  `0% - Initializing..` forever, with SDK detection as the last line in the log.

  `launch_editor` now compares the `BuildId` in the project's
  `Binaries/Win64/UnrealEditor.modules` against the engine's
  `Engine/Build/Build.version` and refuses to launch on a mismatch, naming both
  ids and listing the three ways out: rebuild with `ue_build_start`, point at
  the right engine, or `skip_module_check=True` and answer the dialog by hand.
  A project with a `Source` folder but no compiled binaries is caught the same
  way. Blueprint-only projects have nothing to compare and are never blocked;
  an unreadable engine `Build.version` skips the check rather than blocking.

  `ue_project_info` also reports `modules_build_id` and `binaries_present`.

## [0.4.2] — 2026-07-29

### Fixed

- **`ue_editor_open` no longer demands the Remote Control plugin.** The native
  transport added in 0.4.0 needs only the *Python Editor Script Plugin*, but
  the precondition in `launch_editor` still required *Remote Control* as well —
  so a project set up exactly as the 0.4.0 README describes could not be opened
  by this server's own tool. Found on the first real attempt to use it.

  `ue_project_info` now reports `pyremote_ready` and `remotecontrol_ready`
  separately (`bridge_ready` stays, meaning the former), `launch_editor` asks
  only for the Python plugin, and the `-RCWebControlEnable` /
  `-RCWebInterfaceEnable` flags go on the editor command line only when that
  plugin is actually enabled — passing them otherwise just fills the log with
  warnings that read like errors.

## [0.4.1] — 2026-07-29

### Fixed

- **`uvx unreal-engine-mcp` now works.** The distribution was renamed to
  `unreal-engine-mcp` in 0.3.1 but the only console script stayed `unreal-mcp`,
  and `uvx <package>` runs an executable named after the package — so it found
  nothing and exited, which a client reports only as "Server disconnected".
  The README had advertised that exact command without it ever being run. There
  is now an `unreal-engine-mcp` alias alongside `unreal-mcp`; both start the
  same server, and `uvx --from unreal-engine-mcp unreal-mcp` keeps working.

## [0.4.0] — 2026-07-29

### Added

- **A second transport: the engine's own Python remote execution channel**, and
  it is now the default. Setting up an existing project used to mean two
  plugins plus a hand-written `DefaultRemoteControl.ini` with two separate
  security gates — the step where people gave up. The native channel needs the
  *Python Editor Script Plugin* and one checkbox, *Enable Remote Execution*.
  Discovery is a UDP multicast ping on `239.0.0.1:6766`; the command channel is
  TCP, opened **by the editor towards us**, which is why a firewall shows up as
  "answered discovery, never connected" and the error says so.

  `UE_MCP_TRANSPORT=auto` (default) tries the native channel and falls back to
  the Remote Control API, so existing setups keep working untouched and the
  HTTP route stays the answer for an editor on another machine, or a network
  that swallows multicast. `ue_status` reports which one is live.
- **Viewport camera** — `ue_get_camera`, `ue_set_camera`, `ue_focus_actor`.
  `ue_screenshot` returned an image but pointed wherever the camera happened to
  be; framing what you just built is what makes the picture worth its tokens.
- **The agent is told not to trust the world origin.** Real levels are often
  built thousands of units from `[0,0,0]`, so an actor spawned there can be
  off-screen and invisible — a failure that looks like success. The server
  instructions now say to anchor new actors to an existing one or to the
  camera, and to verify with `ue_focus_actor` + `ue_screenshot`.
- **`uvx unreal-engine-mcp`** in the README: no install step at all.

## [0.3.1] — 2026-07-29

### Fixed

- **`extract_archive` raises `AssetError` on every Python version.** On 3.12+
  the `filter="data"` pass raises tarfile's own exceptions —
  `LinkOutsideDestinationError` and friends — which are not `AssetError` and
  reached the caller as a raw traceback. The archive was still refused, so the
  protection held; only the error type leaked. Caught in CI, which runs 3.10
  through 3.13: the 0.3.0 fix had only been exercised on the 3.10/3.11 branch.

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
  `pip install unreal-engine-mcp` instead of a clone. The distribution is not
  called `unreal-mcp` because PyPI rejects that as too similar to the existing
  `unrealmcp`; the import name and the console script are unchanged.

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

# Unreal Engine MCP

[![CI](https://github.com/FFZackFair92/unreal-engine-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/FFZackFair92/unreal-engine-mcp/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%20%E2%80%93%203.13-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

[Italiano](README.it.md)

An [MCP](https://modelcontextprotocol.io) server that lets an AI agent drive
Unreal Engine 5: create projects, import assets, build levels and Blueprints,
configure replication, compile C++, run Play In Editor and package the game.

**No C++ plugin to compile.** It uses two plugins that already ship with the
engine — *Python Editor Script Plugin* and *Remote Control API* — and talks to
the editor over local HTTP.

```
                    ┌─ LOCAL layer  ── processes + public HTTP
                    │    UnrealEditor.exe, UnrealBuildTool, RunUAT, asset downloads
Agent ──stdio──▶ MCP │
                    └─ EDITOR layer ── HTTP :30010 (Remote Control API)
                         └─ ExecutePythonCommandEx → running editor
```

The local layer exists because the Remote Control API only works against an
editor that is **already running**: creating a project, launching it, compiling
and packaging all have to happen at the process level.

- **143 tools** and **5 resources** — [full reference](docs/TOOLS.md)
- **435 tests**, none of which need Unreal installed
- **[Unreal automation notes](docs/UNREAL-NOTES.md)** — the API traps found the hard way

---

## Requirements

- Unreal Engine **5.0 or newer** (developed and tested against 5.8 — see
  [version compatibility](#unreal-version-compatibility))
- Python 3.10+
- Windows, Linux or macOS. Development happens on Windows, which is where the
  local layer is exercised most; the Linux and macOS paths (`Build.sh`,
  `RunUAT.sh`, `pgrep`) are implemented and covered by CI, but less battle-tested.
  The editor layer is platform-neutral.

### Unreal version compatibility

The server detects what the running engine supports at startup of each call —
`ue_status` reports it under `capabilities` — and fails with an explicit
message instead of a cryptic Python error when a feature is missing:

| Feature | Works on |
|---|---|
| Actors, levels, spawn/transform, PIE, project settings, build & packaging, Sound Cues | **5.0+** |
| Blueprint creation, components (`SubobjectDataSubsystem`), reparenting | **5.0+** |
| Materials, material instances, screenshots, C++ class generation | **5.0+** |
| glTF/`.glb` import via Interchange | **5.2+** (earlier: enable the *glTF Importer* plugin; the tool tells you when that is the problem) |
| Blueprint member variables + per-variable replication (`ue_add_variable`) | **5.4+** (no Python API before that — the tool says so explicitly) |
| MetaSounds | any 5.x with the *MetaSound* plugin enabled |

Custom engine builds are fine: detection is based on the actual Python API
surface, not on the version number.

## Install

```bash
pip install unreal-engine-mcp
```

Or run it without installing anything, if you have [uv](https://docs.astral.sh/uv/):

```bash
uvx unreal-engine-mcp
```

Or from source, to hack on it:

```bash
git clone https://github.com/FFZackFair92/unreal-engine-mcp.git
cd unreal-engine-mcp
pip install -e .
```

## Connect it to your client

The server speaks standard MCP over stdio, so **any MCP-capable client works** —
Claude, Cursor, VS Code, Windsurf, OpenAI Codex, custom agents. The command is
always the same; only where the config lives differs.

### Claude Desktop / Cowork

`Settings ▸ Developer ▸ Edit Config`, then add to `mcpServers`:

```json
{
  "mcpServers": {
    "unreal-mcp": {
      "command": "python",
      "args": ["-m", "unreal_mcp.server"],
      "env": { "UE_MCP_PORT": "30010" }
    }
  }
}
```

### Claude Code

```bash
claude mcp add unreal-mcp -- python -m unreal_mcp.server
```

### Cursor

`~/.cursor/mcp.json` (global) or `.cursor/mcp.json` in the project — same JSON
shape as Claude Desktop (`mcpServers` root key).

### VS Code (Copilot agent mode)

`.vscode/mcp.json` — note the root key is `servers` here:

```json
{
  "servers": {
    "unreal-mcp": { "type": "stdio", "command": "python", "args": ["-m", "unreal_mcp.server"] }
  }
}
```

### Windsurf

`~/.codeium/windsurf/mcp_config.json` — same `mcpServers` shape as Claude Desktop.

### OpenAI Codex CLI

`~/.codex/config.toml`:

```toml
[mcp_servers.unreal-mcp]
command = "python"
args = ["-m", "unreal_mcp.server"]
```

### OpenAI Agents SDK (custom agents)

```python
from agents.mcp import MCPServerStdio

unreal = MCPServerStdio(params={"command": "python", "args": ["-m", "unreal_mcp.server"]})
```

Restart the client completely after editing its config. In every case `python`
must be the interpreter where you ran `pip install -e .` — use an absolute path
if in doubt.

> **Note:** ChatGPT's connector UI only accepts *remote* MCP servers. This
> server is stdio/local by design (it drives processes on your machine), so use
> it from clients with stdio support — the ones above — or wrap it with an
> MCP proxy if you really need HTTP.

## Set up the Unreal side

### Starter project — zero setup, zero builds

Copy [`StarterProject/`](StarterProject/) wherever you like and open the
`.uproject` with any UE 5.0+: plugins enabled, security config in place, web
server auto-started. Being Blueprint-only with engine plugins, **there is
nothing to compile** — unlike starters that bundle a C++ plugin.

### New project — nothing to do by hand

Ask the agent to run `ue_project_create`. It writes the `.uproject` with the
required plugins enabled, the security flags Remote Control needs, and an
`init_unreal.py` that starts the web server on every editor launch.

```
ue_engine_list  →  ue_project_create  →  ue_editor_open  →  ue_status
```

### Existing project — two steps

1. **Enable the plugin**: `Edit ▸ Plugins` → *Python Editor Script Plugin*.
   Restart when prompted.

2. **Tick one box**: `Edit ▸ Project Settings` → search *Python* → check
   **Enable Remote Execution**.

That is the whole setup. The server discovers the editor over the engine's own
Python remote execution channel — no config file, no HTTP port.

<details>
<summary>The HTTP route (Remote Control API), for when multicast will not do</summary>

The native channel finds the editor with a UDP multicast ping on the local
machine. That is the easy path, but it does not cross a subnet, and some
corporate networks and VPN adapters swallow multicast entirely. In those cases
— or with the editor on **another machine** — use the Remote Control API
instead, with `UE_MCP_TRANSPORT=remotecontrol`.

1. **Enable the plugins**: *Python Editor Script Plugin* and *Remote Control
   API*. Restart when prompted.

2. **Allow remote Python.** Create `Config/DefaultRemoteControl.ini`:

   ```ini
   [/Script/RemoteControlCommon.RemoteControlSettings]
   bAutoStartWebServer=True
   bAutoStartWebSocketServer=True
   RemoteControlHttpServerPort=30010
   bEnableRemotePythonExecution=True
   bAllowAnyRemoteFunctionCall=False
   +CustomAllowedRemoteFunctionCalls=(ClassPath="/Script/PythonScriptPlugin.PythonScriptLibrary")
   bAllowConsoleCommandRemoteExecution=False
   ```

   These are **two separate gates** — one unlocks the object, the other the
   function call — and they fail with different errors. `DefaultEngine.ini` is
   the wrong file: `URemoteControlSettings` is declared
   `UCLASS(config = RemoteControl)`. Restart the editor.

   On engines that predate some of these keys they are simply ignored —
   older versions did not gate those calls in the first place.

   The server listens on `127.0.0.1` only, so remote Python execution is not
   reachable from outside the machine.

   What the server can do once connected, and how to open it up safely if you
   really need to reach the editor from another machine, is in
   [SECURITY.md](SECURITY.md).

   `bAllowConsoleCommandRemoteExecution` stays **off**. It enables
   `ExecuteConsoleCommand` *through the web API*, which this server never calls
   — the console commands it does need (`LiveCoding.Compile`,
   `WebControl.StartServer`, `HighResShot`) are issued from inside Python via
   `unreal.SystemLibrary.execute_console_command` and do not go through that
   gate. Older versions of this project set it to `True`; existing projects can
   flip it to `False` without losing anything.

3. **Check it**: open `http://127.0.0.1:30010/remote/info` in a browser. JSON
   back means you are connected.

</details>

### Which transport is in use

`ue_status` reports it. By default (`UE_MCP_TRANSPORT=auto`) the server tries
the native channel first and falls back to HTTP, so a project set up either way
just works. Set `pyremote` or `remotecontrol` to pin one.

## What it can do

| Area | Highlights |
|---|---|
| **Projects** | Find engine installs, create projects from a spec, manage plugins |
| **Editor lifecycle** | Open (waiting for the bridge), status, clean shutdown |
| **C++** | Generate compilable classes with the boilerplate written correctly, then reparent Blueprints onto them |
| **Build** | Compile C++ in the background; `ue_live_compile` recompiles **with the editor open** via Live Coding |
| **Package** | `RunUAT BuildCookRun` → standalone executable, with phase reporting |
| **Assets** | Import `.glb`/`.gltf`/`.fbx`/`.wav`, list and search the Content Browser |
| **Levels** | Create and open levels, spawn/move/delete actors, batch spawn, set properties on placed actors |
| **Materials** | Build material graphs, wire PBR textures, material instances, assign to actors |
| **Blueprints** | Create, add components, typed variables with replication, class defaults, reparent, compile |
| **Networking** | Replication flags, multi-client PIE, project settings |
| **Audio** | Import wavs, MetaSound sources, Sound Cues |
| **Free assets** | Poly Haven, ambientCG and Kenney downloads (all CC0), plus any direct URL |
| **Feedback** | `ue_screenshot` captures the viewport, so the agent can see what it built |

Full parameter list in [docs/TOOLS.md](docs/TOOLS.md).

### Working around the Blueprint graph limit

**Blueprint node graphs cannot be authored from Python.** `EdGraph.Nodes` is
protected, pins are not exposed and there is no linking API — this is a hard
limit of the engine, not of this server. Details in
[docs/UNREAL-NOTES.md](docs/UNREAL-NOTES.md).

What works instead: **put the logic in a C++ parent class.** The Blueprint stays
the container for components and tweakable values; the behaviour is inherited.

```
ue_cpp_class_create      # writes the class, and the whole C++ module if the
                         # project was Blueprint-only
ue_editor_close
ue_build_start           # poll ue_build_status until running=false
ue_editor_open
ue_reparent_blueprint    # the Blueprint now inherits the behaviour
```

Blueprint variables whose names match a `UPROPERTY` on the new parent are
absorbed by it, so values set in the editor survive the move. Functions marked
`BlueprintCallable` become callable from the graph — an agent can build the
vocabulary the designer then wires up by hand.

Material graphs, unlike Blueprint graphs, *are* fully scriptable:
`ue_create_material` really does create and connect the nodes.

### Other limits

- **Compiling C++ needs the editor closed**, unless the change only touches
  function bodies — then `ue_live_compile` works with it open.
- **Packaging always needs the editor closed**: the build step rewrites the DLLs
  the editor holds in memory.
- **Feature availability varies with the engine version** — see the
  [compatibility table](#unreal-version-compatibility); `ue_status` reports
  what the running engine supports.
- **Fab/Marketplace content** has no public API. Those tools shell out to the
  community client [`legendary`](https://github.com/derrod/legendary).

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `UE_MCP_TRANSPORT` | `auto` | `auto`, `pyremote` (native channel) or `remotecontrol` (HTTP) |
| `UE_MCP_HOST` / `UE_MCP_PORT` | `127.0.0.1` / `30010` | Remote Control endpoint |
| `UE_MCP_TIMEOUT` | `180` | Per-call timeout in seconds |
| `UE_MCP_PROJECT` | — | Which project to drive when several editors are open |
| `UE_MCP_MULTICAST_GROUP` / `_PORT` | `239.0.0.1` / `6766` | Discovery endpoint for the native channel |
| `UE_MCP_MULTICAST_BIND` | `0.0.0.0` | Interface the discovery ping goes out of — set it when several adapters (VPN, WSL, Hyper-V) hide the editor |
| `UE_MCP_MULTICAST_TTL` | `0` | Keeps discovery on this machine. Raising it exposes arbitrary code execution to the network |
| `UE_MCP_ENGINE_DIRS` | — | Extra folders to search for engine installs |
| `UE_MCP_LIBRARY` | `~/UnrealAssetLibrary` | Where downloaded assets land |
| `UE_MCP_MAX_DOWNLOAD` | 4 GiB | Per-file download cap |

If the engine sits somewhere unusual, you can also drop an `mcp_engine.txt` next
to the `.uproject` containing its path, or pass `engine_root` explicitly.

## Development

The test suite runs **without Unreal installed**: `tests/fake_unreal.py` stands
in for the `unreal` module and `tests/fake_server.py` emulates the Remote Control
API while *actually executing* the generated snippets — so the whole chain
tool → snippet → harness → result is covered.

```bash
pip install -e ".[dev]"
pytest -q
```

Adding a tool: a reusable helper in `src/unreal_mcp/ue_side.py` (editor side),
an `@mcp.tool()` function in `server.py`, and a test in `tests/`. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the conventions worth knowing.

`ue_side.py` is installed into the running editor as a module and keyed by a
hash of its source, so editor-side changes take effect on the next call without
restarting the server — and every other call is just a small snippet that
imports it. Changes to `server.py` or `local.py` need a client restart.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `No response from http://127.0.0.1:30010` | Editor closed, or the web server never started → console: `WebControl.StartServer` |
| `Object Default__PythonScriptLibrary cannot be accessed remotely` | Missing `bEnableRemotePythonExecution` in `DefaultRemoteControl.ini` |
| `Executing function 'ExecutePythonCommandEx' is not allowed` | Missing `CustomAllowedRemoteFunctionCalls` entry |
| `404 on /remote/object/call` | *Remote Control API* plugin not enabled |
| `NameError: name 'unreal' is not defined` | *Python Editor Script Plugin* not enabled |
| `Unable to build while Live Coding is active` | `LiveCodingConsole.exe` outlives the editor — kill it (the build tools do this automatically) |
| `No Unreal Engine installation found` | Set `UE_MCP_ENGINE_DIRS`, add `mcp_engine.txt`, or pass `engine_root` |
| `ModuleNotFoundError: No module named 'mcp.server.fastmcp'` | `mcp` 2.x is installed; this server targets the 1.x line. `pip install "mcp<2"` — reinstalling the package does it for you |

## Licence

MIT — see [LICENSE](LICENSE).

## References

- [Remote Control for Unreal Engine](https://dev.epicgames.com/documentation/en-us/unreal-engine/remote-control-for-unreal-engine)
- [Remote Control Quick Start](https://dev.epicgames.com/documentation/en-us/unreal-engine/remote-control-quick-start-for-unreal-engine)
- [Importing glTF files](https://dev.epicgames.com/documentation/en-us/unreal-engine/importing-gltf-files-into-unreal-engine)
- [Poly Haven API](https://github.com/Poly-Haven/Public-API) · [ambientCG API](https://docs.ambientcg.com/api/v2/full_json/)

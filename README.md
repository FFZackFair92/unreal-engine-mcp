# Unreal Engine MCP

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

- **49 tools** — [full reference](docs/TOOLS.md)
- **91 tests**, none of which need Unreal installed
- **[Unreal automation notes](docs/UNREAL-NOTES.md)** — the API traps found the hard way

---

## Requirements

- Unreal Engine **5.0 or newer** (developed and tested against 5.8 — see
  [version compatibility](#unreal-version-compatibility))
- Python 3.10+
- Windows for the local layer (process handling and build scripts are Windows-specific).
  The editor layer is platform-neutral.

### Unreal version compatibility

The server detects what the running engine supports at startup of each call —
`ue_status` reports it under `capabilities` — and fails with an explicit
message instead of a cryptic Python error when a feature is missing:

| Feature | Works on |
|---|---|
| Actors, levels, spawn/transform, PIE, project settings, build & packaging, Sound Cues | **5.0+** |
| Blueprint creation, components (`SubobjectDataSubsystem`) | **5.0+** |
| glTF/`.glb` import via Interchange | **5.2+** (earlier: enable the *glTF Importer* plugin; the tool tells you when that is the problem) |
| Blueprint member variables + per-variable replication (`ue_add_variable`) | **5.4+** (no Python API before that — the tool says so explicitly) |
| MetaSounds | any 5.x with the *MetaSound* plugin enabled |

Custom engine builds are fine: detection is based on the actual Python API
surface, not on the version number.

## Install

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

### Existing project — three steps

1. **Enable the plugins**: `Edit ▸ Plugins` → *Python Editor Script Plugin* and
   *Remote Control API*. Restart when prompted.

2. **Allow remote Python.** Create `Config/DefaultRemoteControl.ini`:

   ```ini
   [/Script/RemoteControlCommon.RemoteControlSettings]
   bAutoStartWebServer=True
   bAutoStartWebSocketServer=True
   RemoteControlHttpServerPort=30010
   bEnableRemotePythonExecution=True
   bAllowConsoleCommandRemoteExecution=True
   bAllowAnyRemoteFunctionCall=False
   +CustomAllowedRemoteFunctionCalls=(ClassPath="/Script/PythonScriptPlugin.PythonScriptLibrary")
   ```

   These are **two separate gates** — one unlocks the object, the other the
   function call — and they fail with different errors. `DefaultEngine.ini` is
   the wrong file: `URemoteControlSettings` is declared
   `UCLASS(config = RemoteControl)`. Restart the editor.

   On engines that predate some of these keys they are simply ignored —
   older versions did not gate those calls in the first place.

   The server listens on `127.0.0.1` only, so remote Python execution is not
   reachable from outside the machine.

3. **Check it**: open `http://127.0.0.1:30010/remote/info` in a browser. JSON
   back means you are connected.

## What it can do

| Area | Highlights |
|---|---|
| **Projects** | Find engine installs, create projects from a spec, manage plugins |
| **Editor lifecycle** | Open (waiting for the bridge), status, clean shutdown |
| **Build** | Compile C++ in the background; `ue_live_compile` recompiles **with the editor open** via Live Coding |
| **Package** | `RunUAT BuildCookRun` → standalone executable, with phase reporting |
| **Assets** | Import `.glb`/`.gltf`/`.fbx`/`.wav`, list and search the Content Browser |
| **Levels** | Create and open levels, spawn/move/delete actors |
| **Blueprints** | Create, add components, typed variables with replication, class defaults, compile |
| **Networking** | Replication flags, multi-client PIE, project settings |
| **Audio** | Import wavs, MetaSound sources, Sound Cues |
| **Free assets** | Poly Haven, ambientCG and Kenney downloads (all CC0), plus any direct URL |

Full parameter list in [docs/TOOLS.md](docs/TOOLS.md).

### Known limits

- **Blueprint node graphs cannot be authored.** UE 5.8 does not expose graph
  editing to Python: `EdGraph.Nodes` is protected, pins are not exposed and
  there is no linking API. Variables, components and defaults are scriptable;
  logic belongs in C++ or in the editor. Details in [docs/UNREAL-NOTES.md](docs/UNREAL-NOTES.md).
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
| `UE_MCP_HOST` / `UE_MCP_PORT` | `127.0.0.1` / `30010` | Remote Control endpoint |
| `UE_MCP_TIMEOUT` | `180` | Per-call timeout in seconds |
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
an `@mcp.tool()` function in `server.py`, and a test in `tests/`.
`ue_side.py` is re-read from disk on every call, so editor-side changes take
effect without restarting the server; changes to `server.py` or `local.py` need
a client restart.

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

## Licence

MIT — see [LICENSE](LICENSE).

## References

- [Remote Control for Unreal Engine](https://dev.epicgames.com/documentation/en-us/unreal-engine/remote-control-for-unreal-engine)
- [Remote Control Quick Start](https://dev.epicgames.com/documentation/en-us/unreal-engine/remote-control-quick-start-for-unreal-engine)
- [Importing glTF files](https://dev.epicgames.com/documentation/en-us/unreal-engine/importing-gltf-files-into-unreal-engine)
- [Poly Haven API](https://github.com/Poly-Haven/Public-API) · [ambientCG API](https://docs.ambientcg.com/api/v2/full_json/)

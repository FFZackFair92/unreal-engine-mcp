# StarterProject

A blank Unreal project preconfigured for **unreal-mcp**. Unlike plugin-based
MCP starters, there is **nothing to build**: the two plugins it needs (*Python
Editor Script Plugin* and *Remote Control API*) ship with the engine, and the
config in this folder enables them and opens the bridge.

## Use it

1. Copy this folder wherever you keep your projects (rename it if you like —
   rename the `.uproject` file to match).
2. Open `StarterProject.uproject`. With an empty `EngineAssociation` Windows
   asks which engine to use: any **UE 5.0+** works. (Or right-click the file →
   *Switch Unreal Engine version*.)
3. That's it. On every editor launch `Content/Python/init_unreal.py` starts the
   Remote Control web server on `127.0.0.1:30010`, which is where the MCP
   server connects.

Check: open <http://127.0.0.1:30010/remote/info> in a browser — JSON back
means the bridge is up.

## What's inside

| File | Purpose |
|---|---|
| `StarterProject.uproject` | Enables the two engine plugins |
| `Config/DefaultRemoteControl.ini` | Web server auto-start + the two security gates the bridge needs |
| `Config/DefaultEngine.ini` | Default map and basics |
| `Content/Python/init_unreal.py` | Starts the web server at every editor launch |

## Alternative

You don't need this folder at all if the MCP server is already connected to
your client: ask the agent to run `ue_project_create` and it generates the same
setup (plus optional C++ module, chosen template, default map and game mode).
This folder exists for the opposite path — starting from the project before
wiring up the agent.

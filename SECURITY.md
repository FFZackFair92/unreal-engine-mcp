# Security

## What this server can do

Be clear-eyed about what you are running: this server **executes arbitrary
Python inside your Unreal editor**, and that is not a side effect — it is the
mechanism. Every tool works by sending a snippet to the editor's Python
interpreter over the Remote Control API.

Inside the editor, that code can read and write any file the editor can,
create and delete assets, and run console commands. The local layer also
starts processes on your machine (the editor itself, `UnrealBuildTool`,
`RunUAT`) and downloads files from the network.

If you would not let the model at the other end of the MCP connection run a
Python script on your machine, do not connect it to this server.

## The threat model

The bridge talks to `127.0.0.1:30010`. Unreal's Remote Control web server binds
to localhost, so the endpoint is not reachable from another machine by default.
The two settings that unlock it are deliberately narrow:

- `bEnableRemotePythonExecution=True` — required; this is what lets the bridge
  call `PythonScriptLibrary` at all.
- `+CustomAllowedRemoteFunctionCalls=(ClassPath=".../PythonScriptLibrary")` —
  an allowlist of one class, rather than `bAllowAnyRemoteFunctionCall=True`,
  which would expose every `UFUNCTION` in your project to HTTP.
- `bAllowConsoleCommandRemoteExecution=False` — left off on purpose. It gates
  `ExecuteConsoleCommand` through the web API, which this server never uses.

**Do not widen these on a machine that exposes port 30010 to a network.** If
you must reach the editor from elsewhere, Unreal offers `bRestrictServerAccess`
with an origin allowlist and `bEnforcePassphraseForRemoteClients`; configure
those before opening anything up.

## Downloads

`preset_download_url` fetches any URL you point it at, and archives are
extracted with path traversal filtered out. There is a size cap
(`UE_MCP_MAX_DOWNLOAD`, 4 GiB by default). None of this makes an untrusted
archive safe to import — treat downloaded assets with the same suspicion you
would apply to anything else off the internet.

## Reporting a vulnerability

Open a [GitHub issue](https://github.com/FFZackFair92/unreal-engine-mcp/issues)
for anything that is not itself exploitable from the report. For something
sensitive, use GitHub's **Report a vulnerability** button on the Security tab
so the discussion stays private until there is a fix.

Please include the engine version, the MCP client, and the smallest sequence of
tool calls that reproduces it.

## Supported versions

Fixes land on `main` and go out in the next release. There is no long-term
support branch.

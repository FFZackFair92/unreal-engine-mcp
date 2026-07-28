# Contributing

Thanks for considering a contribution. This is a small, focused project — a
bridge between MCP clients and the Unreal editor — so the bar is mostly about
keeping it honest: every claim in the docs was verified against a running
engine, and it should stay that way.

## Getting set up

```bash
git clone https://github.com/FFZackFair92/unreal-engine-mcp.git
cd unreal-engine-mcp
pip install -e ".[dev]"
pytest -q
ruff check .
```

**You do not need Unreal installed to run the tests.** `tests/fake_unreal.py`
stands in for the `unreal` module and `tests/fake_server.py` emulates the
Remote Control API while *actually executing* the generated snippets, so the
whole chain — tool → snippet → harness → result — is covered. That is why CI
can run on Linux.

You *do* need Unreal to verify anything about real engine behaviour. If a
change depends on how the editor actually responds, say so in the PR and
mention which engine version you tested against.

## Adding a tool

Three pieces, in this order:

1. A reusable helper in `src/unreal_mcp/ue_side.py` (runs inside the editor) or
   in `src/unreal_mcp/local.py` (runs on your machine).
2. An `@mcp.tool()` function in `src/unreal_mcp/server.py` that translates
   arguments into a call to that helper.
3. A test in `tests/`.

`ue_side.py` is hashed and reinstalled in the editor whenever it changes, so
editor-side edits take effect without restarting the MCP server. Changes to
`server.py` or `local.py` need a client restart.

### Things that are easy to get wrong

- **Return keys are English.** They are read by a language model, so a
  consistent vocabulary matters more than it would for a human-facing API.
- **Wrap edits in `mcp_transaction(...)`.** Anything that modifies the level or
  an asset should be undoable with Ctrl+Z. Someone is watching that editor.
- **Only JSON crosses the bridge.** Vectors arrive as `{"x":…}` and assets as
  path strings; use `mcp_coerce_value` rather than assuming a type.
- **Fail with an explanation.** When a feature is missing from the running
  engine, say which version introduced it and what to do instead — see
  `mcp_capabilities()` and how `ue_add_variable` reports it. A cryptic
  `AttributeError` from inside the editor is a bad experience for an agent and
  a worse one for the person reading its output.
- **Prefer runtime detection over version comparison**, so custom engine builds
  keep working.

## Documentation

If you add a tool, update `docs/TOOLS.md`. If you discover something about
Unreal's API that cost you time, add it to `docs/UNREAL-NOTES.md` — that file
exists precisely so the next person does not rediscover it. Italian
translations (`README.it.md`, `docs/TOOLS.it.md`) are welcome but not required;
English is the source of truth.

## Style

`ruff check .` must pass. Line length is 100. Where a lint rule is suppressed,
the `# noqa` carries the reason — please keep that habit rather than widening
the ignore list.

Comments explain *why*, not *what*. Much of the existing code documents traps
found the hard way; that context is the most valuable thing in the file.

## Reporting a bug

Include the engine version, the MCP client, and the full error. If the failure
is inside the editor, the traceback the bridge returns is far more useful than
the summary line.

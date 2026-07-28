"""Auto-start of the Remote Control web server (unreal-mcp).

Unreal runs any script named `init_unreal.py` under Content/Python at editor
startup, so the MCP bridge always finds port 30010 open.
"""

import unreal

try:
    unreal.SystemLibrary.execute_console_command(None, "WebControl.StartServer")
    unreal.log("[unreal-mcp] Remote Control web server started on port 30010.")
except Exception as exc:  # noqa: BLE001
    unreal.log_error("[unreal-mcp] Web server start failed: %s" % exc)

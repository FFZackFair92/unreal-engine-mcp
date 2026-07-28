"""Avvio automatico del web server Remote Control.

Copiare questo file in <ProgettoUnreal>/Content/Python/init_unreal.py:
Unreal esegue automaticamente gli script chiamati `init_unreal.py` all'avvio
dell'editor, così il bridge MCP trova sempre la porta 30010 aperta.
"""

import unreal

try:
    unreal.SystemLibrary.execute_console_command(None, "WebControl.StartServer")
    unreal.log("[unreal-mcp] Remote Control web server avviato sulla porta 30010.")
except Exception as exc:  # noqa: BLE001
    unreal.log_error("[unreal-mcp] Avvio web server fallito: %s" % exc)

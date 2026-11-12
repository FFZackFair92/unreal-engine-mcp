"""MCP server per Unreal Engine 5.

Bridge: Unreal Remote Control API (HTTP :30010) -> PythonScriptLibrary.ExecutePythonCommandEx.
Nessun plugin C++ da compilare: bastano i due plugin inclusi nel motore
("Python Editor Script Plugin" e "Remote Control API").
"""

from importlib.metadata import PackageNotFoundError, version

try:
    # Fonte unica: la versione dichiarata in pyproject.toml.
    __version__ = version("unreal-engine-mcp")
except PackageNotFoundError:  # sorgente non installato (es. eseguito da un checkout)
    __version__ = "0.0.0+sconosciuta"

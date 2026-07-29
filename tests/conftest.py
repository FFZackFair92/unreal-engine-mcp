import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_server import FakeUnrealServer  # noqa: E402

from unreal_mcp import local  # noqa: E402
from unreal_mcp import server as mcp_server  # noqa: E402
from unreal_mcp.bridge import BridgeConfig, UnrealBridge  # noqa: E402


@pytest.fixture(autouse=True)
def isola_dalla_macchina(monkeypatch, tmp_path):
    """Nasconde ai test l'Unreal realmente installato su questa macchina.

    `find_engines()` interroga il registro di Windows e il manifest dell'Epic
    Launcher. Senza questo isolamento la suite passa su un runner pulito e
    fallisce sulla macchina di chi sviluppa il progetto — che è esattamente il
    posto in cui deve funzionare. I test costruiscono i loro motori finti e li
    dichiarano via UE_MCP_ENGINE_DIRS.
    """
    monkeypatch.setattr(local, "_engines_from_registry", list)
    monkeypatch.setattr(local, "WINDOWS_LAUNCHER_DAT", tmp_path / "launcher-assente.dat")
    for variabile in ("UE_MCP_ENGINE_DIRS", "UE_ROOT"):
        monkeypatch.delenv(variabile, raising=False)


@pytest.fixture
def unreal(tmp_path):
    fake = FakeUnrealServer(tmp_path)
    yield fake
    fake.stop()


@pytest.fixture
def bridge(unreal):
    """Bridge puntato all'Unreal finto, sul trasporto HTTP.

    `transport` è esplicito e non "auto": con auto ogni chiamata comincerebbe
    da una scoperta multicast di due secondi verso un editor che qui non
    esiste, e la suite ci passerebbe la maggior parte del tempo.
    """
    return UnrealBridge(
        BridgeConfig(
            host="127.0.0.1", port=unreal.port, timeout=30, transport="remotecontrol"
        )
    )


@pytest.fixture
def tools(bridge, monkeypatch):
    """Il modulo server con il bridge puntato all'Unreal finto."""
    monkeypatch.setattr(mcp_server, "_bridge", bridge)
    return mcp_server

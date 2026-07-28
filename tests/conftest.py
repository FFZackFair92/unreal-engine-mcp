import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_server import FakeUnrealServer  # noqa: E402

from unreal_mcp import server as mcp_server  # noqa: E402
from unreal_mcp.bridge import BridgeConfig, UnrealBridge  # noqa: E402


@pytest.fixture
def unreal(tmp_path):
    fake = FakeUnrealServer(tmp_path)
    yield fake
    fake.stop()


@pytest.fixture
def bridge(unreal):
    return UnrealBridge(BridgeConfig(host="127.0.0.1", port=unreal.port, timeout=30))


@pytest.fixture
def tools(bridge, monkeypatch):
    """Il modulo server con il bridge puntato all'Unreal finto."""
    monkeypatch.setattr(mcp_server, "_bridge", bridge)
    return mcp_server

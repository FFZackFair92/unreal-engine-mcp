"""Test del packaging: avvio di RunUAT in background e lettura dello stato."""

import json
from pathlib import Path

import pytest
from test_local import _make_batch_files, _make_engine

from unreal_mcp import local


@pytest.fixture
def progetto(tmp_path, monkeypatch):
    root = tmp_path / "engines"
    root.mkdir()
    _make_batch_files(_make_engine(root, "5.8"))

    monkeypatch.setenv("UE_MCP_ENGINE_DIRS", str(root))
    monkeypatch.setattr(local, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(local, "STATE_FILE", tmp_path / "state/state.json")
    monkeypatch.setattr(local, "PACKAGE_STATE_FILE", tmp_path / "state/package.json")
    monkeypatch.setattr(local, "WINDOWS_LAUNCHER_DAT", tmp_path / "assente.dat")
    monkeypatch.setattr(local, "editor_status", lambda: {"running": False})
    monkeypatch.setattr(local, "terminate_process_by_name", lambda nome: False)

    return local.create_project("MyGame", str(tmp_path / "Projects"))


class _FakePopen:
    def __init__(self, args, **kwargs):
        self.args = args
        self.pid = 999


def test_package_configurazione_invalida(progetto):
    with pytest.raises(local.LocalError) as excinfo:
        local.start_package(progetto["uproject"], configuration="Turbo")
    assert "Development" in str(excinfo.value)


def test_package_rifiuta_editor_aperto(progetto, monkeypatch):
    monkeypatch.setattr(local, "editor_status", lambda: {"editor_process_detected": True})
    with pytest.raises(local.LocalError) as excinfo:
        local.start_package(progetto["uproject"])
    assert "editor" in str(excinfo.value).lower()


def test_package_comando_generato(progetto, monkeypatch):
    monkeypatch.setattr(local.subprocess, "Popen", _FakePopen)

    avvio = local.start_package(
        progetto["uproject"],
        configuration="Development",
        maps=["/Game/MyGame/Levels/L_Main"],
    )
    assert avvio["pid"] == 999
    assert avvio["configuration"] == "Development"
    salvato = json.loads(local.PACKAGE_STATE_FILE.read_text())
    assert salvato["jobs"][progetto["uproject"]]["pid"] == 999

    script = next((Path(progetto["root"]) / "Saved").glob("mcp_package_run.*"))
    comando = script.read_text(encoding="utf-8")
    assert "BuildCookRun" in comando
    assert "-clientconfig=Development" in comando
    assert "-map=/Game/MyGame/Levels/L_Main" in comando
    assert "-archivedirectory=" in comando
    assert "-server" not in comando


def test_package_server_dedicato(progetto, monkeypatch):
    monkeypatch.setattr(local.subprocess, "Popen", _FakePopen)
    local.start_package(progetto["uproject"], dedicated_server=True)

    script = next((Path(progetto["root"]) / "Saved").glob("mcp_package_run.*"))
    comando = script.read_text(encoding="utf-8")
    assert "-server" in comando
    assert "-serverconfig=Development" in comando


def test_package_stato_successo(progetto, monkeypatch):
    monkeypatch.setattr(local.subprocess, "Popen", _FakePopen)
    avvio = local.start_package(progetto["uproject"])

    archivio = Path(avvio["archive"]) / "Windows"
    archivio.mkdir(parents=True, exist_ok=True)
    (archivio / "MyGame.exe").write_text("finto eseguibile", encoding="utf-8")

    Path(avvio["log"]).write_text(
        "********** COOK COMMAND STARTED **********\n"
        "********** ARCHIVE COMMAND STARTED **********\n"
        "BUILD SUCCESSFUL\n"
        "EXITCODE=0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(local, "_process_alive", lambda pid: False)

    stato = local.package_status()
    assert stato["succeeded"] is True
    assert stato["phase"] == "Archive"
    assert any("MyGame.exe" in e for e in stato["executables"])


def test_package_stato_fallimento(progetto, monkeypatch):
    monkeypatch.setattr(local.subprocess, "Popen", _FakePopen)
    avvio = local.start_package(progetto["uproject"])

    Path(avvio["log"]).write_text(
        "********** COOK COMMAND STARTED **********\n"
        "ERROR: Cook failed. Deployment aborted.\n"
        "EXITCODE=1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(local, "_process_alive", lambda pid: False)

    stato = local.package_status()
    assert stato["succeeded"] is False
    assert stato["exit_code"] == "1"
    assert any("Cook failed" in e for e in stato["errors"])
    assert stato["phase"] == "Cook"


def test_package_status_senza_avvii(tmp_path, monkeypatch):
    monkeypatch.setattr(local, "PACKAGE_STATE_FILE", tmp_path / "mai.json")
    assert local.package_status()["running"] is False


@pytest.mark.parametrize(
    "kwargs",
    [
        {"configuration": "Shipping && whoami"},
        {"platform_name": "Win64; id"},
        {"maps": ["/Game/L_Main -execcmds=quit"]},
        {"maps": ["/Game/L_Main && calc.exe"]},
        {"output_dir": '/tmp/out" & whoami & "'},
    ],
)
def test_package_rifiuta_argomenti_iniettabili(progetto, monkeypatch, kwargs):
    monkeypatch.setattr(local.subprocess, "Popen", _FakePopen)
    with pytest.raises(local.LocalError):
        local.start_package(progetto["uproject"], **kwargs)


def _pyproject() -> dict:
    """pyproject.toml letto come dati. `tomllib` è stdlib solo da 3.11."""
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - solo su 3.10
        import tomli as tomllib

    radice = Path(__file__).resolve().parents[1]
    return tomllib.loads((radice / "pyproject.toml").read_text(encoding="utf-8"))


def test_i_comandi_installati_coprono_il_nome_della_distribuzione():
    """`uvx <pacchetto>` esegue un eseguibile che si chiama come il pacchetto.

    La distribuzione è `unreal-engine-mcp` ma lo script storico è `unreal-mcp`:
    senza un alias, `uvx unreal-engine-mcp` — il comando che il README consiglia
    — non trova niente ed esce, e il client MCP lo riporta solo come "Server
    disconnected". È già successo una volta.
    """
    dati = _pyproject()
    nome = dati["project"]["name"]
    scripts = dati["project"]["scripts"]

    assert nome in scripts, (
        "manca un console script chiamato %r: `uvx %s` fallirebbe" % (nome, nome)
    )
    # Tutti gli alias devono puntare allo stesso entry point.
    assert len(set(scripts.values())) == 1


def test_il_readme_consiglia_un_comando_che_esiste():
    """Il comando pubblicizzato nel README dev'essere davvero installato."""
    import re

    radice = Path(__file__).resolve().parents[1]
    scripts = set(_pyproject()["project"]["scripts"])

    for readme in ("README.md", "README.it.md"):
        testo = (radice / readme).read_text(encoding="utf-8")
        for comando in re.findall(r"^uvx (\S+)$", testo, re.MULTILINE):
            assert comando in scripts, (
                "%s consiglia `uvx %s`, ma i comandi installati sono %s"
                % (readme, comando, sorted(scripts))
            )


def test_anche_packaging_e_render_ricevono_lambiente(progetto, monkeypatch, tmp_path):
    """La stessa variabile mancante rompeva RunUAT e il render, non solo Build.bat."""
    monkeypatch.delenv("TMP", raising=False)
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setattr(local, "RENDER_STATE_FILE", tmp_path / "state/render.json")
    monkeypatch.setattr(local, "STATE_FILE", tmp_path / "state/state.json")
    catturato = []

    class _Cattura(_FakePopen):
        def __init__(self, args, **kwargs):
            catturato.append(kwargs)
            super().__init__(args, **kwargs)

    monkeypatch.setattr(local.subprocess, "Popen", _Cattura)

    # Il test parla di ambiente, non di render, ma `start_render` si rifiuta di
    # partire senza la Movie Render Queue: il plugin va abilitato per arrivare
    # alla Popen che qui interessa davvero.
    local.set_project_plugins(progetto["uproject"], enable=[local.MRQ_PLUGIN])

    local.start_package(progetto["uproject"])
    local.start_render(progetto["uproject"], "/Game/LS")
    local.launch_editor(progetto["uproject"], skip_module_check=True)

    assert len(catturato) == 3
    for kwargs in catturato:
        assert kwargs["env"]["TMP"] == str(tmp_path)

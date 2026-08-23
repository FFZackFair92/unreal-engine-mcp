"""Test del render Movie Render Queue.

Attenzione al limite di questi test, che è lo stesso di tutta la suite ma qui
pesa di più: verificano le funzioni pure — mappatura dei formati, costruzione
della riga di comando, validazione dei path, raccolta dei file, stato — con un
`subprocess.Popen` finto. **Nessuno di essi ha mai renderizzato un fotogramma
con Unreal vero.** La parte che resta non verificata è se questa riga di comando
produce davvero output su un motore reale.
"""

import json
from pathlib import Path

import pytest
from test_local import _make_batch_files, _make_engine

from unreal_mcp import local


class _FakePopen:
    def __init__(self, args, **kwargs):
        self.args = args
        self.pid = 7777


@pytest.fixture
def progetto(tmp_path, monkeypatch):
    root = tmp_path / "engines"
    root.mkdir()
    engine_root = _make_engine(root, "5.8")
    _make_batch_files(engine_root)

    monkeypatch.setenv("UE_MCP_ENGINE_DIRS", str(root))
    monkeypatch.setattr(local, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(local, "RENDER_STATE_FILE", tmp_path / "state/render.json")
    monkeypatch.setattr(local, "WINDOWS_LAUNCHER_DAT", tmp_path / "assente.dat")
    creato = local.create_project("MyGame", str(tmp_path / "Projects"))
    return creato


@pytest.fixture
def progetto_con_mrq(progetto):
    """Progetto con la Movie Render Queue abilitata.

    I test del render vero e proprio partono da qui: il caso senza plugin ha i
    suoi test dedicati e non deve far fallire tutti gli altri.
    """
    local.set_project_plugins(progetto["uproject"], enable=[local.MRQ_PLUGIN])
    return progetto


# ------------------------------------------------------------ funzioni pure


@pytest.mark.parametrize(
    "formato,attese",
    [
        ("png", ["MoviePipelineImageSequenceOutput_PNG"]),
        ("EXR", ["MoviePipelineImageSequenceOutput_EXR"]),
        (".jpg", ["MoviePipelineImageSequenceOutput_JPG"]),
        ("prores", ["MoviePipelineAppleProResOutput"]),
    ],
)
def test_mappatura_formati(formato, attese):
    assert local.render_output_classes(formato) == attese


def test_mp4_passa_dall_encoder_da_riga_di_comando():
    """mp4 non ha un nodo di output nativo: PNG più encoder configurato nel progetto."""
    classi = local.render_output_classes("mp4")
    assert "MoviePipelineImageSequenceOutput_PNG" in classi
    assert "MoviePipelineCommandLineEncoder" in classi


def test_formato_sconosciuto_elenca_quelli_validi():
    with pytest.raises(local.LocalError, match="png"):
        local.render_output_classes("avi")


@pytest.mark.parametrize(
    "dentro,fuori",
    [
        ("/Game/Cine/LS_Intro", "/Game/Cine/LS_Intro.LS_Intro"),
        ("/Game/A/B/Cfg", "/Game/A/B/Cfg.Cfg"),
        ("/Game/X.X", "/Game/X.X"),
    ],
)
def test_object_path(dentro, fuori):
    assert local.to_object_path(dentro) == fuori


def test_riga_di_comando_headless():
    args = local.build_render_command(
        "C:/UE/UnrealEditor-Cmd.exe", "C:/P/G.uproject", "/Game/Maps/M.M",
        "/Game/Cine/LS.LS", "/Game/Cine/Cfg.Cfg", [1280, 720],
    )
    assert args[0].endswith("UnrealEditor-Cmd.exe")
    assert "-RenderOffscreen" in args and "-Unattended" in args
    assert "-resx=1280" in args and "-resy=720" in args
    assert "-LevelSequence=/Game/Cine/LS.LS" in args
    assert "-MoviePipelineConfig=/Game/Cine/Cfg.Cfg" in args


def test_senza_config_non_si_passa_lo_switch():
    args = local.build_render_command("cmd", "p", "m", "s", None, None)
    assert not [a for a in args if a.startswith("-MoviePipelineConfig")]
    assert "-resx=1920" in args  # default


def test_raccolta_dei_file_prodotti(tmp_path):
    out = tmp_path / "renders"
    (out / "seq").mkdir(parents=True)
    vecchio = out / "seq" / "0001.png"
    vecchio.write_text("x")
    (out / "seq" / "0002.png").write_text("x")

    assert len(local.collect_render_output(str(out))) == 2
    # Escludendo quelli già presenti resta solo il nuovo, senza guardare l'ora.
    assert local.collect_render_output(str(out), [str(vecchio)]) == [
        str(out / "seq" / "0002.png")
    ]
    assert local.collect_render_output(str(tmp_path / "vuoto")) == []
    assert local.collect_render_output(None) == []


def test_i_file_gia_presenti_non_contano_come_prodotti(progetto_con_mrq, monkeypatch):
    """Un render che non scrive niente non deve sembrare riuscito solo perché
    nella cartella c'era già roba di un render precedente."""
    monkeypatch.setattr(local.subprocess, "Popen", _FakePopen)
    destinazione = Path(progetto_con_mrq["uproject"]).parent / "Saved/MovieRenders"
    destinazione.mkdir(parents=True, exist_ok=True)
    (destinazione / "vecchio.png").write_text("x")

    esito = local.start_render(progetto_con_mrq["uproject"], "/Game/LS")
    monkeypatch.setattr(local, "_process_alive", lambda pid: False)
    Path(esito["log"]).write_text("EXITCODE=0\n", encoding="utf-8")

    stato = local.render_status()
    assert stato["frames_written"] == 0
    assert stato["succeeded"] is False


# ------------------------------------------------------------- avvio e stato


@pytest.mark.parametrize(
    "kwargs",
    [
        {"sequence": "/Game/LS -Unattended -ExecCmds=quit"},
        {"sequence": "/Game/LS", "config": "/Game/Cfg -resx=1"},
        {"sequence": "/Game/LS", "map_path": "../fuori"},
    ],
)
def test_path_iniettabili_rifiutati(progetto_con_mrq, monkeypatch, kwargs):
    """I path finiscono sulla riga di comando dell'editor, che la ri-tokenizza."""
    monkeypatch.setattr(local.subprocess, "Popen", _FakePopen)
    with pytest.raises(local.LocalError):
        local.start_render(progetto_con_mrq["uproject"], **kwargs)


def test_avvio_registra_lo_stato(progetto_con_mrq, monkeypatch):
    catturato = {}

    class _Cattura(_FakePopen):
        def __init__(self, args, **kwargs):
            catturato["args"] = args
            super().__init__(args, **kwargs)

    monkeypatch.setattr(local.subprocess, "Popen", _Cattura)
    esito = local.start_render(
        progetto_con_mrq["uproject"], "/Game/Cine/LS_Intro", config="/Game/Cine/Cfg"
    )
    assert esito["pid"] == 7777
    assert esito["sequence"] == "/Game/Cine/LS_Intro"
    salvato = json.loads(local.RENDER_STATE_FILE.read_text())
    assert salvato["jobs"][progetto_con_mrq["uproject"]]["pid"] == 7777


def test_secondo_render_rifiutato(progetto_con_mrq, monkeypatch):
    monkeypatch.setattr(local.subprocess, "Popen", _FakePopen)
    local.start_render(progetto_con_mrq["uproject"], "/Game/LS")
    monkeypatch.setattr(local, "_process_alive", lambda pid: True)
    with pytest.raises(local.LocalError, match="già un render"):
        local.start_render(progetto_con_mrq["uproject"], "/Game/LS")
    local.start_render(progetto_con_mrq["uproject"], "/Game/LS", force=True)


def test_successo_si_misura_sui_file_non_sul_codice_di_uscita(progetto_con_mrq, monkeypatch):
    """MRQ headless può uscire con 0 senza scrivere niente, se la config non
    aveva nodi di output: il codice di uscita da solo mentirebbe."""
    monkeypatch.setattr(local.subprocess, "Popen", _FakePopen)
    esito = local.start_render(progetto_con_mrq["uproject"], "/Game/LS")
    monkeypatch.setattr(local, "_process_alive", lambda pid: False)
    Path(esito["log"]).write_text("EXITCODE=0\n", encoding="utf-8")

    stato = local.render_status()
    assert stato["exit_code"] == "0"
    assert stato["succeeded"] is False
    assert stato["frames_written"] == 0

    prodotto = Path(esito["output_dir"]) / "0001.png"
    prodotto.write_text("x")
    stato = local.render_status()
    assert stato["succeeded"] is True
    assert stato["frames_written"] == 1


def test_stato_senza_render_avviato(progetto):
    assert local.render_status()["running"] is False


# ------------------------------------- il plugin deve esserci, prima di tutto


def test_render_rifiutato_senza_il_plugin(progetto, monkeypatch):
    """Un render headless senza Movie Render Queue parte e non scrive niente.

    È il peggior modo di fallire: nessun errore, nessun fotogramma, e chi
    guarda pensa di aver sbagliato la scena o il preset.
    """
    monkeypatch.setattr(local.subprocess, "Popen", _FakePopen)
    with pytest.raises(local.LocalError) as errore:
        local.start_render(progetto["uproject"], "/Game/Cine/LS")
    testo = str(errore.value)
    assert "MovieRenderPipeline" in testo
    assert "ue_project_set_plugins" in testo


def test_render_procede_con_il_plugin(progetto, monkeypatch):
    monkeypatch.setattr(local.subprocess, "Popen", _FakePopen)
    local.set_project_plugins(progetto["uproject"], enable=[local.MRQ_PLUGIN])
    esito = local.start_render(progetto["uproject"], "/Game/Cine/LS")
    assert esito["pid"] == 7777


def test_stato_del_plugin_leggibile(progetto):
    stato = local.mrq_available(progetto["uproject"])
    assert stato["enabled"] is False
    assert stato["plugin"] == "MovieRenderPipeline"

    local.set_project_plugins(progetto["uproject"], enable=["MovieRenderPipeline"])
    assert local.mrq_available(progetto["uproject"])["enabled"] is True

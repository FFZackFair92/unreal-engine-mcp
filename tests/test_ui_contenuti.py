"""Il pannello contenuti ha le stesse trappole silenziose di quello viewport.

Un elenco di asset che finisce nel risultato del tool costa token a ogni
apertura; una categoria che esiste nella vista ma non nel server mostra una
colonna sempre vuota. Sono rotture che non sollevano niente.
"""

from __future__ import annotations

import re

import pytest

from unreal_mcp import server, ui
from unreal_mcp.server import mcp


@pytest.fixture
async def tools():
    return {t.name: t for t in await mcp.list_tools()}


async def test_il_pannello_punta_alla_sua_risorsa(tools):
    assert tools["ue_content_panel"].meta == {"ui": {"resourceUri": ui.CONTENUTI_URI}}


async def test_il_pannello_non_restituisce_lelenco(tools):
    """Un progetto vero ha migliaia di asset e questo risultato lo legge il modello.

    Il pannello manda solo i conteggi; l'elenco lo chiede la vista con
    ue_content_list, che è una chiamata che nel contesto non entra.
    """
    campi = (tools["ue_content_panel"].outputSchema or {}).get("properties", {})
    assert "assets" not in campi
    assert {"conteggi", "totale", "levels"} <= set(campi)


async def test_lelenco_e_un_tool_a_parte_e_non_una_app(tools):
    elenco = tools["ue_content_list"]
    campi = (elenco.outputSchema or {}).get("properties", {})
    assert {"assets", "troncato"} <= set(campi)
    # Se dichiarasse una UI l'host proverebbe a renderizzarlo come pannello.
    assert elenco.meta is None


async def test_lo_stato_lavori_dichiara_uno_schema(tools):
    # Senza outputSchema niente structuredContent: la barra resterebbe muta.
    campi = (tools["ue_jobs_status"].outputSchema or {}).get("properties", {})
    assert {"build", "package", "render"} <= set(campi)


async def test_le_categorie_della_vista_esistono_nel_server():
    """ORDINE nella pagina e CATEGORIE_ASSET nel server devono coincidere.

    Sono due elenchi in due linguaggi: una categoria presente solo nella vista
    è una voce che non comparirà mai, una presente solo nel server è un gruppo
    di asset che l'utente non può raggiungere.
    """
    corpo = next(iter(await mcp.read_resource(ui.CONTENUTI_URI))).content
    blocco = re.search(r"const ORDINE = \[(.*?)\];", corpo, re.S)
    assert blocco, "ORDINE non trovato nella pagina"
    vista = set(re.findall(r'"(\w+)"', blocco.group(1)))

    lato_server = {nome for nome, _ in server.CATEGORIE_ASSET} | {"altro"}
    assert vista == lato_server


@pytest.mark.parametrize(
    "classe,attesa",
    [
        ("World", "livelli"),
        ("SoundWave", "audio"),
        ("SoundCue", "audio"),
        ("MetaSoundSource", "audio"),
        ("Blueprint", "blueprint"),
        ("WidgetBlueprint", "blueprint"),
        ("StaticMesh", "mesh"),
        ("SkeletalMesh", "mesh"),
        ("Texture2D", "texture"),
        ("AnimSequence", "animazioni"),
        ("NiagaraSystem", "effetti"),
        ("QualcosaDiIgnoto", "altro"),
    ],
)
def test_classificazione_degli_asset(classe, attesa):
    assert server._categoria(classe) == attesa


@pytest.mark.parametrize("classe", ["WorldDataLayers", "WorldPartitionMiniMap"])
def test_gli_oggetti_interni_di_un_livello_non_sono_livelli(classe):
    """Trovato su supertekken: 9 livelli mostrati, 7 reali.

    `World` come sottostringa cattura anche gli oggetti che un livello si
    porta dentro. Finivano nell'elenco con il bottone "Apri", che su di loro
    non può funzionare. Per questo il pattern dei livelli è esatto.
    """
    assert server._categoria(classe) == "altro"


def test_il_marcatore_di_esattezza_funziona():
    assert server._combacia("World", "=World")
    assert not server._combacia("WorldDataLayers", "=World")
    # Senza "=" resta una sottostringa, che per le famiglie serve.
    assert server._combacia("MaterialInstanceConstant", "Material")


def test_i_materiali_non_rubano_le_texture():
    """L'ordine di CATEGORIE_ASSET è significativo, non decorativo.

    "MaterialInstanceConstant" contiene "Material": se le texture stessero
    prima dei materiali, o i prefissi fossero in ordine diverso, gli asset
    finirebbero nella categoria sbagliata senza che niente lo segnali.
    """
    assert server._categoria("MaterialInstanceConstant") == "materiali"
    assert server._categoria("MaterialFunction") == "materiali"
    assert server._categoria("TextureRenderTarget2D") == "texture"


async def test_laggancio_onora_la_risposta_dellhost():
    """`requestDisplayMode` risponde con la modalità *davvero* applicata.

    Fidarsi della richiesta invece che della risposta accende un bottone che
    non ha cambiato niente: il pannello resta dov'è e sembra un bug nostro.
    Era esattamente il difetto della prima versione.
    """
    corpo = next(iter(await mcp.read_resource(ui.CONTENUTI_URI))).content
    assert "availableDisplayModes" in corpo, "non controlla se pip è disponibile"
    assert 'esito?.mode' in corpo, "ignora la modalità restituita dall'host"
    assert 'ottenuta === "pip"' in corpo


async def test_la_pagina_e_autoconsistente():
    corpo = next(iter(await mcp.read_resource(ui.CONTENUTI_URI))).content
    assert "__APP_BUNDLE__" not in corpo
    for cdn in ("esm.sh", "unpkg.com", "jsdelivr", "cdnjs"):
        assert cdn not in corpo
    # I nomi dei tool chiamati dalla vista devono esistere davvero.
    nomi = {t.name for t in await mcp.list_tools()}
    for chiamato in re.findall(r'name: "(\w+)"', corpo):
        assert chiamato in nomi, "la vista chiama un tool inesistente: %s" % chiamato

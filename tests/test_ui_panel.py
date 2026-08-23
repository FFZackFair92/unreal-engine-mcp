"""Il pannello viewport è un contratto con l'host, non solo con il modello.

Se `_meta.ui.resourceUri` sparisce, o il mime della risorsa cambia, o il tool
smette di dichiarare un outputSchema, il pannello continua a "funzionare": si
apre e resta vuoto. Sono rotture silenziose, ed è il motivo per cui vale la
pena fissarle qui.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from unreal_mcp import ui
from unreal_mcp.server import mcp


@pytest.fixture
async def tool_pannello():
    for tool in await mcp.list_tools():
        if tool.name == "ue_viewport_panel":
            return tool
    pytest.fail("ue_viewport_panel non è fra i tool esposti")


@pytest.fixture
async def risorsa_ui():
    for risorsa in await mcp.list_resources():
        if str(risorsa.uri) == ui.VIEWPORT_URI:
            return risorsa
    pytest.fail("la risorsa %s non è esposta" % ui.VIEWPORT_URI)


async def test_il_tool_punta_alla_risorsa_ui(tool_pannello):
    assert tool_pannello.meta == {"ui": {"resourceUri": ui.VIEWPORT_URI}}


async def test_meta_viaggia_come_underscore_meta(tool_pannello):
    # Il campo si chiama `meta` in Python ma `_meta` sul filo: senza l'alias
    # l'host non riconosce il tool come app e lo tratta come uno qualsiasi.
    serializzato = json.loads(tool_pannello.model_dump_json(by_alias=True, exclude_none=True))
    assert serializzato["_meta"]["ui"]["resourceUri"] == ui.VIEWPORT_URI


async def test_il_tool_dichiara_un_output_schema(tool_pannello):
    # Senza outputSchema FastMCP non popola structuredContent, e il pannello
    # non ha da nessuna parte da cui leggere i dati.
    campi = (tool_pannello.outputSchema or {}).get("properties", {})
    assert {"status", "actors", "error"} <= set(campi)


async def test_il_pannello_non_restituisce_limmagine(tool_pannello):
    """Il costo di sbagliarsi qui è ~200.000 token per apertura.

    Un PNG della viewport pesa mezzo megabyte; in base64 dentro il risultato
    del tool finisce tutto nel contesto del modello. La cattura la chiede la
    vista con una sua chiamata a ue_viewport_frame, che quel giro non lo fa.
    """
    campi = (tool_pannello.outputSchema or {}).get("properties", {})
    assert "screenshot" not in campi


async def test_esiste_il_tool_della_cattura():
    tools = {t.name: t for t in await mcp.list_tools()}
    assert "ue_viewport_frame" in tools
    campi = (tools["ue_viewport_frame"].outputSchema or {}).get("properties", {})
    assert "screenshot" in campi
    # Non è un'app: se dichiarasse una UI l'host proverebbe a renderizzarlo.
    assert tools["ue_viewport_frame"].meta is None


async def test_la_risorsa_usa_il_mime_delle_app(risorsa_ui):
    # "text/html+skybridge" è il mime della vecchia mcp-ui: un host conforme
    # all'estensione apps non lo riconosce.
    assert risorsa_ui.mimeType == "text/html;profile=mcp-app"


async def test_la_csp_non_apre_origini_esterne(risorsa_ui):
    # L'helper è vendorizzato apposta per non dover autorizzare nessun CDN:
    # se qui ricompare un'origine, il pannello ha ripreso a dipendere dalla
    # rete e smette di funzionare offline.
    csp = (risorsa_ui.meta or {}).get("ui", {}).get("csp", {})
    assert csp.get("script-src") == []
    assert csp.get("connect-src") == []
    # Lo screenshot arriva come data: URI dentro il risultato del tool, e
    # `data:` non è implicito in img-src.
    assert "data:" in csp.get("img-src", [])


async def test_html_autoconsistente():
    corpo = next(iter(await mcp.read_resource(ui.VIEWPORT_URI))).content
    assert "__APP_BUNDLE__" not in corpo, "placeholder del bundle non sostituito"
    # Il pannello si riaggiorna richiamando i propri tool: se un nome qui
    # dentro e quello del tool divergono, i bottoni smettono di funzionare.
    for tool in ("ue_viewport_panel", "ue_viewport_frame", "ue_viewport_camera"):
        assert tool in corpo


async def test_la_pagina_non_scarica_niente():
    corpo = next(iter(await mcp.read_resource(ui.VIEWPORT_URI))).content
    for cdn in ("esm.sh", "unpkg.com", "jsdelivr", "cdnjs"):
        assert cdn not in corpo, "la pagina è tornata a dipendere da %s" % cdn


async def test_il_bundle_espone_App_su_globalthis():
    # La trasformazione applicata da scripts/vendor_ext_apps.py: il bundle è un
    # modulo ESM, ma incorporato inline i suoi export non li legge nessuno.
    # Se un aggiornamento la salta, `const { App } = ...` esplode a runtime.
    corpo = next(iter(await mcp.read_resource(ui.VIEWPORT_URI))).content
    assert "globalThis.__EXT_APPS__={App:" in corpo
    assert "globalThis.__EXT_APPS__" in ui.VIEWPORT_HTML


def _sorgente_ue_side() -> str:
    """Il sorgente di ue_side.py come testo.

    Importarlo non si può: quel modulo fa `import unreal` e vive dentro
    l'editor. Nella suite completa l'import passa solo perché un altro test ha
    già installato il finto `unreal` in sys.modules — cioè funziona o no a
    seconda dell'ordine. Leggerlo come file non dipende da niente.
    """
    return (Path(ui.__file__).parent / "ue_side.py").read_text(encoding="utf-8")


async def test_le_viste_del_menu_esistono_lato_editor():
    """Le opzioni della tendina e le viste dell'editor devono coincidere.

    Sono due elenchi in due file e due linguaggi diversi: se divergono, la
    tendina offre una vista che il lato editor rifiuta con un ValueError, e
    l'utente vede un errore scegliendo una voce che gli avevamo proposto noi.
    """
    corpo = next(iter(await mcp.read_resource(ui.VIEWPORT_URI))).content
    tendina = set(re.findall(r'<option value="(\w+)">', corpo))
    assert tendina, "nessuna vista trovata nella tendina"

    blocco = re.search(r"MCP_VISTE = \{(.*?)\}", _sorgente_ue_side(), re.S)
    assert blocco, "MCP_VISTE non trovata in ue_side.py"
    lato_editor = set(re.findall(r'"(\w+)":', blocco.group(1)))
    assert tendina <= lato_editor, "la tendina offre viste che l'editor rifiuta"


async def test_la_cattura_del_pannello_riusa_un_solo_file():
    """Un nome col timestamp riempirebbe il disco senza che nessuno lo noti.

    Il pannello ricattura a ogni comando della camera e, in auto-refresh, ogni
    cinque secondi: a mezzo mega per PNG sono ~7 MB al minuto. L'unico file
    che serve è l'ultimo.
    """
    import inspect

    from unreal_mcp import server

    assert server.PANNELLO_SCREENSHOT
    sorgente = inspect.getsource(server._cattura_data_uri)
    assert "PANNELLO_SCREENSHOT" in sorgente
    assert "mcp_screenshot(None" not in sorgente, "torna a generare un nome nuovo"

    # Riusare il nome funziona solo se l'editor cancella prima: altrimenti chi
    # aspetta la cattura trova subito il file del giro precedente.
    assert "os.remove(destinazione)" in _sorgente_ue_side()


async def test_il_bundle_non_chiude_lo_script():
    # È incorporato dentro <script>: una sequenza `</script` nel sorgente
    # chiuderebbe il tag e sputerebbe il resto del bundle nella pagina.
    assert "</script" not in ui._APP_JS.lower()

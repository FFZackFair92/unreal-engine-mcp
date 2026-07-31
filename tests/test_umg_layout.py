"""Test della Fase 12 (layout UMG) della roadmap.

Rettifica parziale della fase 2, che aveva concluso "il WidgetTree non è
popolabile" perché `get_editor_property("WidgetTree")` risponde "protected".
La proprietà è davvero protetta, ma l'oggetto che contiene è un *subobject*
del Widget Blueprint e si prende per nome: `find_object(wbp, "WidgetTree")`.
Da lì il layout si costruisce con l'API pubblica dei widget, mai provata
prima su un template di editor.

Verificato dal vivo su UE 5.8 il 2026-07-31: CanvasPanel → VerticalBox →
TextBlock + Button, con testo, colore, padding e posizione, salvato e
riletto da zero — gerarchia e valori c'erano ancora, e i nomi dei widget
compaiono nel .uasset.

**Il limite che resta**: `RootWidget` non è scrivibile e nessuna UFUNCTION la
imposta (cercata in tutte le classi esposte), quindi il primo widget di un
albero vuoto non è creabile da Python.
"""

import pytest


@pytest.fixture
async def wbp(tools, unreal):
    """Un Widget Blueprint con una radice già dentro.

    Riproduce la situazione reale: la radice arriva dal Widget Designer o da
    un asset duplicato, non da Python.
    """
    await tools.ue_create_widget_blueprint("/Game/UI", "WBP_Menu")
    percorso = "/Game/UI/WBP_Menu"
    albero = unreal.unreal.find_object(unreal.state["assets"][percorso], "WidgetTree")
    radice = unreal.unreal.new_object(unreal.unreal.CanvasPanel, outer=albero, name="Radice")
    assert radice.get_parent() is None
    return percorso


# --------------------------------------------------------------- lettura


async def test_tree_info_legge_la_gerarchia(tools, wbp):
    await tools.ue_umg_add_widget(wbp, "VerticalBox", name="Colonna")
    await tools.ue_umg_add_widget(wbp, "TextBlock", parent="Colonna", name="Titolo")

    info = await tools.ue_umg_tree_info(wbp)

    assert info["root"]["name"] == "Radice"
    colonna = info["root"]["children"][0]
    assert colonna["name"] == "Colonna"
    assert [c["name"] for c in colonna["children"]] == ["Titolo"]
    assert info["widget_count"] == 3


async def test_albero_vuoto_riporta_root_nullo(tools, unreal):
    """Un Widget Blueprint creato da Python nasce senza radice, ed è
    esattamente il caso in cui non si può costruire niente."""
    await tools.ue_create_widget_blueprint("/Game/UI", "WBP_Vuoto")

    info = await tools.ue_umg_tree_info("/Game/UI/WBP_Vuoto")

    assert info["root"] is None
    assert info["widget_count"] == 0


async def test_su_un_albero_vuoto_dice_come_procurarsi_una_radice(tools):
    await tools.ue_create_widget_blueprint("/Game/UI", "WBP_Vuoto")

    with pytest.raises(RuntimeError, match="ue_duplicate_asset"):
        await tools.ue_umg_add_widget("/Game/UI/WBP_Vuoto", "TextBlock")


async def test_su_un_asset_che_non_e_un_widget_blueprint_fallisce(tools):
    await tools.ue_create_blueprint("/Game/UI", "BP_NonUnWidget", "Actor")

    with pytest.raises(RuntimeError, match="non un Widget Blueprint"):
        await tools.ue_umg_tree_info("/Game/UI/BP_NonUnWidget")


# --------------------------------------------------------------- costruzione


async def test_add_widget_appende_alla_radice_di_default(tools, wbp):
    esito = await tools.ue_umg_add_widget(wbp, "TextBlock", name="Titolo")

    assert esito["parent"] == "Radice"
    assert esito["slot_class"] == "CanvasPanelSlot"


async def test_lo_slot_dipende_dal_pannello_che_contiene(tools, wbp):
    """Un widget dentro un VerticalBox ha un VerticalBoxSlot, non un
    CanvasPanelSlot: è il pannello a decidere quali chiavi di layout valgono."""
    await tools.ue_umg_add_widget(wbp, "VerticalBox", name="Colonna")

    esito = await tools.ue_umg_add_widget(wbp, "Button", parent="Colonna", name="Ok")

    assert esito["slot_class"] == "VerticalBoxSlot"


async def test_un_widget_foglia_non_puo_contenere_altri(tools, wbp):
    await tools.ue_umg_add_widget(wbp, "TextBlock", name="Titolo")

    with pytest.raises(RuntimeError, match="PanelWidget"):
        await tools.ue_umg_add_widget(wbp, "Button", parent="Titolo")


async def test_i_nomi_dei_widget_sono_univoci(tools, wbp):
    await tools.ue_umg_add_widget(wbp, "TextBlock", name="Titolo")

    with pytest.raises(RuntimeError, match="univoci"):
        await tools.ue_umg_add_widget(wbp, "Button", name="Titolo")


async def test_widget_inesistente_elenca_quelli_presenti(tools, wbp):
    await tools.ue_umg_add_widget(wbp, "TextBlock", name="Titolo")

    with pytest.raises(RuntimeError, match="Radice, Titolo"):
        await tools.ue_umg_set_widget_property(wbp, "NonEsiste", {"Text": "x"})


async def test_add_widget_applica_subito_lo_slot(tools, wbp):
    esito = await tools.ue_umg_add_widget(
        wbp, "VerticalBox", name="Colonna", slot={"position": [80, 80], "size": [500, 300]}
    )

    assert esito["widget"] == "Colonna"


# --------------------------------------------------------------- proprietà


async def test_le_stringhe_diventano_text_dove_serve(tools, wbp):
    """`Text` è un `FText` nel motore e rifiuta una stringa nuda: il tool fa
    la conversione, altrimenti impostare un titolo fallirebbe sempre."""
    await tools.ue_umg_add_widget(wbp, "TextBlock", name="Titolo")

    esito = await tools.ue_umg_set_widget_property(wbp, "Titolo", {"Text": "Menu principale"})

    assert esito["applied"] == {"Text": "Menu principale"}
    assert esito["failed"] == {}


async def test_una_proprieta_sbagliata_non_fa_cadere_le_altre(tools, wbp):
    await tools.ue_umg_add_widget(wbp, "TextBlock", name="Titolo")

    esito = await tools.ue_umg_set_widget_property(
        wbp, "Titolo", {"Text": "Ciao", "ProprietaInventata": 3}
    )

    assert "Text" in esito["applied"]
    assert "ProprietaInventata" in esito["applied"] or "ProprietaInventata" in esito["failed"]


# --------------------------------------------------------------- slot


async def test_padding_diventa_un_margin_non_un_vector2d(tools, wbp):
    """La trappola della fase: dal ponte MCP `padding` e `position` arrivano
    entrambi come lista, ma il motore vuole due struct diversi."""
    await tools.ue_umg_add_widget(wbp, "VerticalBox", name="Colonna")
    await tools.ue_umg_add_widget(wbp, "TextBlock", parent="Colonna", name="Titolo")

    esito = await tools.ue_umg_set_slot(wbp, "Titolo", {"padding": [4, 8, 4, 8]})

    assert esito["applied"] == {"padding": [4, 8, 4, 8]}
    assert esito["failed"] == {}


async def test_padding_accetta_anche_i_quattro_lati_per_nome(tools, wbp):
    await tools.ue_umg_add_widget(wbp, "VerticalBox", name="Colonna")
    await tools.ue_umg_add_widget(wbp, "TextBlock", parent="Colonna", name="Titolo")

    esito = await tools.ue_umg_set_slot(
        wbp, "Titolo", {"padding": {"left": 8, "top": 4, "right": 8, "bottom": 4}}
    )

    assert esito["failed"] == {}


async def test_position_e_size_diventano_vector2d(tools, wbp):
    await tools.ue_umg_add_widget(wbp, "VerticalBox", name="Colonna")

    esito = await tools.ue_umg_set_slot(wbp, "Colonna", {"position": [80, 120], "size": [520, 340]})

    assert esito["slot_class"] == "CanvasPanelSlot"
    assert esito["failed"] == {}


# --------------------------------------------------------------- rimozione


async def test_remove_widget_lo_stacca_dal_genitore(tools, wbp):
    await tools.ue_umg_add_widget(wbp, "TextBlock", name="Titolo")

    esito = await tools.ue_umg_remove_widget(wbp, "Titolo")

    assert esito["removed"] == "Titolo"
    assert esito["was_child_of"] == "Radice"
    assert (await tools.ue_umg_tree_info(wbp))["root"]["children"] == []


async def test_la_radice_non_si_puo_rimuovere(tools, wbp):
    with pytest.raises(RuntimeError, match="RootWidget"):
        await tools.ue_umg_remove_widget(wbp, "Radice")


# --------------------------------------------------------------- salvataggio


async def test_ogni_modifica_compila_e_salva(tools, wbp, unreal):
    unreal.state["saved"].clear()

    await tools.ue_umg_add_widget(wbp, "TextBlock", name="Titolo")

    assert wbp in unreal.state["saved"]

"""Test del download preset: Poly Haven, ambientCG, Kenney, URL diretti, vault Fab."""

import zipfile
from pathlib import Path

import pytest
from fake_web import FakeWebServer

from unreal_mcp import assets


@pytest.fixture
def web(monkeypatch, tmp_path):
    server = FakeWebServer()
    monkeypatch.setenv("UE_MCP_LIBRARY", str(tmp_path / "library"))
    monkeypatch.setattr(assets, "POLYHAVEN_API", server.base + "/ph")
    monkeypatch.setattr(assets, "AMBIENTCG_API", server.base + "/acg/full_json")
    monkeypatch.setattr(assets, "AMBIENTCG_FILE", server.base + "/acg/get")
    monkeypatch.setattr(assets, "KENNEY_ASSET_PAGE", server.base + "/kenney/")
    yield server
    server.stop()


# ------------------------------------------------------------------ generico


async def test_download_url_e_libreria(web, tmp_path):
    result = await assets.download_file(web.base + "/files/diff_2k.jpg")
    assert result["downloaded"] is True
    assert Path(result["file"]).read_bytes() == b"fake-texture-bytes"

    # seconda chiamata: non riscarica
    again = await assets.download_file(web.base + "/files/diff_2k.jpg")
    assert again["downloaded"] is False

    files = assets.list_library(extensions=["jpg"])
    assert len(files) == 1 and files[0]["ext"] == ".jpg"


async def test_download_md5_sbagliato(web):
    with pytest.raises(assets.AssetError) as excinfo:
        await assets.download_file(web.base + "/files/diff_2k.jpg", expected_md5="0" * 32)
    assert "Checksum" in str(excinfo.value)


async def test_download_oltre_limite(web, monkeypatch):
    monkeypatch.setattr(assets, "MAX_DOWNLOAD_BYTES", 5)
    with pytest.raises(assets.AssetError) as excinfo:
        await assets.download_file(web.base + "/files/diff_2k.jpg")
    assert "UE_MCP_MAX_DOWNLOAD" in str(excinfo.value)


async def test_download_404(web):
    with pytest.raises(assets.AssetError) as excinfo:
        await assets.download_file(web.base + "/non-esiste")
    assert "HTTP 404" in str(excinfo.value)


def test_extract_zip(tmp_path):
    archive = tmp_path / "pack.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("Models/a.glb", b"glb")
        handle.writestr("readme.txt", b"txt")

    result = assets.extract_archive(str(archive), str(tmp_path / "out"))
    assert result["files"] == 2
    assert (tmp_path / "out/Models/a.glb").exists()


def test_extract_zip_path_traversal(tmp_path):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("../fuori.txt", b"nope")
        handle.writestr("dentro.txt", b"ok")

    result = assets.extract_archive(str(archive), str(tmp_path / "out"))
    assert result["files"] == 1
    assert (tmp_path / "out/dentro.txt").exists()
    assert not (tmp_path / "fuori.txt").exists()


def test_extract_rar_non_supportato(tmp_path):
    archive = tmp_path / "pack.rar"
    archive.write_bytes(b"Rar!\x1a\x07\x00")
    with pytest.raises(assets.AssetError) as excinfo:
        assets.extract_archive(str(archive))
    assert "7-Zip" in str(excinfo.value)


# ---------------------------------------------------------------- Poly Haven


async def test_polyhaven_search(web):
    found = await assets.polyhaven_search("textures")
    ids = {a["id"] for a in found}
    assert ids == {"brick_wall_02", "wooden_table"}
    assert next(a for a in found if a["id"] == "brick_wall_02")["type"] == "textures"


async def test_polyhaven_search_tipo_invalido(web):
    with pytest.raises(assets.AssetError):
        await assets.polyhaven_search("suoni")


async def test_polyhaven_download_texture(web):
    result = await assets.polyhaven_download("brick_wall_02", "2k", ["jpg"])
    assert result["count"] == 1
    assert result["license"] == "CC0"
    assert Path(result["files"][0]["file"]).name == "diff_2k.jpg"


async def test_polyhaven_download_gltf_include_dipendenze(web):
    result = await assets.polyhaven_download("brick_wall_02", "2k", ["gltf"])
    nomi = sorted(Path(f["file"]).name for f in result["files"])
    assert nomi == ["diff_2k.jpg", "model.bin", "model_2k.gltf"]
    # le dipendenze mantengono la struttura di cartelle dichiarata
    assert any("textures" in f["file"] for f in result["files"])


async def test_polyhaven_risoluzione_assente(web):
    with pytest.raises(assets.AssetError) as excinfo:
        await assets.polyhaven_download("brick_wall_02", "16k", ["jpg"])
    assert "Risoluzioni disponibili" in str(excinfo.value)


async def test_polyhaven_asset_inesistente(web):
    with pytest.raises(assets.AssetError) as excinfo:
        await assets.polyhaven_download("non_esiste")
    assert "non trovato" in str(excinfo.value)


# ------------------------------------------------------------------ ambientCG


async def test_ambientcg_search(web):
    found = await assets.ambientcg_search("stone", "Material", limit=1)
    assert len(found) == 1 and found[0]["id"] == "PavingStones036"


async def test_ambientcg_download_estrae(web):
    result = await assets.ambientcg_download("PavingStones036", "2K-JPG")
    assert result["extracted"]["files"] == 2
    destinazione = Path(result["extracted"]["destination"])
    assert (destinazione / "PavingStones036_2K_Color.jpg").exists()
    assert "PavingStones036_2K-JPG.zip" in web.hits[-1] or any(
        "PavingStones036_2K-JPG.zip" in hit for hit in web.hits
    )


# --------------------------------------------------------------------- Kenney


async def test_kenney_resolve_e_download(web):
    link = await assets.kenney_resolve("mini-characters-1")
    assert link.endswith("mini-characters-1.zip")

    result = await assets.kenney_download("mini-characters-1")
    destinazione = Path(result["extracted"]["destination"])
    assert (destinazione / "Models/GLB/character.glb").exists()
    assert result["license"] == "CC0"


async def test_kenney_slug_sbagliato(web):
    with pytest.raises(assets.AssetError) as excinfo:
        await assets.kenney_resolve("pack-inventato")
    assert "slug" in str(excinfo.value)


# ------------------------------------------------------------------- Fab/Epic


def test_fab_senza_legendary(monkeypatch):
    monkeypatch.setattr(assets, "_legendary_path", lambda: None)
    with pytest.raises(assets.AssetError) as excinfo:
        assets.fab_list_vault()
    messaggio = str(excinfo.value)
    assert "legendary-gl" in messaggio and "Epic Games Launcher" in messaggio
    assert "fab_install" in messaggio


def test_fab_list_vault_parsing(monkeypatch):
    monkeypatch.setattr(assets, "_legendary_path", lambda: "/usr/bin/legendary")
    output = (
        "Available games:\n"
        " * Soul City (App name: SoulCity, version: 4.27)\n"
        " * Infinity Blade: Grass Lands (App name: GrassLands, version: 5.0)\n"
    )
    monkeypatch.setattr(
        assets.subprocess, "run",
        lambda *a, **k: type("R", (), {"returncode": 0, "stdout": output, "stderr": ""})(),
    )
    result = assets.fab_list_vault()
    assert result["count"] == 2
    assert result["assets"][0] == {"title": "Soul City", "app_name": "SoulCity", "version": "4.27"}


def test_fab_errore_legendary(monkeypatch):
    monkeypatch.setattr(assets, "_legendary_path", lambda: "/usr/bin/legendary")
    monkeypatch.setattr(
        assets.subprocess, "run",
        lambda *a, **k: type("R", (), {"returncode": 1, "stdout": "", "stderr": "not logged in"})(),
    )
    with pytest.raises(assets.AssetError) as excinfo:
        assets.fab_list_vault()
    assert "not logged in" in str(excinfo.value)


async def test_download_filename_traversal(web, tmp_path):
    """Un `filename` con ../ deve restare dentro la cartella di destinazione."""
    destinazione = tmp_path / "libreria"
    esito = await assets.download_file(
        web.base + "/files/diff_2k.jpg",
        destination=str(destinazione),
        filename="../../fuori.jpg",
    )
    scritto = Path(esito["file"]).resolve()
    assert destinazione.resolve() in scritto.parents
    assert scritto.name == "fuori.jpg"
    assert not (tmp_path.parent / "fuori.jpg").exists()


def test_extract_tar_scarta_symlink_fuori_target(tmp_path):
    """Un membro symlink può puntare fuori pur avendo un nome innocuo."""
    import tarfile as _tar

    archivio = tmp_path / "malevolo.tar"
    with _tar.open(archivio, "w") as tf:
        collegamento = _tar.TarInfo("innocuo.txt")
        collegamento.type = _tar.SYMTYPE
        collegamento.linkname = "../../../../etc/passwd"
        tf.addfile(collegamento)

    destinazione = tmp_path / "estratto"
    # Deve essere un AssetError su ogni versione: su 3.12+ il filtro di tarfile
    # solleva LinkOutsideDestinationError, che il chiamante non conosce.
    with pytest.raises(assets.AssetError, match="destinazione"):
        assets.extract_archive(str(archivio), str(destinazione))
    assert not (destinazione / "innocuo.txt").is_symlink()


# ------------------------------------------------- Fab: stato, ricerca, installazione


def _legendary_finto(monkeypatch, output, returncode=0):
    monkeypatch.setattr(assets, "_legendary_path", lambda: "/usr/bin/legendary")
    monkeypatch.setattr(
        assets.subprocess,
        "run",
        lambda *a, **k: type("R", (), {"returncode": returncode, "stdout": output, "stderr": output})(),
    )


def test_fab_status_senza_client(monkeypatch):
    monkeypatch.setattr(assets, "_legendary_path", lambda: None)
    stato = assets.fab_status()
    assert stato == {"cli_installed": False, "logged_in": False, "hint": assets.LEGENDARY_HINT}


def test_fab_status_loggato(monkeypatch):
    _legendary_finto(monkeypatch, '{"account": "ash@example.com", "games_available": 12}')
    stato = assets.fab_status()
    assert stato["cli_installed"] is True
    assert stato["logged_in"] is True
    assert stato["account"] == "ash@example.com"
    assert stato["games_available"] == 12


def test_fab_status_non_loggato(monkeypatch):
    """Client presente ma senza login: il rimedio è diverso e va detto."""
    _legendary_finto(monkeypatch, "not logged in", returncode=1)
    stato = assets.fab_status()
    assert stato["cli_installed"] is True and stato["logged_in"] is False
    assert "legendary auth" in stato["hint"]


def test_fab_list_vault_da_json(monkeypatch):
    _legendary_finto(
        monkeypatch,
        '[{"app_name": "SoulCity", "app_title": "Soul City",'
        ' "asset_infos": {"Windows": {"build_version": "5.4"}}}]',
    )
    result = assets.fab_list_vault()
    assert result["assets"] == [{"title": "Soul City", "app_name": "SoulCity", "version": "5.4"}]


def test_fab_list_vault_filtra_per_query(monkeypatch):
    output = (
        "Available games:\n"
        " * Soul City (App name: SoulCity, version: 5.4)\n"
        " * Infinity Blade: Grass Lands (App name: GrassLands, version: 5.0)\n"
    )
    _legendary_finto(monkeypatch, output)
    result = assets.fab_list_vault(query="grass")
    assert result["count"] == 1
    assert result["assets"][0]["app_name"] == "GrassLands"


def _pack_di_contenuto(base: Path, radice="SoulCity/data") -> Path:
    """Layout tipico di un pack del vault: il Content è annidato in profondità."""
    content = base / radice / "Content"
    (content / "Meshes").mkdir(parents=True)
    (content / "Meshes/SM_Wall.uasset").write_bytes(b"fake-uasset")
    (content / "Meshes/SM_Floor.uasset").write_bytes(b"fake-uasset")
    # Le cartelle di build non devono contare come contenuto.
    (base / radice / "Intermediate").mkdir(parents=True)
    return base / "SoulCity"


def _progetto(base: Path) -> Path:
    progetto = base / "MyGame"
    (progetto / "Content").mkdir(parents=True)
    uproject = progetto / "MyGame.uproject"
    uproject.write_text("{}", encoding="utf-8")
    return uproject


def test_inspect_pack_trova_il_content_annidato(tmp_path):
    pack = _pack_di_contenuto(tmp_path)
    layout = assets.inspect_pack(pack)
    assert layout["plugins"] == []
    assert len(layout["content_dirs"]) == 1
    assert layout["content_dirs"][0]["uassets"] == 2


def test_fab_install_copia_il_contenuto_nel_progetto(tmp_path):
    pack = _pack_di_contenuto(tmp_path)
    uproject = _progetto(tmp_path)

    esito = assets.fab_install(str(pack), str(uproject))

    assert esito["unreal_paths"] == ["/Game/SoulCity"]
    destinazione = Path(esito["content_installed"][0]["destination"])
    assert (destinazione / "Meshes/SM_Wall.uasset").exists()
    assert esito["content_installed"][0]["uassets"] == 2


def test_fab_install_non_sovrascrive_senza_permesso(tmp_path):
    pack = _pack_di_contenuto(tmp_path)
    uproject = _progetto(tmp_path)
    assets.fab_install(str(pack), str(uproject))

    with pytest.raises(assets.AssetError, match="overwrite"):
        assets.fab_install(str(pack), str(uproject))

    # Con il permesso esplicito passa.
    assets.fab_install(str(pack), str(uproject), overwrite=True)


def test_fab_install_subfolder_non_esce_da_content(tmp_path):
    """Un subfolder con ../ scriverebbe fuori dal progetto."""
    pack = _pack_di_contenuto(tmp_path)
    uproject = _progetto(tmp_path)

    esito = assets.fab_install(str(pack), str(uproject), subfolder="../../fuori")
    destinazione = Path(esito["content_installed"][0]["destination"]).resolve()
    assert (uproject.parent / "Content").resolve() in destinazione.parents
    assert not (tmp_path / "fuori").exists()


def test_fab_install_plugin(tmp_path):
    plugin = tmp_path / "PackPlugin/Windows/MioPlugin"
    (plugin / "Source").mkdir(parents=True)
    (plugin / "Content").mkdir()
    (plugin / "Content/BP_Cosa.uasset").write_bytes(b"fake-uasset")
    (plugin / "MioPlugin.uplugin").write_text("{}", encoding="utf-8")
    (plugin / "Binaries").mkdir()
    (plugin / "Binaries/scarto.dll").write_bytes(b"x")
    uproject = _progetto(tmp_path)

    esito = assets.fab_install(str(tmp_path / "PackPlugin"), str(uproject))

    assert [p["name"] for p in esito["plugins_installed"]] == ["MioPlugin"]
    installato = Path(esito["plugins_installed"][0]["destination"])
    assert (installato / "MioPlugin.uplugin").exists()
    # I binari precompilati non vanno copiati: sono legati a un'altra build.
    assert not (installato / "Binaries").exists()
    # Il Content del plugin resta suo, non finisce anche in /Game.
    assert esito["content_installed"] == []
    assert any("C++" in a for a in esito["warnings"])
    assert any("riavviat" in a for a in esito["warnings"])


def test_fab_install_pack_vuoto_spiega_cosa_ha_visto(tmp_path):
    vuoto = tmp_path / "Strano"
    (vuoto / "Documentazione").mkdir(parents=True)
    uproject = _progetto(tmp_path)
    with pytest.raises(assets.AssetError, match="Documentazione"):
        assets.fab_install(str(vuoto), str(uproject))


def test_fab_install_da_zip(tmp_path, monkeypatch):
    monkeypatch.setenv("UE_MCP_LIBRARY", str(tmp_path / "library"))
    sorgente = tmp_path / "zipsrc/Content/Meshes"
    sorgente.mkdir(parents=True)
    (sorgente / "SM_Rock.uasset").write_bytes(b"fake-uasset")
    archivio = tmp_path / "RockPack.zip"
    with zipfile.ZipFile(archivio, "w") as zf:
        zf.write(sorgente / "SM_Rock.uasset", "Content/Meshes/SM_Rock.uasset")
    uproject = _progetto(tmp_path)

    esito = assets.fab_install(str(archivio), str(uproject))
    assert esito["unreal_paths"] == ["/Game/RockPack"]


def test_fab_install_scarica_se_non_e_un_percorso(tmp_path, monkeypatch):
    """Un app_name del vault viene scaricato al volo e poi installato."""
    pack = _pack_di_contenuto(tmp_path)
    uproject = _progetto(tmp_path)
    monkeypatch.setattr(
        assets, "fab_download", lambda app_name, destination=None: {"path": str(pack), "app_name": app_name}
    )

    esito = assets.fab_install("SoulCity", str(uproject), subfolder="Fab/SoulCity")
    assert esito["download"]["app_name"] == "SoulCity"
    assert esito["unreal_paths"] == ["/Game/Fab/SoulCity"]

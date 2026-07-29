"""Download di asset e preset da fonti gratuite, direttamente sul disco locale.

Fonti con API pubblica e licenza CC0:
- **Poly Haven** (https://api.polyhaven.com) — HDRI, texture PBR, modelli.
- **ambientCG** (https://ambientcg.com/api/v2/full_json) — materiali PBR, HDRI, modelli.
- **Kenney** (https://kenney.nl) — pack low-poly; niente API, si risolve il link dalla pagina.
- URL diretto — qualunque zip/glb/fbx/wav.

Contenuti Fab/Marketplace acquistati richiedono l'autenticazione Epic: non
esiste API pubblica, si passa dal client community `legendary` (vedi
:func:`fab_list_vault`).
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

USER_AGENT = "unreal-mcp/0.2"
POLYHAVEN_API = "https://api.polyhaven.com"
AMBIENTCG_API = "https://ambientcg.com/api/v2/full_json"
AMBIENTCG_FILE = "https://ambientcg.com/get"
KENNEY_ASSET_PAGE = "https://kenney.nl/assets/"

#: Limite di sicurezza per singolo file (byte). Override: UE_MCP_MAX_DOWNLOAD.
MAX_DOWNLOAD_BYTES = int(os.environ.get("UE_MCP_MAX_DOWNLOAD", 4 * 1024**3))


class AssetError(RuntimeError):
    """Errore di download/estrazione."""


def library_dir() -> Path:
    """Cartella locale dove finiscono i preset scaricati."""
    path = Path(os.environ.get("UE_MCP_LIBRARY", Path.home() / "UnrealAssetLibrary"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _client(timeout: float = 300.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
        follow_redirects=True,
        trust_env=False,
    )


async def _get_json(url: str, params: dict | None = None) -> object:
    async with _client(60.0) as client:
        response = await client.get(url, params=params)
        if response.status_code >= 400:
            raise AssetError("HTTP %s da %s" % (response.status_code, url))
        try:
            return response.json()
        except ValueError as exc:
            raise AssetError("Risposta non JSON da %s" % url) from exc


# ------------------------------------------------------------------ download


async def download_file(
    url: str,
    destination: str | None = None,
    filename: str | None = None,
    expected_md5: str | None = None,
    overwrite: bool = False,
) -> dict:
    """Scarica un file in streaming nella libreria locale."""
    folder = Path(destination).expanduser() if destination else library_dir()
    folder.mkdir(parents=True, exist_ok=True)

    # Path(...).name su entrambi i rami: un `filename` con "../" o un URL
    # costruito ad arte scriverebbe fuori dalla cartella di destinazione.
    name = Path(filename or urlparse(url).path).name or "download.bin"
    target = folder / name
    if target.exists() and not overwrite:
        return {
            "url": url, "file": str(target), "bytes": target.stat().st_size,
            "downloaded": False, "reason": "già presente (overwrite=False)",
        }

    digest = hashlib.md5()  # noqa: S324 - solo per confronto con l'md5 pubblicato
    written = 0
    temp = target.with_suffix(target.suffix + ".part")
    async with _client() as client:
        async with client.stream("GET", url) as response:
            if response.status_code >= 400:
                raise AssetError("HTTP %s scaricando %s" % (response.status_code, url))
            declared = int(response.headers.get("Content-Length") or 0)
            if declared > MAX_DOWNLOAD_BYTES:
                raise AssetError(
                    "File da %.1f GB: oltre il limite. Alza UE_MCP_MAX_DOWNLOAD se è voluto."
                    % (declared / 1024**3)
                )
            with open(temp, "wb") as handle:
                async for chunk in response.aiter_bytes(1024 * 256):
                    written += len(chunk)
                    if written > MAX_DOWNLOAD_BYTES:
                        handle.close()
                        temp.unlink(missing_ok=True)
                        raise AssetError("Download interrotto: superato UE_MCP_MAX_DOWNLOAD.")
                    digest.update(chunk)
                    handle.write(chunk)

    checksum = digest.hexdigest()
    if expected_md5 and checksum != expected_md5:
        temp.unlink(missing_ok=True)
        raise AssetError(
            "Checksum md5 non corrispondente per %s (atteso %s, ottenuto %s)."
            % (name, expected_md5, checksum)
        )

    temp.replace(target)
    return {"url": url, "file": str(target), "bytes": written, "md5": checksum, "downloaded": True}


def extract_archive(archive: str, destination: str | None = None) -> dict:
    """Estrae zip/tar. I .rar non sono supportati dalla stdlib."""
    path = Path(archive).expanduser()
    if not path.exists():
        raise AssetError("Archivio non trovato: %s" % path)

    target = Path(destination).expanduser() if destination else path.with_suffix("")
    target.mkdir(parents=True, exist_ok=True)

    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive_file:
            members = [m for m in archive_file.namelist() if not m.startswith(("/", "..")) and ".." not in Path(m).parts]
            archive_file.extractall(target, members=members)  # noqa: S202 - membri già filtrati sopra
        extracted = members
    elif tarfile.is_tarfile(path):
        with tarfile.open(path) as archive_file:
            members = [m for m in archive_file.getmembers() if not m.name.startswith(("/", "..")) and ".." not in Path(m.name).parts]
            # Il filtro sui nomi non basta: un membro symlink o hardlink può
            # puntare fuori dalla destinazione pur avendo un nome innocuo.
            # `filter="data"` è il comportamento predefinito da Python 3.14,
            # ma qui il minimo supportato è 3.10.
            if sys.version_info >= (3, 12):
                archive_file.extractall(target, members=members, filter="data")
            else:  # pragma: no cover - solo su 3.10/3.11
                members = [
                    m for m in members if not (m.issym() or m.islnk() or m.isdev())
                ]
                archive_file.extractall(target, members=members)  # noqa: S202 - membri già filtrati sopra
        extracted = [m.name for m in members]
    elif path.suffix.lower() == ".rar":
        raise AssetError(
            "Formato .rar non supportato. Estrai manualmente (7-Zip/WinRAR) e poi usa "
            "ue_import_assets sui file estratti."
        )
    else:
        raise AssetError("Formato non riconosciuto: %s" % path.suffix)

    return {
        "archive": str(path),
        "destination": str(target),
        "files": len(extracted),
        "sample": extracted[:15],
    }


def list_library(subfolder: str | None = None, extensions: list[str] | None = None) -> list[dict]:
    """Elenca i file scaricati, pronti per ue_import_assets."""
    base = library_dir() / subfolder if subfolder else library_dir()
    if not base.exists():
        return []
    allowed = {e.lower().lstrip(".") for e in (extensions or [])}
    out = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        if allowed and path.suffix.lower().lstrip(".") not in allowed:
            continue
        out.append({"file": str(path), "bytes": path.stat().st_size, "ext": path.suffix.lower()})
    return out


# ---------------------------------------------------------------- Poly Haven


async def polyhaven_search(
    asset_type: str | None = None, categories: list[str] | None = None, limit: int = 40
) -> list[dict]:
    """Cerca su Poly Haven. asset_type: hdris | textures | models."""
    params = {}
    if asset_type:
        if asset_type not in {"hdris", "textures", "models"}:
            raise AssetError("asset_type deve essere hdris, textures o models.")
        params["t"] = asset_type
    if categories:
        params["c"] = ",".join(categories)

    data = await _get_json(POLYHAVEN_API + "/assets", params or None)
    if not isinstance(data, dict):
        raise AssetError("Risposta inattesa da Poly Haven.")
    out = []
    for asset_id, meta in list(data.items())[: max(1, limit)]:
        out.append(
            {
                "id": asset_id,
                "name": meta.get("name"),
                "type": {0: "hdris", 1: "textures", 2: "models"}.get(meta.get("type"), meta.get("type")),
                "categories": meta.get("categories", []),
                "tags": meta.get("tags", [])[:8],
            }
        )
    return out


def _polyhaven_collect(files: dict, resolution: str, formats: set[str]) -> list[dict]:
    wanted = []
    for map_name, resolutions in files.items():
        if not isinstance(resolutions, dict) or resolution not in resolutions:
            continue
        for file_format, entry in resolutions[resolution].items():
            if file_format not in formats or not isinstance(entry, dict) or "url" not in entry:
                continue
            wanted.append(
                {
                    "url": entry["url"],
                    "md5": entry.get("md5"),
                    "relpath": Path(urlparse(entry["url"]).path).name,
                    "map": map_name,
                }
            )
            for relpath, sub in (entry.get("include") or {}).items():
                wanted.append(
                    {"url": sub["url"], "md5": sub.get("md5"), "relpath": relpath, "map": map_name}
                )
    return wanted


async def polyhaven_download(
    asset_id: str,
    resolution: str = "2k",
    formats: list[str] | None = None,
    destination: str | None = None,
) -> dict:
    """Scarica un asset Poly Haven (CC0).

    formats tipici: ["jpg"] per texture, ["gltf"] per modelli (include i .bin e
    le texture collegate), ["hdr"] o ["exr"] per HDRI.
    """
    files = await _get_json(f"{POLYHAVEN_API}/files/{asset_id}")
    if not isinstance(files, dict) or not files:
        raise AssetError("Asset '%s' non trovato su Poly Haven." % asset_id)

    allowed = set(formats or ["jpg", "hdr", "gltf"])
    wanted = _polyhaven_collect(files, resolution, allowed)
    if not wanted:
        available = sorted({r for v in files.values() if isinstance(v, dict) for r in v})
        raise AssetError(
            "Nessun file per risoluzione '%s' e formati %s. Risoluzioni disponibili: %s"
            % (resolution, sorted(allowed), ", ".join(available))
        )

    folder = Path(destination).expanduser() if destination else library_dir() / "polyhaven" / asset_id
    results = []
    for item in wanted:
        relative = Path(item["relpath"])
        results.append(
            await download_file(
                item["url"],
                destination=str(folder / relative.parent) if relative.parent != Path(".") else str(folder),
                filename=relative.name,
                expected_md5=item["md5"],
            )
        )
    return {
        "asset": asset_id, "source": "polyhaven", "license": "CC0",
        "resolution": resolution, "destination": str(folder),
        "files": results, "count": len(results),
    }


# ------------------------------------------------------------------ ambientCG


async def ambientcg_search(
    query: str = "", asset_type: str = "Material", limit: int = 20
) -> list[dict]:
    """Cerca su ambientCG. asset_type: Material | HDRI | 3DModel | Decal | Atlas | Terrain."""
    data = await _get_json(
        AMBIENTCG_API,
        {"q": query, "type": asset_type, "limit": max(1, min(int(limit), 250)), "include": "displayData"},
    )
    assets = data.get("foundAssets", []) if isinstance(data, dict) else []
    return [
        {
            "id": asset.get("assetId"),
            "type": asset.get("dataType"),
            "title": asset.get("displayName") or asset.get("assetId"),
            "category": asset.get("displayCategory"),
        }
        for asset in assets
    ]


async def ambientcg_download(
    asset_id: str, variant: str = "2K-JPG", destination: str | None = None
) -> dict:
    """Scarica un asset ambientCG (CC0). variant tipici: 1K-JPG, 2K-JPG, 4K-PNG."""
    filename = f"{asset_id}_{variant}.zip"
    folder = Path(destination).expanduser() if destination else library_dir() / "ambientcg"
    downloaded = await download_file(
        f"{AMBIENTCG_FILE}?file={filename}", destination=str(folder), filename=filename
    )
    extracted = extract_archive(downloaded["file"], str(folder / asset_id))
    return {
        "asset": asset_id, "source": "ambientcg", "license": "CC0",
        "variant": variant, "archive": downloaded, "extracted": extracted,
    }


# --------------------------------------------------------------------- Kenney


async def kenney_resolve(slug: str) -> str:
    """Trova il link .zip nella pagina di un pack Kenney."""
    url = KENNEY_ASSET_PAGE + slug.strip("/")
    async with _client(60.0) as client:
        response = await client.get(url)
    if response.status_code >= 400:
        raise AssetError(
            "Pagina Kenney non raggiungibile (%s). Controlla lo slug: per "
            "kenney.nl/assets/mini-characters-1 lo slug è 'mini-characters-1'."
            % response.status_code
        )
    matches = re.findall(r'href="([^"]+\.zip)"', response.text)
    if not matches:
        raise AssetError(
            "Nessun link .zip trovato in %s. Scarica manualmente e usa "
            "preset_extract_archive sul file." % url
        )
    # urljoin risolve sia i link assoluti sia quelli relativi alla pagina
    return urljoin(str(response.url), matches[0])


async def kenney_download(slug: str, destination: str | None = None) -> dict:
    """Scarica ed estrae un pack Kenney (CC0)."""
    link = await kenney_resolve(slug)
    folder = Path(destination).expanduser() if destination else library_dir() / "kenney"
    downloaded = await download_file(link, destination=str(folder))
    extracted = extract_archive(downloaded["file"], str(folder / slug))
    return {
        "asset": slug, "source": "kenney", "license": "CC0",
        "url": link, "archive": downloaded, "extracted": extracted,
    }


# ------------------------------------------------------------- Fab / vault Epic


def _legendary_path() -> str | None:
    return shutil.which("legendary")


def _run_legendary(args: list[str], timeout: float = 900.0) -> str:
    executable = _legendary_path()
    if executable is None:
        raise AssetError(
            "Il contenuto Fab/Marketplace acquistato è protetto da login Epic e non ha "
            "API pubblica. Per scaricarlo da riga di comando serve il client community "
            "'legendary':\n"
            "  pip install legendary-gl\n"
            "  legendary auth\n"
            "Dopo l'autenticazione questo tool funziona. In alternativa scarica i pack "
            "dall'Epic Games Launcher (scheda Fab) e usa ue_import_assets."
        )
    result = subprocess.run(  # noqa: S603
        [executable, *args], capture_output=True, text=True, timeout=timeout
    )
    if result.returncode != 0:
        raise AssetError(
            "legendary %s è fallito (exit %s):\n%s"
            % (" ".join(args), result.returncode, (result.stderr or result.stdout)[-1500:])
        )
    return result.stdout


def fab_list_vault() -> dict:
    """Elenca il contenuto Unreal acquistato sull'account Epic (richiede legendary)."""
    output = _run_legendary(["list", "--include-ue"], timeout=300)
    entries = []
    for line in output.splitlines():
        match = re.search(r"\*\s+(.+?)\s+\(App name:\s*([^,]+),\s*version:\s*([^)]*)\)", line)
        if match:
            entries.append(
                {"title": match.group(1).strip(), "app_name": match.group(2).strip(), "version": match.group(3).strip()}
            )
    return {"count": len(entries), "assets": entries, "raw_tail": output[-500:] if not entries else ""}


def fab_download(app_name: str, destination: str | None = None) -> dict:
    """Scarica un asset del vault Epic tramite legendary."""
    folder = Path(destination).expanduser() if destination else library_dir() / "fab"
    folder.mkdir(parents=True, exist_ok=True)
    output = _run_legendary(
        ["download", app_name, "--base-path", str(folder), "--skip-sdl", "--yes"]
    )
    return {"app_name": app_name, "destination": str(folder), "log_tail": output[-1000:]}

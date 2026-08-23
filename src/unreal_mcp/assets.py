"""Download di asset e preset da fonti gratuite, direttamente sul disco locale.

Fonti con API pubblica e licenza CC0:
- **Poly Haven** (https://api.polyhaven.com) — HDRI, texture PBR, modelli.
- **ambientCG** (https://ambientcg.com/api/v2/full_json) — materiali PBR, HDRI, modelli.
- **Kenney** (https://kenney.nl) — pack low-poly; niente API, si risolve il link dalla pagina.
- URL diretto — qualunque zip/glb/fbx/wav.

Contenuti Fab/Marketplace acquistati richiedono l'autenticazione Epic: non
esiste API pubblica, si passa dal client community `legendary` (vedi
:func:`fab_status`). Il flusso completo è fab_status -> fab_list_vault ->
fab_install, che scarica il pack e lo copia dentro Content/ o Plugins/ del
progetto: da lì l'editor lo vede come contenuto proprio.
"""

from __future__ import annotations

import hashlib
import json
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
                try:
                    archive_file.extractall(target, members=members, filter="data")
                except tarfile.TarError as exc:
                    # Su 3.12+ il filtro solleva le sue eccezioni (per esempio
                    # LinkOutsideDestinationError), che non sono AssetError e
                    # arriverebbero al chiamante come traceback grezzo: qui
                    # l'archivio è semplicemente ostile, e va detto così.
                    raise AssetError(
                        "Archivio rifiutato: %s. Un membro esce dalla cartella di "
                        "destinazione (symlink, hardlink o percorso assoluto)." % exc
                    ) from exc
            else:  # pragma: no cover - solo su 3.10/3.11
                # Stesso esito senza `filter`, che qui non esiste: i membri che
                # possono puntare fuori dalla destinazione si scartano a mano.
                pericolosi = [m for m in members if m.issym() or m.islnk() or m.isdev()]
                if pericolosi:
                    raise AssetError(
                        "Archivio rifiutato: %s esce dalla cartella di destinazione "
                        "(symlink, hardlink o file speciale)." % pericolosi[0].name
                    )
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

#: Cartelle che, dentro un pack Fab, non contengono contenuto da installare.
FAB_SKIP_DIRS = frozenset(
    {"Binaries", "Intermediate", "Saved", "DerivedDataCache", "__pycache__", ".git", ".svn"}
)

LEGENDARY_HINT = (
    "Il contenuto Fab/Marketplace acquistato è protetto da login Epic e non ha "
    "API pubblica. Per scaricarlo da riga di comando serve il client community "
    "'legendary':\n"
    "  pip install legendary-gl\n"
    "  legendary auth\n"
    "Dopo l'autenticazione questo tool funziona. In alternativa scarica il pack "
    "dall'Epic Games Launcher (scheda Fab) o dalla finestra Fab dell'editor e passa "
    "la cartella a fab_install: accetta sia un app_name del vault sia un percorso "
    "già presente sul disco."
)


def _legendary_from_python() -> str | None:
    """legendary installato via pip ma con gli script fuori dal PATH.

    È il caso normale su Windows quando il server MCP gira dentro un venv: la
    cartella `Scripts` del venv non è nel PATH del processo che lo ha lanciato.
    """
    base = Path(sys.executable).parent
    for folder in (base, base / "Scripts", base / "bin"):
        for name in ("legendary", "legendary.exe"):
            candidate = folder / name
            if candidate.exists():
                return str(candidate)
    return None


def _legendary_path() -> str | None:
    return shutil.which("legendary") or _legendary_from_python()


# Quanto si aspetta `legendary` prima di arrendersi. Il valore precedente era
# 300 s, cioe' **piu' lungo del timeout di qualsiasi client MCP**: quando
# qualcosa andava storto il tool non falliva, spariva — il client mollava per
# primo e l'errore vero non arrivava mai a destinazione. Meglio un errore
# leggibile a 150 s che un silenzio a 300.
STATUS_TIMEOUT = 30.0
VAULT_TIMEOUT = 150.0


def _run_legendary(args: list[str], timeout: float = 900.0) -> str:
    """Esegue `legendary` e ne restituisce lo stdout.

    ## `stdin=DEVNULL` non e' pignoleria

    Questo server parla MCP **su stdio**: il suo stdin e' la pipe JSON-RPC del
    client. `subprocess.run` senza `stdin` esplicito fa ereditare quella pipe al
    figlio — e se `legendary` prova a leggere una riga (una conferma, una
    richiesta di credenziali, qualunque prompt) si mette in attesa su un flusso
    che non gli parlera' mai, e nel frattempo **si mangia i byte del protocollo**.
    Il sintomo e' un tool che non torna mai, senza un errore da nessuna parte,
    mentre lo stesso comando da terminale risponde in tre secondi. Misurato il
    2026-08-23: `legendary status --json` 3,18 s a mano, mai una risposta dal
    tool.

    Nota: in `local.py` ci sono altri quattordici punti che lanciano processi
    figli senza `stdin` esplicito. Non hanno mai dato problemi — molti sono
    DETACHED e nessuno legge stdin — ma il meccanismo e' lo stesso.
    """
    executable = _legendary_path()
    if executable is None:
        raise AssetError(LEGENDARY_HINT)
    result = subprocess.run(  # noqa: S603
        [executable, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        raise AssetError(
            "legendary %s è fallito (exit %s):\n%s"
            % (" ".join(args), result.returncode, (result.stderr or result.stdout)[-1500:])
        )
    return result.stdout


def fab_status() -> dict:
    """Stato del ponte verso il vault Epic: CLI presente e login effettuato.

    Da chiamare prima di list/download: distingue "manca il client" da "manca
    il login", che sono due rimedi diversi e altrimenti arrivano come lo stesso
    errore generico.
    """
    executable = _legendary_path()
    if executable is None:
        return {"cli_installed": False, "logged_in": False, "hint": LEGENDARY_HINT}

    info: dict = {"cli_installed": True, "executable": executable}
    try:
        raw = _run_legendary(["status", "--json"], timeout=STATUS_TIMEOUT)
    except AssetError as exc:
        info.update(
            {
                "logged_in": False,
                "error": str(exc)[-600:],
                "hint": "Esegui 'legendary auth' in un terminale e riprova.",
            }
        )
        return info

    try:
        data = json.loads(raw)
    except ValueError:
        data = {}
    account = str(data.get("account") or "").strip()
    info["account"] = account or None
    info["games_available"] = data.get("games_available")
    info["logged_in"] = bool(account) and "not logged in" not in account.casefold()
    if not info["logged_in"]:
        info["hint"] = "Esegui 'legendary auth' in un terminale e riprova."
    return info


def _parse_vault_text(output: str) -> list[dict]:
    entries = []
    for line in output.splitlines():
        match = re.search(r"\*\s+(.+?)\s+\(App name:\s*([^,]+),\s*version:\s*([^)]*)\)", line)
        if match:
            entries.append(
                {
                    "title": match.group(1).strip(),
                    "app_name": match.group(2).strip(),
                    "version": match.group(3).strip(),
                }
            )
    return entries


def _vault_from_json() -> list[dict] | None:
    """Elenco del vault dal JSON di legendary, o None se questa versione non lo dà."""
    raw = _run_legendary(["list", "--include-ue", "--json"], timeout=VAULT_TIMEOUT)
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, list):
        return None
    entries = []
    for item in data:
        if not isinstance(item, dict):
            return None
        app_name = str(item.get("app_name") or "").strip()
        if not app_name:
            continue
        version = ""
        infos = item.get("asset_infos")
        if isinstance(infos, dict):
            for value in infos.values():
                if isinstance(value, dict) and value.get("build_version"):
                    version = str(value["build_version"])
                    break
        entries.append(
            {"title": str(item.get("app_title") or app_name), "app_name": app_name, "version": version}
        )
    return entries


def fab_list_vault(query: str | None = None) -> dict:
    """Elenca il contenuto Unreal acquistato sull'account Epic (richiede legendary).

    Args:
        query: filtro parziale, case-insensitive, su titolo o app_name.
    """
    entries: list[dict] | None
    try:
        entries = _vault_from_json()
    except AssetError:
        # Le versioni di legendary senza `--json` escono con errore: il testo
        # resta l'unico formato disponibile, e vale la pena riprovare da lì
        # prima di dare per persa la chiamata.
        entries = None

    raw_tail = ""
    if entries is None:
        output = _run_legendary(["list", "--include-ue"], timeout=VAULT_TIMEOUT)
        entries = _parse_vault_text(output)
        raw_tail = output[-500:] if not entries else ""

    if query:
        needle = query.casefold()
        entries = [
            e for e in entries if needle in e["title"].casefold() or needle in e["app_name"].casefold()
        ]

    return {"count": len(entries), "query": query, "assets": entries, "raw_tail": raw_tail}


def _guess_pack_root(folder: Path, app_name: str) -> Path | None:
    """Cartella creata da legendary per un pack, quando il diff non l'ha vista."""
    named = folder / app_name
    if named.is_dir():
        return named
    subdirs = [p for p in folder.iterdir() if p.is_dir()]
    if not subdirs:
        return None
    return max(subdirs, key=lambda p: p.stat().st_mtime)


def fab_download(app_name: str, destination: str | None = None) -> dict:
    """Scarica un asset del vault Epic tramite legendary.

    Restituisce anche `path`, la cartella del pack: è quella da passare a
    :func:`fab_install`.
    """
    folder = Path(destination).expanduser() if destination else library_dir() / "fab"
    folder.mkdir(parents=True, exist_ok=True)
    before = {p.name for p in folder.iterdir() if p.is_dir()}
    output = _run_legendary(
        ["download", app_name, "--base-path", str(folder), "--skip-sdl", "--yes"]
    )
    nuove = [p for p in folder.iterdir() if p.is_dir() and p.name not in before]
    root = nuove[0] if nuove else _guess_pack_root(folder, app_name)
    return {
        "app_name": app_name,
        "destination": str(folder),
        "path": str(root) if root else str(folder),
        "log_tail": output[-1000:],
    }


# ------------------------------------------------- installazione dentro il progetto


def _walk_dirs(root: Path, max_depth: int = 6):
    """Cartelle sotto `root`, in profondità limitata e senza le cartelle di build."""
    stack = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        yield current, depth
        if depth >= max_depth:
            continue
        try:
            children = [p for p in current.iterdir() if p.is_dir()]
        except OSError:  # pragma: no cover - permessi o percorso sparito
            continue
        for child in sorted(children):
            if child.name in FAB_SKIP_DIRS:
                continue
            stack.append((child, depth + 1))


def _dir_stats(path: Path) -> dict:
    files = 0
    size = 0
    uassets = 0
    for item in path.rglob("*"):
        if not item.is_file():
            continue
        files += 1
        if item.suffix.lower() in (".uasset", ".umap"):
            uassets += 1
        try:
            size += item.stat().st_size
        except OSError:  # pragma: no cover
            pass
    return {"files": files, "uassets": uassets, "bytes": size}


def _engine_versions(root: Path) -> list[str]:
    """Versioni di motore dichiarate dalla struttura del pack (es. cartelle "5.4")."""
    trovate = set()
    for current, _ in _walk_dirs(root, max_depth=4):
        if re.fullmatch(r"[45]\.\d{1,2}", current.name):
            trovate.add(current.name)
    return sorted(trovate)


def inspect_pack(path: str | Path) -> dict:
    """Riconosce la struttura di un pack Fab già estratto sul disco.

    I pack del vault non hanno un layout unico: il contenuto può stare in
    `Content/`, sotto `data/`, dentro una cartella con la versione del motore,
    oppure essere un plugin con il suo `.uplugin`. Qui si guarda com'è fatto
    invece di indovinarlo.
    """
    root = Path(path).expanduser()
    if not root.exists():
        raise AssetError("Percorso non trovato: %s" % root)
    if root.is_file():
        raise AssetError("inspect_pack vuole una cartella, non un file: %s" % root)

    plugin_dirs: list[Path] = []
    content_dirs: list[tuple[Path, int]] = []
    for current, depth in _walk_dirs(root):
        if any(current.glob("*.uplugin")):
            plugin_dirs.append(current)
        elif current.name == "Content":
            content_dirs.append((current, depth))

    def dentro_un_plugin(candidate: Path) -> bool:
        return any(candidate == p or p in candidate.parents for p in plugin_dirs)

    # Un Content annidato dentro un altro Content verrebbe copiato due volte.
    contenuti = []
    for current, depth in sorted(content_dirs, key=lambda item: item[1]):
        if dentro_un_plugin(current):
            continue
        if any(Path(c["path"]) in current.parents for c in contenuti):
            continue
        if not any(current.iterdir()):
            continue
        contenuti.append({"path": str(current), "depth": depth, **_dir_stats(current)})

    plugins = []
    for current in sorted(plugin_dirs):
        uplugin = sorted(current.glob("*.uplugin"))[0]
        plugins.append(
            {
                "name": uplugin.stem,
                "path": str(current),
                "uplugin": str(uplugin),
                "has_source": (current / "Source").is_dir(),
                **_dir_stats(current),
            }
        )

    return {
        "root": str(root),
        "plugins": plugins,
        "content_dirs": contenuti,
        "engine_versions": _engine_versions(root),
    }


def _safe_relative(name: str) -> Path:
    """Sottocartella di /Game sicura: niente `..`, niente caratteri che Unreal rifiuta."""
    parti = []
    for pezzo in re.split(r"[\\/]+", name.strip()):
        pulito = re.sub(r"[^0-9A-Za-z_]+", "_", pezzo).strip("_")
        if not pulito or pulito in (".", ".."):
            continue
        if pulito[0].isdigit():
            pulito = "P_" + pulito
        parti.append(pulito[:64])
    if not parti:
        raise AssetError("Nome di cartella non utilizzabile in Unreal: %r" % name)
    return Path(*parti)


def resolve_project_dirs(uproject: str | Path) -> dict:
    """Cartelle Content/Plugins di un progetto, dato il .uproject o la sua cartella."""
    path = Path(uproject).expanduser()
    if path.is_dir():
        candidati = sorted(path.glob("*.uproject"))
        if not candidati:
            raise AssetError("Nessun .uproject dentro %s" % path)
        path = candidati[0]
    if not path.exists():
        raise AssetError("File .uproject non trovato: %s" % path)
    return {
        "uproject": str(path),
        "root": path.parent,
        "content": path.parent / "Content",
        "plugins": path.parent / "Plugins",
    }


def _prepara_sorgente(source: str) -> tuple[Path, str, dict | None]:
    """Risolve `source` (app_name del vault, cartella o archivio) in una cartella."""
    candidate = Path(source).expanduser()
    if candidate.exists():
        if candidate.is_file():
            estratto = extract_archive(str(candidate), str(library_dir() / "fab" / candidate.stem))
            return Path(estratto["destination"]), candidate.stem, None
        return candidate, candidate.name, None
    scaricato = fab_download(source)
    return Path(scaricato["path"]), source, scaricato


def fab_install(
    source: str,
    uproject: str,
    subfolder: str | None = None,
    mode: str = "auto",
    overwrite: bool = False,
) -> dict:
    """Installa un pack Fab dentro un progetto Unreal.

    Args:
        source: app_name del vault Epic (scaricato al volo), oppure percorso di
            una cartella già estratta o di un archivio zip.
        uproject: file .uproject del progetto di destinazione (o la sua cartella).
        subfolder: sottocartella di Content dove finisce il pack; default il nome
            del pack. Accetta percorsi annidati ("Fab/SoulCity").
        mode: "auto" installa sia i plugin sia il contenuto trovati, "content" e
            "plugin" forzano uno dei due.
        overwrite: sovrascrive una destinazione già esistente.
    """
    if mode not in ("auto", "content", "plugin"):
        raise AssetError("mode deve essere 'auto', 'content' o 'plugin'.")

    project = resolve_project_dirs(uproject)
    pack_root, pack_name, downloaded = _prepara_sorgente(source)
    layout = inspect_pack(pack_root)

    avvisi: list[str] = []
    contenuti_installati: list[dict] = []
    plugin_installati: list[dict] = []

    if mode in ("auto", "content"):
        for indice, voce in enumerate(layout["content_dirs"]):
            relativo = _safe_relative(subfolder or pack_name)
            if indice:
                relativo = relativo.with_name("%s_%d" % (relativo.name, indice + 1))
            target = project["content"] / relativo
            if target.exists() and not overwrite:
                raise AssetError(
                    "%s esiste già: passa overwrite=True per sovrascriverlo, oppure "
                    "un `subfolder` diverso." % target
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(voce["path"], target, dirs_exist_ok=True)
            contenuti_installati.append(
                {
                    "source": voce["path"],
                    "destination": str(target),
                    "unreal_path": "/Game/" + relativo.as_posix(),
                    **_dir_stats(target),
                }
            )

    if mode in ("auto", "plugin"):
        for voce in layout["plugins"]:
            target = project["plugins"] / voce["name"]
            if target.exists() and not overwrite:
                raise AssetError(
                    "Il plugin %s è già installato in %s: passa overwrite=True per "
                    "sostituirlo." % (voce["name"], target)
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(
                voce["path"],
                target,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("Binaries", "Intermediate"),
            )
            plugin_installati.append(
                {"name": voce["name"], "destination": str(target), "has_source": voce["has_source"]}
            )
            if voce["has_source"]:
                avvisi.append(
                    "Il plugin %s contiene codice C++: va compilato (ue_build_start a "
                    "editor chiuso) prima che l'editor possa caricarlo." % voce["name"]
                )

    if not contenuti_installati and not plugin_installati:
        raise AssetError(
            "In %s non ho trovato né una cartella Content né un .uplugin da installare. "
            "Contenuto della radice: %s"
            % (pack_root, ", ".join(sorted(p.name for p in pack_root.iterdir())[:15]) or "vuota")
        )

    if plugin_installati:
        avvisi.append(
            "L'editor va riavviato (ue_editor_close + ue_editor_open) perché carichi i "
            "plugin appena installati."
        )
    if layout["engine_versions"]:
        avvisi.append(
            "Il pack dichiara la versione motore %s: se non è quella del progetto, "
            "Unreal converte gli asset al primo caricamento e può segnalare warning."
            % ", ".join(layout["engine_versions"])
        )

    return {
        "source": source,
        "pack": str(pack_root),
        "uproject": project["uproject"],
        "download": downloaded,
        "content_installed": contenuti_installati,
        "plugins_installed": plugin_installati,
        "unreal_paths": [c["unreal_path"] for c in contenuti_installati],
        "warnings": avvisi,
    }

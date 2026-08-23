"""Scarica e vendorizza l'helper `App` di @modelcontextprotocol/ext-apps.

Il pannello viewport gira dentro un iframe con CSP deny-by-default: prendere
l'helper da un CDN significa aggiungere un'origine esterna alla CSP e avere una
vista che non si carica senza rete. Vendorizzarlo lo rende un file come gli
altri, e la pagina resta interamente locale.

    python scripts/vendor_ext_apps.py [versione]

Rigenera src/unreal_mcp/vendor/ext_apps_app.js. Va rilanciato solo per
aggiornare la versione dell'helper: il file prodotto è versionato nel repo, chi
installa il pacchetto non deve avere Node né rete.

L'unica trasformazione applicata è sull'ultima riga: il bundle è un modulo ESM
che espone `App` come export, ma la pagina lo incorpora inline invece di
importarlo, e da un `<script type="module">` inline nessuno può leggere quegli
export. La riga `export{...}` diventa quindi un'assegnazione su globalThis.
"""

from __future__ import annotations

import io
import re
import sys
import tarfile
import urllib.request
from pathlib import Path

PACCHETTO = "@modelcontextprotocol/ext-apps"
DENTRO_AL_TAR = "package/dist/src/app-with-deps.js"
DESTINAZIONE = Path(__file__).resolve().parent.parent / "src/unreal_mcp/vendor/ext_apps_app.js"

# Il bundle è minificato, quindi il nome locale di App cambia a ogni build:
# va letto dalla mappa di export, non indovinato.
EXPORT_APP = re.compile(rb"export\{(?:[^{}]*,)?(\w+) as App\};?\s*$")


def scarica(versione: str) -> tuple[bytes, str]:
    meta_url = "https://registry.npmjs.org/%s/%s" % (PACCHETTO, versione)
    with urllib.request.urlopen(meta_url) as risposta:  # noqa: S310
        import json

        meta = json.load(risposta)
    url = meta["dist"]["tarball"]
    risolta = meta["version"]

    with urllib.request.urlopen(url) as risposta:  # noqa: S310
        archivio = risposta.read()

    with tarfile.open(fileobj=io.BytesIO(archivio), mode="r:gz") as tar:
        membro = tar.extractfile(DENTRO_AL_TAR)
        if membro is None:
            raise SystemExit("%s non è nel pacchetto %s" % (DENTRO_AL_TAR, risolta))
        return membro.read(), risolta


def trasforma(sorgente: bytes, versione: str) -> bytes:
    trovato = EXPORT_APP.search(sorgente)
    if not trovato:
        raise SystemExit(
            "Non trovo l'export di App in coda al bundle: il formato è cambiato, "
            "controlla a mano prima di aggiornare."
        )

    locale = trovato.group(1).decode()
    intestazione = (
        "// Generato da scripts/vendor_ext_apps.py — non modificare a mano.\n"
        "// %s@%s, Apache-2.0, https://github.com/modelcontextprotocol/ext-apps\n"
        % (PACCHETTO, versione)
    ).encode()
    coda = ("\nglobalThis.__EXT_APPS__={App:%s};\n" % locale).encode()
    return intestazione + sorgente[: trovato.start()] + coda


def main() -> int:
    versione = sys.argv[1] if len(sys.argv) > 1 else "latest"
    sorgente, risolta = scarica(versione)
    DESTINAZIONE.parent.mkdir(parents=True, exist_ok=True)
    DESTINAZIONE.write_bytes(trasforma(sorgente, risolta))
    print(
        "Scritto %s (%s@%s, %.0f KB)"
        % (DESTINAZIONE.name, PACCHETTO, risolta, DESTINAZIONE.stat().st_size / 1024)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

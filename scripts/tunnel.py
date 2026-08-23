"""Espone il server in HTTP e ci apre sopra un tunnel cloudflared.

**Serve solo per claude.ai sul web.** Il pannello viewport non ha bisogno di
tutto questo: su Claude Desktop, VS Code e gli altri host che lanciano il
server in locale funziona già in stdio, con la configurazione che hai. Le MCP
App sono indipendenti dal trasporto — sono un tool con `_meta.ui.resourceUri`
più una risorsa `ui://`, e viaggiano identiche sui due canali.

Il motivo per cui questo script esiste è un altro: claude.ai gira sui server di
Anthropic, che al tuo computer non possono arrivare. Un connettore remoto è
l'unico modo di farli incontrare, e un connettore remoto vuole un URL.

Quindi: se usi un client locale, ignora questo file. Se vuoi il pannello dentro
claude.ai nel browser, ci sono due modi, scelti in base a `--hostname`.

**Tunnel usa-e-getta** (default, niente da configurare):

    python scripts/tunnel.py

Apre un `*.trycloudflare.com` casuale. Ogni riavvio cambia URL, quindi il
connettore su claude.ai va riaggiornato ogni volta.

**Tunnel nominato** (URL stabile, si incolla una volta sola):

    python scripts/tunnel.py --hostname unreal.tuodominio.com

Richiede un dominio con il DNS gestito da Cloudflare. Al primo avvio apre il
browser per l'autorizzazione, crea il tunnel e il record DNS; dalle volte dopo
riusa tutto e riparte in silenzio sullo stesso indirizzo.

ATTENZIONE. Il tunnel pubblica su Internet un server che espone
`ue_exec_python`: chi raggiunge l'URL esegue codice arbitrario dentro il tuo
editor. Il tunnel usa-e-getta non ha nessuna autenticazione davanti, quindi
tienilo aperto il tempo della prova. Su un tunnel nominato metti Cloudflare
Access davanti all'hostname prima di lasciarlo su.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from contextlib import closing, suppress
from pathlib import Path
from socket import AF_INET, SOCK_STREAM, socket

# cloudflared scrive l'URL del tunnel usa-e-getta su stderr, fra le sue righe
# di log: non c'è modo di saperlo prima, va pescato da lì.
URL_USA_E_GETTA = re.compile(rb"https://[a-z0-9-]+\.trycloudflare\.com")

CERTIFICATO = Path.home() / ".cloudflared" / "cert.pem"


# --------------------------------------------------------------------- utilità


def comando_cloudflared() -> list[str] | None:
    """Come lanciare cloudflared su questa macchina, o None se non si può.

    Il binario è la strada buona, ma richiede un'installazione; npx lo scarica
    al volo e per una prova va benissimo. Provarli in quest'ordine evita di
    fermare l'utente su un `winget install` che magari non vuole fare.
    """
    binario = shutil.which("cloudflared")
    if binario:
        return [binario]

    # Su Windows npx è npx.cmd: which lo trova solo col nome completo.
    npx = shutil.which("npx") or shutil.which("npx.cmd")
    if npx:
        print("cloudflared non installato: uso npx (il primo avvio è più lento).")
        return [npx, "--yes", "cloudflared"]

    return None


def porta_libera(porta: int) -> bool:
    with closing(socket(AF_INET, SOCK_STREAM)) as s:
        return s.connect_ex(("127.0.0.1", porta)) != 0


def attendi_server(porta: int, secondi: float = 20.0) -> bool:
    scadenza = time.monotonic() + secondi
    while time.monotonic() < scadenza:
        if not porta_libera(porta):
            return True
        time.sleep(0.25)
    return False


def esegui(argomenti: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(argomenti, check=False, **kwargs)  # noqa: S603


# ------------------------------------------------------- preparazione nominato


def assicura_login(cloudflared: list[str]) -> bool:
    """Autorizza cloudflared sul tuo account, se non l'ha già fatto.

    `tunnel login` apre il browser e aspetta che scegli la zona: è interattivo
    per forza, ma succede una volta sola e il certificato resta sul disco.
    """
    if CERTIFICATO.exists():
        return True

    print("Prima autorizzazione: si apre il browser, scegli il dominio da usare.")
    esito = esegui([*cloudflared, "tunnel", "login"])
    if esito.returncode != 0 or not CERTIFICATO.exists():
        print("Autorizzazione non completata.", file=sys.stderr)
        return False
    return True


def assicura_tunnel(cloudflared: list[str], nome: str) -> bool:
    """Crea il tunnel se non esiste già.

    Va interrogata la lista invece di creare e ignorare l'errore: `tunnel
    create` fallisce anche per motivi veri, e distinguerli dal "esiste già"
    guardando il testo dell'errore è più fragile che chiedere prima.
    """
    esito = esegui(
        [*cloudflared, "tunnel", "list", "--output", "json"],
        capture_output=True,
        text=True,
    )
    if esito.returncode == 0:
        with suppress(json.JSONDecodeError, TypeError):
            if any(t.get("name") == nome for t in json.loads(esito.stdout or "[]")):
                print("Tunnel '%s' già presente." % nome)
                return True

    print("Creo il tunnel '%s'." % nome)
    return esegui([*cloudflared, "tunnel", "create", nome]).returncode == 0


def assicura_dns(cloudflared: list[str], nome: str, hostname: str) -> bool:
    """Punta l'hostname al tunnel.

    Rilanciarlo su un record già a posto è innocuo, quindi si esegue sempre
    invece di controllare prima: se il record esiste ma guarda altrove serve
    comunque riscriverlo.
    """
    esito = esegui(
        [*cloudflared, "tunnel", "route", "dns", "--overwrite-dns", nome, hostname],
        capture_output=True,
        text=True,
    )
    if esito.returncode != 0:
        print("Non sono riuscito a creare il record DNS:", file=sys.stderr)
        print((esito.stderr or esito.stdout).strip(), file=sys.stderr)
        print(
            "\nControlla che %s stia su una zona del tuo account Cloudflare."
            % hostname,
            file=sys.stderr,
        )
        return False
    return True


# ------------------------------------------------------------------ esecuzione


def avvia_server(porta: int, hostname: str | None) -> subprocess.Popen:
    ambiente = {**os.environ, "UE_MCP_HTTP_PORT": str(porta)}
    # Con un hostname stabile la lista di Host ammessi si può stringere: è il
    # vantaggio meno ovvio del tunnel nominato, e vale la pena prenderlo.
    if hostname and "UE_MCP_ALLOWED_HOSTS" not in ambiente:
        ambiente["UE_MCP_ALLOWED_HOSTS"] = hostname

    print("Avvio il server MCP su http://127.0.0.1:%d/mcp" % porta)
    return subprocess.Popen(
        [sys.executable, "-m", "unreal_mcp.server", "--http"], env=ambiente
    )


def annuncia(url: str, stabile: bool) -> None:
    print("\n" + "=" * 62)
    print("Connettore personalizzato da incollare su claude.ai:")
    print("  %s/mcp" % url)
    if stabile:
        print("\nQuesto indirizzo non cambia: lo incolli una volta sola.")
        print("Metti Cloudflare Access davanti prima di lasciarlo su stabilmente.")
    else:
        print("\nURL temporaneo: al prossimo avvio cambia e va reincollato.")
        print("Per averne uno fisso: --hostname unreal.tuodominio.com")
    print("=" * 62)
    print("Ctrl-C per chiudere tunnel e server.\n")


def avvia_nominato(cloudflared: list[str], porta: int, nome: str, hostname: str):
    """Tunnel nominato: l'URL si sa già, quindi non c'è niente da pescare.

    Senza pipe da leggere il log di cloudflared finisce dritto sul terminale,
    che è anche il posto giusto per vederlo se qualcosa non va.
    """
    annuncia("https://%s" % hostname, stabile=True)
    return subprocess.Popen(  # noqa: S603
        [*cloudflared, "tunnel", "run", "--url", "http://localhost:%d" % porta, nome]
    )


def avvia_usa_e_getta(cloudflared: list[str], porta: int):
    """Tunnel anonimo: l'URL arriva su stderr e va aspettato."""
    tunnel = subprocess.Popen(  # noqa: S603
        [*cloudflared, "tunnel", "--url", "http://localhost:%d" % porta],
        stderr=subprocess.PIPE,
    )
    assert tunnel.stderr is not None

    for riga in tunnel.stderr:
        trovato = URL_USA_E_GETTA.search(riga)
        if trovato:
            annuncia(trovato.group().decode(), stabile=False)
            break
    else:
        print("cloudflared è uscito senza dare un URL.", file=sys.stderr)
        return None

    # La pipe va comunque svuotata: se si riempie cloudflared si blocca sulla
    # scrittura, e chiuderla di netto gli manda EPIPE e lo fa morire un istante
    # dopo aver stampato l'URL. Quindi si continua a leggere e si butta via, su
    # un thread che non tiene in piedi il processo.
    threading.Thread(
        target=lambda: [None for _ in tunnel.stderr], daemon=True
    ).start()
    return tunnel


# ------------------------------------------------------------------------ main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--hostname",
        default=os.environ.get("UE_MCP_TUNNEL_HOSTNAME"),
        help="hostname stabile su una zona Cloudflare, es. unreal.tuodominio.com. "
        "Senza, apre un tunnel temporaneo con URL casuale.",
    )
    parser.add_argument(
        "--name",
        default=os.environ.get("UE_MCP_TUNNEL_NAME", "unreal-mcp"),
        help="nome del tunnel nominato (default: unreal-mcp)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("UE_MCP_HTTP_PORT", "8000")),
        help="porta locale del server HTTP (default: 8000)",
    )
    args = parser.parse_args()

    cloudflared = comando_cloudflared()
    if cloudflared is None:
        print(
            "Non trovo né cloudflared né npx. Installa uno dei due:\n"
            "  winget install --id Cloudflare.cloudflared   (Windows)\n"
            "  brew install cloudflared                     (macOS)\n"
            "  winget install --id OpenJS.NodeJS.LTS        (Node, per la via npx)\n"
            "Dopo l'installazione apri un terminale nuovo: il PATH non si "
            "aggiorna in quello già aperto.",
            file=sys.stderr,
        )
        return 1

    if not porta_libera(args.port):
        print(
            "La porta %d è già occupata: chiudi l'altro processo oppure passa "
            "--port." % args.port,
            file=sys.stderr,
        )
        return 1

    # Login, tunnel e DNS prima di avviare il server: sono i passi che possono
    # chiedere qualcosa all'utente, e falliscono meglio da soli.
    if args.hostname and not (
        assicura_login(cloudflared)
        and assicura_tunnel(cloudflared, args.name)
        and assicura_dns(cloudflared, args.name, args.hostname)
    ):
        return 1

    server = avvia_server(args.port, args.hostname)
    tunnel = None
    try:
        if not attendi_server(args.port):
            print("Il server non ha aperto la porta: vedi l'errore qui sopra.", file=sys.stderr)
            return 1

        tunnel = (
            avvia_nominato(cloudflared, args.port, args.name, args.hostname)
            if args.hostname
            else avvia_usa_e_getta(cloudflared, args.port)
        )
        if tunnel is None:
            return 1
        tunnel.wait()
    except KeyboardInterrupt:
        print("\nChiudo.")
    finally:
        for processo in (tunnel, server):
            if processo and processo.poll() is None:
                processo.terminate()
                with suppress(subprocess.TimeoutExpired):
                    processo.wait(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

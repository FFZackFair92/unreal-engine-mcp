"""Motore di flow: una lista di chiamate a tool descritta in YAML (o JSON).

Perché esiste. Costruire una scena è quasi sempre la stessa sequenza di dieci
o venti chiamate — crea il materiale, crea il blueprint, spawna, allinea,
salva. Farla passare dal modello una chiamata alla volta costa un turno per
passo, e il turno costa contesto: al decimo passo il modello ha in memoria
nove risposte JSON che non gli servono più. Un flow la descrive una volta, la
esegue tutta in una chiamata e restituisce solo il riassunto.

Non è un linguaggio di programmazione e non vuole diventarlo. Ci sono i passi,
le variabili e una condizione; non ci sono cicli, funzioni né espressioni
aritmetiche. Se un flow ha bisogno di quelle cose, il posto giusto per la
logica è il modello che lo genera, non il file YAML.

Sul parser: PyYAML è una dipendenza *opzionale*. Il repo tiene le dipendenze
al minimo per scelta (vedi la nota su `mcp>=1.2.0,<2` in pyproject.toml), e un
flow scritto in JSON è comunque un flow valido — JSON è un sottoinsieme di
YAML. Senza PyYAML installato, i flow JSON funzionano e quelli YAML danno un
errore che dice come installarlo, invece di un traceback dentro il parser.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# `${nome}` e `${nome.chiave.0}`: il riferimento a un valore salvato da un
# passo precedente. Deliberatamente stretto — niente espressioni, niente
# chiamate: un flow non deve poter eseguire codice arbitrario.
_RIFERIMENTO = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_-]+)*)\}")


class FlowError(RuntimeError):
    """Un flow malformato, o un passo che è fallito."""


class SegnapostoDryRun(dict):
    """Il "risultato" di un passo non eseguito, in dry run.

    Serve a una cosa sola: far passare i `${riferimenti}` dei passi
    successivi. Senza, un flow corretto che usa il risultato di un passo
    precedente — cioè quasi ogni flow — risulterebbe rotto in dry run, e il
    dry run smetterebbe di essere utile proprio nel caso in cui serve.

    Risponde a qualunque chiave restituendo se stesso, così anche i percorsi
    lunghi (`${cubo.a.b.c}`) si risolvono. Resta un dict, quindi finisce nel
    riassunto JSON come `{"_dry_run": true}` e si vede che non è un valore
    vero.
    """

    def __init__(self):
        super().__init__(_dry_run=True)

    def __contains__(self, chiave):
        return True

    def __getitem__(self, chiave):
        return self


def carica_flow(sorgente: str) -> dict:
    """Interpreta un flow da testo YAML/JSON, o dal path di un file.

    Distinguere fra "questo è un flow" e "questo è il path di un flow" senza
    chiedere all'utente di dirlo: un flow ha per forza più di una riga o una
    graffa, un path no.
    """
    testo = sorgente
    candidato = sorgente.strip()
    if "\n" not in candidato and not candidato.startswith(("{", "[")):
        percorso = Path(candidato)
        if not percorso.is_file():
            raise FlowError(
                f"'{candidato}' non è un file e non sembra un flow: passa il "
                "contenuto YAML/JSON, oppure il path di un file esistente."
            )
        testo = percorso.read_text(encoding="utf-8")

    dati = _parse(testo)
    if isinstance(dati, list):
        dati = {"steps": dati}
    if not isinstance(dati, dict):
        raise FlowError(
            f"Un flow è un oggetto con una chiave 'steps' (o direttamente una "
            f"lista di passi), non un {type(dati).__name__}."
        )
    return dati


def _parse(testo: str) -> Any:
    """YAML se PyYAML c'è, JSON altrimenti — con un errore che lo spiega."""
    try:
        import yaml
    except ImportError:
        try:
            return json.loads(testo)
        except json.JSONDecodeError as exc:
            raise FlowError(
                "Per i flow in YAML serve PyYAML (`pip install pyyaml`); senza, "
                f"si possono usare solo flow in JSON, e questo non lo è: {exc}"
            ) from exc

    try:
        return yaml.safe_load(testo)
    except yaml.YAMLError as exc:
        raise FlowError(f"YAML non valido: {exc}") from exc


def normalizza_passi(flow: dict) -> list[dict]:
    """Valida la forma dei passi e restituisce la lista pulita."""
    passi = flow.get("steps")
    if not isinstance(passi, list) or not passi:
        raise FlowError("Il flow non ha nessun passo: serve una lista 'steps' non vuota.")

    puliti = []
    for indice, passo in enumerate(passi):
        if not isinstance(passo, dict):
            raise FlowError(
                f"Passo {indice}: dev'essere un oggetto con una chiave 'tool', "
                f"non un {type(passo).__name__}."
            )
        nome = passo.get("tool")
        if not isinstance(nome, str) or not nome:
            raise FlowError(f"Passo {indice}: manca il nome del tool ('tool').")

        argomenti = passo.get("args", {})
        if argomenti is None:
            argomenti = {}
        if not isinstance(argomenti, dict):
            raise FlowError(
                f"Passo {indice} ({nome}): 'args' dev'essere un oggetto, non un "
                f"{type(argomenti).__name__}."
            )

        puliti.append(
            {
                "tool": nome,
                "args": argomenti,
                "save": passo.get("save"),
                "when": passo.get("when"),
                "continue_on_error": bool(passo.get("continue_on_error", False)),
                "name": passo.get("name") or nome,
            }
        )
    return puliti


def _percorso(contesto: dict, percorso: str) -> Any:
    """Segue `nome.chiave.0` dentro le variabili, con un errore leggibile."""
    parti = percorso.split(".")
    corrente: Any = contesto
    visto: list[str] = []
    for parte in parti:
        visto.append(parte)
        if isinstance(corrente, dict):
            if parte not in corrente:
                disponibili = ", ".join(sorted(str(k) for k in corrente)) or "niente"
                raise FlowError(
                    f"${{{percorso}}}: '{'.'.join(visto)}' non esiste. "
                    f"A quel livello ci sono: {disponibili}."
                )
            corrente = corrente[parte]
        elif isinstance(corrente, (list, tuple)):
            if not parte.lstrip("-").isdigit():
                raise FlowError(
                    f"${{{percorso}}}: '{'.'.join(visto[:-1])}' è una lista, "
                    f"quindi '{parte}' dovrebbe essere un indice numerico."
                )
            indice = int(parte)
            if not -len(corrente) <= indice < len(corrente):
                raise FlowError(
                    f"${{{percorso}}}: indice {indice} fuori range "
                    f"(la lista ha {len(corrente)} elementi)."
                )
            corrente = corrente[indice]
        else:
            raise FlowError(
                f"${{{percorso}}}: '{'.'.join(visto[:-1])}' è un valore "
                f"{type(corrente).__name__}, non si può scendere più giù."
            )
    return corrente


def espandi(valore: Any, contesto: dict) -> Any:
    """Sostituisce i `${riferimenti}` dentro args, ricorsivamente.

    Una stringa che è *solo* un riferimento restituisce il valore con il suo
    tipo (un numero resta un numero, un dict resta un dict); un riferimento
    dentro a una frase più lunga viene interpolato come testo. Senza questa
    distinzione `location: "${cubo.location}"` diventerebbe la stringa
    "{'x': 0.0, ...}" e il tool la rifiuterebbe.
    """
    if isinstance(valore, str):
        intero = _RIFERIMENTO.fullmatch(valore.strip())
        if intero:
            return _percorso(contesto, intero.group(1))
        return _RIFERIMENTO.sub(lambda m: str(_percorso(contesto, m.group(1))), valore)
    if isinstance(valore, dict):
        return {chiave: espandi(v, contesto) for chiave, v in valore.items()}
    if isinstance(valore, list):
        return [espandi(v, contesto) for v in valore]
    return valore


def condizione_vera(when: Any, contesto: dict) -> bool:
    """Valuta il `when` di un passo.

    Tre forme, tutte senza `eval`: un booleano, un riferimento (vero se il
    valore è truthy), o `{"equals": [a, b]}` / `{"not_equals": [a, b]}` /
    `{"exists": "nome.chiave"}`.
    """
    if when is None:
        return True
    if isinstance(when, bool):
        return when
    if isinstance(when, str):
        return bool(espandi(when, contesto))
    if isinstance(when, dict):
        if "exists" in when:
            try:
                _percorso(contesto, str(when["exists"]))
            except FlowError:
                return False
            return True
        for chiave, atteso in (("equals", True), ("not_equals", False)):
            if chiave in when:
                coppia = when[chiave]
                if not isinstance(coppia, (list, tuple)) or len(coppia) != 2:
                    raise FlowError(f"'{chiave}' vuole esattamente due valori da confrontare.")
                sinistra, destra = (espandi(v, contesto) for v in coppia)
                return (sinistra == destra) is atteso
    raise FlowError(
        "'when' può essere un booleano, un ${riferimento}, oppure "
        "{equals: [a, b]}, {not_equals: [a, b]}, {exists: nome.chiave}."
    )


def riepiloga(valore: Any, limite: int = 400) -> Any:
    """Accorcia il risultato di un passo.

    Un flow di venti passi che restituisce venti risposte intere annulla il
    motivo per cui esiste. Le risposte restano intere nelle variabili, dove
    servono ai passi successivi: qui si accorcia solo quello che torna al
    modello.
    """
    testo = json.dumps(valore, ensure_ascii=False, default=str)
    if len(testo) <= limite:
        return valore
    return {"_truncated": True, "preview": testo[:limite] + "…", "size": len(testo)}

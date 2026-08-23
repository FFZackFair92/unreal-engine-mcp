"""Pannello viewport come MCP App (estensione apps, 2026-01-26).

Il meccanismo è tutto nel protocollo, non nell'SDK: un tool dichiara
`_meta.ui.resourceUri` puntando a una risorsa `ui://`, l'host la scarica e la
renderizza in un iframe sandboxed dentro la conversazione. La pagina rientra
verso il server con `tools/call` via postMessage.

Niente toolchain npm qui dentro e niente CDN: l'helper `App` è vendorizzato in
`vendor/ext_apps_app.js` e viene incorporato nella pagina. Così il pannello
funziona senza rete e la CSP non deve aprire nessuna origine esterna — che su
un iframe deny-by-default è la differenza fra una vista che si carica e una
che resta bianca. Per aggiornarlo: `python scripts/vendor_ext_apps.py`.
"""

from __future__ import annotations

from pathlib import Path

# Il valore è quello esportato come RESOURCE_MIME_TYPE da
# @modelcontextprotocol/ext-apps. Non è "text/html+skybridge": quello è il
# mime della vecchia mcp-ui, e un host conforme non lo riconosce come app.
UI_MIME = "text/html;profile=mcp-app"

VIEWPORT_URI = "ui://unreal/viewport.html"

# La CSP è deny-by-default. Qui non serve aprire nessuna origine: lo script è
# incorporato nella pagina e lo screenshot arriva come data: URI dentro il
# risultato del tool. L'unica voce è quella, e serve perché `data:` non è
# implicito in `img-src`.
VIEWPORT_CSP = {
    "connect-src": [],
    "script-src": [],
    "img-src": ["data:"],
}

# Letto una volta all'import: sono ~330 KB che finiscono in ogni lettura della
# risorsa, e rileggerli dal disco a ogni apertura del pannello non darebbe
# niente in cambio.
_APP_JS = (Path(__file__).parent / "vendor" / "ext_apps_app.js").read_text("utf-8")


VIEWPORT_HTML = """<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="utf-8" />
<title>Viewport Unreal</title>
<style>
  :root {
    color-scheme: light dark;
    --bg: #ffffff; --fg: #1a1a18; --muted: #6b6b66;
    --line: rgba(0,0,0,.12); --hover: rgba(0,0,0,.06);
    --sel: rgba(60,120,220,.16); --selfg: #1b4c96;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #1f1f1d; --fg: #ececea; --muted: #9a9a94;
      --line: rgba(255,255,255,.14); --hover: rgba(255,255,255,.07);
      --sel: rgba(120,170,255,.20); --selfg: #a8c8ff;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--fg);
    font: 400 13px/1.5 ui-sans-serif, system-ui, sans-serif;
  }
  .wrap { display: grid; grid-template-columns: minmax(0,1fr) 244px; gap: 14px; padding: 14px; }
  @media (max-width: 640px) { .wrap { grid-template-columns: minmax(0,1fr); } }

  h1 { font: 500 15px/1.4 inherit; margin: 0 0 8px; }
  h2 { font: 500 12px/1.4 inherit; margin: 0 0 8px; color: var(--muted); }

  .shot {
    position: relative; background: var(--hover);
    border: .5px solid var(--line); border-radius: 10px;
    overflow: hidden; aspect-ratio: 16/9;
    display: flex; align-items: center; justify-content: center;
  }
  .shot img { display: block; width: 100%; height: 100%; object-fit: cover; }
  .empty { padding: 1rem; text-align: center; color: var(--muted); font-size: 12px; margin: 0; }

  .pad { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 10px; }
  .sep { width: .5px; height: 20px; background: var(--line); margin: 0 3px; }
  .grow { flex: 1; }

  button, select {
    font: inherit; font-size: 12px; padding: 4px 9px; border-radius: 6px;
    border: .5px solid var(--line); background: transparent; color: inherit;
    cursor: pointer; line-height: 1.5;
  }
  button:hover:not(:disabled), select:hover { background: var(--hover); }
  button:disabled { opacity: .4; cursor: default; }
  button.on { background: var(--sel); color: var(--selfg); border-color: transparent; }
  .icon { min-width: 26px; text-align: center; font-variant-numeric: tabular-nums; }

  input[type=search] {
    font: inherit; font-size: 12px; width: 100%; padding: 5px 8px;
    border-radius: 6px; border: .5px solid var(--line);
    background: transparent; color: inherit; margin-bottom: 8px;
  }
  .list { max-height: 380px; overflow-y: auto; margin: 0 -4px; padding: 0 4px; }
  .gruppo { margin-bottom: 10px; }
  .gruppo > p {
    margin: 0 0 3px; font-size: 11px; color: var(--muted);
    text-transform: uppercase; letter-spacing: .04em;
  }
  .row {
    display: block; width: 100%; text-align: left; border: 0;
    border-radius: 5px; padding: 4px 7px; background: transparent; font-size: 12px;
  }
  .row:hover { background: var(--hover); }
  .row.on { background: var(--sel); color: var(--selfg); }
  .row b { font-weight: 500; }
  .coord {
    margin: 3px 0 0 7px; font-size: 11px; color: var(--muted);
    font-variant-numeric: tabular-nums;
  }
  .meta { color: var(--muted); font-size: 11px; margin: 8px 0 0; }
  .err {
    margin: 0 14px; padding: 7px 10px; border-radius: 6px;
    border: .5px solid var(--line); color: var(--muted); font-size: 12px;
  }
  [hidden] { display: none !important; }
</style>
</head>
<body>
<p class="err" id="err" hidden></p>
<div class="wrap">
  <div>
    <h1 id="title">Viewport</h1>
    <div class="shot" id="shot"><p class="empty">Nessuna cattura</p></div>

    <div class="pad">
      <button class="icon" data-yaw="-30" title="Ruota a sinistra">&#8592;</button>
      <button class="icon" data-yaw="30" title="Ruota a destra">&#8594;</button>
      <button class="icon" data-pitch="15" title="Alza lo sguardo">&#8593;</button>
      <button class="icon" data-pitch="-15" title="Abbassa lo sguardo">&#8595;</button>
      <span class="sep"></span>
      <button class="icon" data-dolly="300" title="Avvicina">+</button>
      <button class="icon" data-dolly="-300" title="Allontana">&#8722;</button>
      <span class="sep"></span>
      <select id="view" title="Vista preimpostata">
        <option value="">Vista…</option>
        <option value="persp">Prospettica</option>
        <option value="top">Dall'alto</option>
        <option value="front">Frontale</option>
        <option value="back">Posteriore</option>
        <option value="left">Sinistra</option>
        <option value="right">Destra</option>
      </select>
      <span class="grow"></span>
      <button id="auto" title="Ricattura da sola ogni 5 secondi">Auto</button>
      <button id="refresh">Aggiorna</button>
    </div>
    <p class="meta" id="meta"></p>
  </div>

  <div>
    <h2 id="count">Attori</h2>
    <input type="search" id="filter" placeholder="Filtra per nome o classe" />
    <div class="list" id="list"></div>
  </div>
</div>
<script type="module">
__APP_BUNDLE__
const { App } = globalThis.__EXT_APPS__;

const $ = (id) => document.getElementById(id);
let attori = [];
let selezionato = null;
let timerAuto = null;

const mostraErrore = (testo) => {
  $("err").textContent = testo;
  $("err").hidden = !testo;
};

// Il server dichiara un outputSchema, quindi structuredContent c'è sempre;
// il fallback sul testo copre gli host che spingono solo `content`, dove
// altrimenti il pannello si caricherebbe vuoto senza dire niente.
const estrai = (r) => {
  if (!r) return null;
  if (r.structuredContent) return r.structuredContent;
  const testo = r.content?.find((c) => c.type === "text")?.text;
  if (!testo) return null;
  try { return JSON.parse(testo); } catch { return null; }
};

const app = new App({ name: "Unreal viewport", version: "1.0.0" });

// ------------------------------------------------------------------ outliner

const arrotonda = (n) => Math.round(Number(n) || 0);

// Raggruppare per classe è ciò che rende leggibile un livello vero: a otto
// attori la lista piatta va bene, a trecento no, e il costo è lo stesso.
const disegnaLista = () => {
  const q = $("filter").value.trim().toLowerCase();
  const visibili = q
    ? attori.filter((a) => (a.label + a.name + a.class).toLowerCase().includes(q))
    : attori;

  $("count").textContent = `Attori (${visibili.length}/${attori.length})`;

  const perClasse = new Map();
  for (const a of visibili) {
    if (!perClasse.has(a.class)) perClasse.set(a.class, []);
    perClasse.get(a.class).push(a);
  }

  const gruppi = [...perClasse.entries()].sort((x, y) => x[0].localeCompare(y[0]));
  $("list").replaceChildren(...gruppi.map(([classe, elenco]) => {
    const box = document.createElement("div");
    box.className = "gruppo";
    const testa = document.createElement("p");
    testa.textContent = elenco.length > 1 ? `${classe} · ${elenco.length}` : classe;
    box.append(testa);

    for (const a of elenco) {
      const nome = a.label || a.name;
      const b = document.createElement("button");
      b.className = "row" + (nome === selezionato ? " on" : "");
      b.title = "Inquadra questo attore";
      const forte = document.createElement("b");
      forte.textContent = nome;
      b.append(forte);
      b.onclick = () => inquadra(nome);
      box.append(b);

      // Le coordinate solo sul selezionato: averle su ogni riga trasforma
      // l'outliner in un muro di numeri che non si legge più.
      if (nome === selezionato) {
        const c = document.createElement("p");
        c.className = "coord";
        const l = a.location || {};
        c.textContent = `x ${arrotonda(l.x)}  y ${arrotonda(l.y)}  z ${arrotonda(l.z)}`;
        box.append(c);
      }
    }
    return box;
  }));
};

// ------------------------------------------------------------------- cattura

const disegnaCattura = (dati) => {
  if (dati && dati.screenshot) {
    const img = document.createElement("img");
    img.src = dati.screenshot;
    img.alt = "Cattura della viewport dell'editor Unreal";
    $("shot").replaceChildren(img);
    return;
  }
  const p = document.createElement("p");
  p.className = "empty";
  p.textContent = (dati && (dati.screenshot_note || dati.error)) || "Nessuna cattura";
  $("shot").replaceChildren(p);
};

// La cattura è un tool a parte, e la chiede la vista invece di riceverla nel
// risultato del pannello: un PNG in base64 sono ~200.000 token, e dentro il
// risultato del tool finirebbero nel contesto del modello a ogni apertura.
// Chiesta da qui, l'immagine va dall'host all'iframe e basta.
const aggiornaCattura = async ({ silenzioso = false } = {}) => {
  if (!silenzioso && !$("shot").querySelector("img")) {
    $("shot").replaceChildren(Object.assign(document.createElement("p"), {
      className: "empty", textContent: "Catturo…",
    }));
  }
  try {
    const r = await app.callServerTool({ name: "ue_viewport_frame", arguments: {} });
    disegnaCattura(estrai(r));
  } catch (e) {
    disegnaCattura({ error: String(e?.message || e) });
  }
};

// -------------------------------------------------------------------- stato

const disegna = (dati) => {
  if (!dati) return;
  if (dati.error) { mostraErrore(dati.error); return; }
  mostraErrore("");

  const s = dati.status || {};
  $("title").textContent = s.current_level ? `Viewport — ${s.current_level}` : "Viewport";
  // Solo la versione breve del motore: la stringa completa è un numero di
  // build lungo mezza riga che nessuno legge.
  const motore = String(s.engine_version || "").split("-")[0];
  $("meta").textContent = [
    motore && `Unreal ${motore}`,
    s.actor_count != null && `${s.actor_count} attori`,
    s.transport,
  ].filter(Boolean).join(" · ");

  if (dati.focused) selezionato = dati.focused;
  attori = Array.isArray(dati.actors) ? dati.actors : [];
  disegnaLista();
};

// ------------------------------------------------------------------- comandi

const occupato = (attivo, etichetta) => {
  const b = $("refresh");
  b.disabled = attivo;
  b.textContent = attivo ? etichetta : "Aggiorna";
};

const chiama = async (args, etichetta) => {
  occupato(true, etichetta);
  try {
    const r = await app.callServerTool({ name: "ue_viewport_panel", arguments: args });
    const dati = estrai(r);
    disegna(dati);
    if (dati && !dati.error) await aggiornaCattura();
  } catch (e) {
    mostraErrore(String(e?.message || e));
  } finally {
    occupato(false);
  }
};

// I comandi camera non toccano stato né outliner: ricaricarli sarebbe un giro
// sprecato, basta la nuova cattura.
const camera = async (args, etichetta) => {
  occupato(true, etichetta);
  try {
    await app.callServerTool({ name: "ue_viewport_camera", arguments: args });
    await aggiornaCattura();
  } catch (e) {
    mostraErrore(String(e?.message || e));
  } finally {
    occupato(false);
  }
};

const inquadra = (label) => {
  selezionato = label;
  disegnaLista();
  return chiama({ focus: label }, "Inquadro…");
};

for (const b of document.querySelectorAll("[data-yaw],[data-pitch],[data-dolly]")) {
  b.onclick = () => camera({
    yaw: Number(b.dataset.yaw || 0),
    pitch: Number(b.dataset.pitch || 0),
    dolly: Number(b.dataset.dolly || 0),
  }, "Muovo…");
}

$("view").onchange = (e) => {
  const v = e.target.value;
  e.target.value = "";
  if (v) camera({ view: v }, "Allineo…");
};

$("refresh").onclick = () => chiama({}, "Aggiorno…");
$("filter").oninput = disegnaLista;

// L'auto-refresh ricattura e basta: cinque secondi sono abbastanza per non
// martellare l'editor, e la lista degli attori cambia molto più di rado
// dell'immagine — ricaricarla ogni volta sarebbe lavoro sprecato.
$("auto").onclick = () => {
  if (timerAuto) {
    clearInterval(timerAuto);
    timerAuto = null;
    $("auto").classList.remove("on");
    return;
  }
  timerAuto = setInterval(() => aggiornaCattura({ silenzioso: true }), 5000);
  $("auto").classList.add("on");
  aggiornaCattura({ silenzioso: true });
};

// I gestori vanno registrati prima di connect(), altrimenti il primo
// risultato spinto dall'host arriva mentre non c'è ancora nessuno ad
// ascoltarlo e il pannello resta vuoto.
app.ontoolresult = (r) => {
  const dati = estrai(r);
  disegna(dati);
  if (dati && !dati.error) aggiornaCattura();
};

await app.connect();
</script>
</body>
</html>
""".replace("__APP_BUNDLE__", _APP_JS)


CONTENUTI_URI = "ui://unreal/contenuti.html"

CONTENUTI_HTML = """<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="utf-8" />
<title>Contenuti Unreal</title>
<style>
  :root {
    color-scheme: light dark;
    --bg: #ffffff; --fg: #1a1a18; --muted: #6b6b66;
    --line: rgba(0,0,0,.12); --hover: rgba(0,0,0,.06);
    --sel: rgba(60,120,220,.16); --selfg: #1b4c96;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #1f1f1d; --fg: #ececea; --muted: #9a9a94;
      --line: rgba(255,255,255,.14); --hover: rgba(255,255,255,.07);
      --sel: rgba(120,170,255,.20); --selfg: #a8c8ff;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--fg);
    font: 400 13px/1.5 ui-sans-serif, system-ui, sans-serif;
  }
  .testa {
    display: flex; align-items: baseline; gap: 10px;
    padding: 14px 14px 0;
  }
  h1 { font: 500 15px/1.4 inherit; margin: 0; }
  .tot { color: var(--muted); font-size: 12px; flex: 1; }

  button, select {
    font: inherit; font-size: 12px; padding: 4px 9px; border-radius: 6px;
    border: .5px solid var(--line); background: transparent; color: inherit;
    cursor: pointer; line-height: 1.5;
  }
  button:hover:not(:disabled) { background: var(--hover); }
  button:disabled { opacity: .4; cursor: default; }
  button.on { background: var(--sel); color: var(--selfg); border-color: transparent; }

  .lavori { padding: 10px 14px 0; }
  .job { margin-bottom: 8px; }
  .job p { margin: 0 0 3px; font-size: 12px; display: flex; gap: 8px; }
  .job p b { font-weight: 500; }
  .job p span { color: var(--muted); margin-left: auto; font-variant-numeric: tabular-nums; }
  .barra { height: 4px; border-radius: 2px; background: var(--hover); overflow: hidden; }
  .barra i {
    display: block; height: 100%; width: 34%; border-radius: 2px;
    background: var(--selfg); opacity: .75;
    animation: scorre 1.6s ease-in-out infinite;
  }
  .barra.fatto i { width: 100%; animation: none; }
  @keyframes scorre {
    0% { transform: translateX(-100%); }
    100% { transform: translateX(330%); }
  }

  .wrap { display: grid; grid-template-columns: 168px minmax(0,1fr); gap: 14px; padding: 12px 14px 14px; }
  @media (max-width: 620px) { .wrap { grid-template-columns: minmax(0,1fr); } }

  .cat { display: block; width: 100%; text-align: left; border: 0; border-radius: 6px;
         padding: 5px 8px; background: transparent; display: flex; gap: 8px; }
  .cat:hover { background: var(--hover); }
  .cat.on { background: var(--sel); color: var(--selfg); }
  .cat span { margin-left: auto; color: var(--muted); font-variant-numeric: tabular-nums; }
  .cat.on span { color: inherit; opacity: .8; }

  input[type=search] {
    font: inherit; font-size: 12px; width: 100%; padding: 5px 8px;
    border-radius: 6px; border: .5px solid var(--line);
    background: transparent; color: inherit; margin-bottom: 8px;
  }
  .elenco { max-height: 340px; overflow-y: auto; margin: 0 -4px; padding: 0 4px; }
  .riga {
    display: flex; align-items: center; gap: 8px;
    border-radius: 5px; padding: 4px 7px; font-size: 12px;
  }
  .riga:hover { background: var(--hover); }
  .riga b { font-weight: 500; }
  .riga em { font-style: normal; color: var(--muted); font-size: 11px; }
  .riga .via { margin-left: auto; display: flex; gap: 4px; }
  .nome { min-width: 0; }
  .nome b, .nome em { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .vuoto { color: var(--muted); font-size: 12px; padding: 1rem 0; margin: 0; text-align: center; }
  .err {
    margin: 0 14px; padding: 7px 10px; border-radius: 6px;
    border: .5px solid var(--line); color: var(--muted); font-size: 12px;
  }
  [hidden] { display: none !important; }
</style>
</head>
<body>
<p class="err" id="err" hidden></p>

<div class="testa">
  <h1 id="titolo">Contenuti</h1>
  <span class="tot" id="tot"></span>
  <button id="pin" title="Tieni il pannello sempre visibile">Aggancia</button>
  <button id="refresh">Aggiorna</button>
</div>

<div class="lavori" id="lavori"></div>

<div class="wrap">
  <div id="categorie"></div>
  <div>
    <input type="search" id="filtro" placeholder="Cerca nel percorso" />
    <div class="elenco" id="elenco"><p class="vuoto">Scegli una categoria</p></div>
  </div>
</div>

<script type="module">
__APP_BUNDLE__
const { App } = globalThis.__EXT_APPS__;

const $ = (id) => document.getElementById(id);
const app = new App({ name: "Unreal contenuti", version: "1.0.0" });

let conteggi = {};
let categoria = null;
let timerLavori = null;
let attesa = null;

const mostraErrore = (t) => { $("err").textContent = t; $("err").hidden = !t; };

const estrai = (r) => {
  if (!r) return null;
  if (r.structuredContent) return r.structuredContent;
  const testo = r.content?.find((c) => c.type === "text")?.text;
  if (!testo) return null;
  try { return JSON.parse(testo); } catch { return null; }
};

// L'ordine è quello in cui uno cerca le cose aprendo un progetto, non
// alfabetico: prima dove si gioca, poi cosa si sente, poi di cosa è fatto.
const ORDINE = ["livelli", "audio", "blueprint", "mesh", "animazioni",
                "materiali", "texture", "effetti", "altro"];

const disegnaCategorie = () => {
  const presenti = ORDINE.filter((c) => conteggi[c]);
  $("categorie").replaceChildren(...presenti.map((c) => {
    const b = document.createElement("button");
    b.className = "cat" + (c === categoria ? " on" : "");
    b.append(Object.assign(document.createElement("i"), {
      style: "font-style:normal", textContent: c,
    }));
    b.append(Object.assign(document.createElement("span"), {
      textContent: String(conteggi[c]),
    }));
    b.onclick = () => { categoria = c; disegnaCategorie(); caricaElenco(); };
    return b;
  }));
};

const nomeBreve = (percorso) => String(percorso).split("/").pop().split(".")[0];

const disegnaElenco = (dati) => {
  const asset = (dati && dati.assets) || [];
  if (!asset.length) {
    $("elenco").replaceChildren(Object.assign(document.createElement("p"), {
      className: "vuoto", textContent: dati?.error || "Nessun asset",
    }));
    return;
  }

  const righe = asset.map((a) => {
    const r = document.createElement("div");
    r.className = "riga";
    const n = document.createElement("div");
    n.className = "nome";
    n.append(Object.assign(document.createElement("b"), { textContent: nomeBreve(a.path) }));
    n.append(Object.assign(document.createElement("em"), { textContent: a.path }));
    r.append(n);

    const via = document.createElement("div");
    via.className = "via";
    if (categoria === "livelli") {
      const apri = document.createElement("button");
      apri.textContent = "Apri";
      apri.title = "Carica questo livello nell'editor";
      apri.onclick = () => apriLivello(a.path, apri);
      via.append(apri);
    }
    const chiedi = document.createElement("button");
    chiedi.textContent = "Chiedi";
    chiedi.title = "Fai analizzare questo asset a Claude";
    chiedi.onclick = () => app.sendMessage({
      content: [{ type: "text", text: `Parlami dell'asset ${a.path} (${a.class}).` }],
    });
    via.append(chiedi);
    r.append(via);
    return r;
  });

  if (dati.troncato) {
    righe.push(Object.assign(document.createElement("p"), {
      className: "vuoto",
      textContent: `Mostrati ${asset.length} di ${dati.totale}: restringi con la ricerca.`,
    }));
  }
  $("elenco").replaceChildren(...righe);
};

const caricaElenco = async () => {
  if (!categoria) return;
  $("elenco").replaceChildren(Object.assign(document.createElement("p"), {
    className: "vuoto", textContent: "Carico…",
  }));
  try {
    const r = await app.callServerTool({
      name: "ue_content_list",
      arguments: { category: categoria, query: $("filtro").value.trim() || null },
    });
    disegnaElenco(estrai(r));
  } catch (e) {
    disegnaElenco({ error: String(e?.message || e) });
  }
};

const apriLivello = async (percorso, bottone) => {
  const prima = bottone.textContent;
  bottone.disabled = true;
  bottone.textContent = "Apro…";
  try {
    await app.callServerTool({ name: "ue_open_level", arguments: { path: percorso } });
    await aggiorna();
  } catch (e) {
    mostraErrore(String(e?.message || e));
  } finally {
    bottone.disabled = false;
    bottone.textContent = prima;
  }
};

// ------------------------------------------------------------------- lavori

// Nessuna percentuale: build, cook e render non la espongono, e inventarla
// dando una barra che avanza a caso è peggio che non averla. Si mostra la
// fase e il tempo trascorso, che sono le due cose vere che si sanno.
const disegnaLavori = (stato) => {
  const attivi = Object.entries(stato || {})
    .filter(([, v]) => v && v.running);

  if (!attivi.length) { $("lavori").replaceChildren(); return; }

  $("lavori").replaceChildren(...attivi.map(([nome, v]) => {
    const box = document.createElement("div");
    box.className = "job";
    const riga = document.createElement("p");
    riga.append(Object.assign(document.createElement("b"), { textContent: nome }));
    if (v.phase) riga.append(document.createTextNode(v.phase));
    const secondi = Math.round(Number(v.elapsed_seconds) || 0);
    riga.append(Object.assign(document.createElement("span"), {
      textContent: secondi >= 60
        ? `${Math.floor(secondi / 60)}m ${secondi % 60}s`
        : `${secondi}s`,
    }));
    box.append(riga);
    const barra = document.createElement("div");
    barra.className = "barra";
    barra.append(document.createElement("i"));
    box.append(barra);
    return box;
  }));
};

const controllaLavori = async () => {
  try {
    const r = await app.callServerTool({ name: "ue_jobs_status", arguments: {} });
    disegnaLavori(estrai(r));
  } catch {
    // Un polling che fallisce non deve riempire il pannello di errori:
    // al giro dopo riprova, e intanto la vista resta usabile.
  }
};

// ------------------------------------------------------------------- pannello

const disegna = (dati) => {
  if (!dati) return;
  if (dati.error) { mostraErrore(dati.error); return; }
  mostraErrore("");
  $("titolo").textContent = dati.project ? `Contenuti — ${dati.project}` : "Contenuti";
  $("tot").textContent = `${dati.totale} asset`;
  conteggi = dati.conteggi || {};
  // Aprire su "livelli" invece che su niente: è la categoria che si guarda
  // per prima, ed evita una vista vuota all'apertura.
  if (!categoria) categoria = conteggi.livelli ? "livelli" : ORDINE.find((c) => conteggi[c]);
  disegnaCategorie();
  caricaElenco();
};

const aggiorna = async () => {
  $("refresh").disabled = true;
  try {
    const r = await app.callServerTool({ name: "ue_content_panel", arguments: {} });
    disegna(estrai(r));
  } catch (e) {
    mostraErrore(String(e?.message || e));
  } finally {
    $("refresh").disabled = false;
  }
};

$("refresh").onclick = aggiorna;

// Il filtro rimbalza: ogni tasto premuto sarebbe un giro fino all'editor.
$("filtro").oninput = () => {
  clearTimeout(attesa);
  attesa = setTimeout(caricaElenco, 250);
};

// `pip` tiene la vista fuori dal flusso della conversazione, quindi resta
// visibile mentre la chat scorre. È l'host a deciderlo, non noi.
//
// Due errori facili, entrambi fatti la prima volta: chiedere senza guardare
// `availableDisplayModes`, e — peggio — ignorare il valore di ritorno.
// `requestDisplayMode` risponde con la modalità *davvero* applicata, che può
// non essere quella chiesta: fidarsi della richiesta accende un bottone che
// non ha cambiato niente, ed è indistinguibile da un bug.
const preparaAggancio = () => {
  const pin = $("pin");
  const disponibili = app.getHostContext()?.availableDisplayModes;

  if (Array.isArray(disponibili) && !disponibili.includes("pip")) {
    pin.textContent = "Aggancio non disponibile";
    pin.title = "Questo client non supporta la modalità pip";
    pin.disabled = true;
    return;
  }

  pin.onclick = async () => {
    const acceso = pin.classList.contains("on");
    const chiesta = acceso ? "inline" : "pip";
    pin.disabled = true;
    try {
      const esito = await app.requestDisplayMode({ mode: chiesta });
      const ottenuta = esito?.mode;
      pin.classList.toggle("on", ottenuta === "pip");
      if (ottenuta !== chiesta) {
        pin.textContent = "Aggancio rifiutato";
        pin.title = `Ho chiesto "${chiesta}", l'host è rimasto su "${ottenuta}"`;
      }
    } catch (e) {
      pin.textContent = "Aggancio non disponibile";
      pin.title = String(e?.message || e);
      pin.disabled = true;
      return;
    } finally {
      pin.disabled = false;
    }
  };
};

app.ontoolresult = (r) => disegna(estrai(r));

await app.connect();
preparaAggancio();
controllaLavori();
timerLavori = setInterval(controllaLavori, 3000);
</script>
</body>
</html>
""".replace("__APP_BUNDLE__", _APP_JS)

# Unreal Engine MCP

[English](README.md)

Un server [MCP](https://modelcontextprotocol.io) che permette a un agente AI di
pilotare Unreal Engine 5: creare progetti, importare asset, costruire livelli e
Blueprint, configurare la replication, compilare il C++, avviare il Play In
Editor e produrre il pacchetto del gioco.

**Nessun plugin C++ da compilare.** Usa due plugin già inclusi nel motore —
*Python Editor Script Plugin* e *Remote Control API* — e parla con l'editor via
HTTP locale.

```
                    ┌─ livello LOCALE ── processi + HTTP pubblico
                    │    UnrealEditor.exe, UnrealBuildTool, RunUAT, download asset
Agente ──stdio──▶ MCP │
                    └─ livello EDITOR ── HTTP :30010 (Remote Control API)
                         └─ ExecutePythonCommandEx → editor in esecuzione
```

Il livello locale esiste perché la Remote Control API funziona **solo con un
editor già aperto**: creare un progetto, avviarlo, compilare e produrre il
pacchetto sono tutte operazioni a livello di processo.

- **72 tool** e **5 resource** — [riferimento completo](docs/TOOLS.it.md)
- **265 test**, nessuno dei quali richiede Unreal installato
- **[Note sull'automazione di Unreal](docs/UNREAL-NOTES.md)** — le trappole delle API trovate sul campo

---

## Requisiti

- Unreal Engine **5.0 o successivo** (sviluppato e provato su 5.8 — vedi
  [compatibilità con le versioni](#compatibilit%C3%A0-con-le-versioni-di-unreal))
- Python 3.10+
- Windows, Linux o macOS. Lo sviluppo avviene su Windows, dove il livello locale
  è più collaudato; i percorsi Linux e macOS (`Build.sh`, `RunUAT.sh`, `pgrep`)
  ci sono e la CI li esegue, ma hanno meno chilometri alle spalle. Il livello
  editor è indipendente dalla piattaforma.

### Compatibilità con le versioni di Unreal

Il server rileva a runtime cosa supporta il motore in esecuzione — `ue_status`
lo riporta in `capabilities` — e quando una funzione manca fallisce con un
messaggio esplicito invece di un errore Python criptico:

| Funzionalità | Funziona su |
|---|---|
| Attori, livelli, spawn/transform, PIE, impostazioni progetto, build e packaging, Sound Cue | **5.0+** |
| Creazione Blueprint, componenti (`SubobjectDataSubsystem`) | **5.0+** |
| Import glTF/`.glb` via Interchange | **5.2+** (prima: abilita il plugin *glTF Importer*; il tool te lo dice quando è quello il problema) |
| Variabili membro dei Blueprint + replication per variabile (`ue_add_variable`) | **5.4+** (prima non esiste l'API Python — il tool lo dice esplicitamente) |
| MetaSounds | qualunque 5.x col plugin *MetaSound* abilitato |

Le build custom del motore vanno bene: il rilevamento si basa sull'API Python
effettivamente presente, non sul numero di versione.

## Installazione

```bash
pip install unreal-engine-mcp
```

Oppure senza installare niente, se hai [uv](https://docs.astral.sh/uv/):

```bash
uvx unreal-engine-mcp
```

Oppure dai sorgenti, per metterci mano:

```bash
git clone https://github.com/FFZackFair92/unreal-engine-mcp.git
cd unreal-engine-mcp
pip install -e .
```

## Collegarlo al tuo client

Il server parla MCP standard su stdio, quindi **funziona con qualunque client
MCP** — Claude, Cursor, VS Code, Windsurf, OpenAI Codex, agenti custom. Il
comando è sempre lo stesso; cambia solo dove sta il file di configurazione.

### Claude Desktop / Cowork

`Impostazioni ▸ Sviluppatore ▸ Modifica configurazione`, poi dentro `mcpServers`:

```json
{
  "mcpServers": {
    "unreal-mcp": {
      "command": "python",
      "args": ["-m", "unreal_mcp.server"],
      "env": { "UE_MCP_PORT": "30010" }
    }
  }
}
```

### Claude Code

```bash
claude mcp add unreal-mcp -- python -m unreal_mcp.server
```

### Cursor

`~/.cursor/mcp.json` (globale) oppure `.cursor/mcp.json` nel progetto — stesso
JSON di Claude Desktop (chiave radice `mcpServers`).

### VS Code (Copilot agent mode)

`.vscode/mcp.json` — attenzione: qui la chiave radice è `servers`:

```json
{
  "servers": {
    "unreal-mcp": { "type": "stdio", "command": "python", "args": ["-m", "unreal_mcp.server"] }
  }
}
```

### Windsurf

`~/.codeium/windsurf/mcp_config.json` — stessa forma `mcpServers` di Claude Desktop.

### OpenAI Codex CLI

`~/.codex/config.toml`:

```toml
[mcp_servers.unreal-mcp]
command = "python"
args = ["-m", "unreal_mcp.server"]
```

### OpenAI Agents SDK (agenti custom)

```python
from agents.mcp import MCPServerStdio

unreal = MCPServerStdio(params={"command": "python", "args": ["-m", "unreal_mcp.server"]})
```

Dopo aver modificato la configurazione riavvia il client completamente. In ogni
caso `python` deve essere l'interprete in cui hai eseguito `pip install -e .` —
nel dubbio usa il percorso assoluto.

> **Nota:** l'interfaccia connettori di ChatGPT accetta solo server MCP
> *remoti*. Questo server è stdio/locale per costruzione (pilota processi sulla
> tua macchina): usalo dai client con supporto stdio — quelli qui sopra — o
> mettici davanti un proxy MCP se ti serve davvero via HTTP.

## Preparare il lato Unreal

### Progetto starter — zero setup, zero build

Copia [`StarterProject/`](StarterProject/) dove vuoi e apri il `.uproject`
con qualunque UE 5.0+: plugin abilitati, config di sicurezza pronta, web
server avviato in automatico. Essendo Blueprint-only con plugin del motore,
**non c'è niente da compilare** — a differenza degli starter che includono
un plugin C++.

### Progetto nuovo — niente da fare a mano

Chiedi all'agente di eseguire `ue_project_create`. Scrive il `.uproject` con i
plugin necessari, le chiavi di sicurezza che servono al Remote Control e un
`init_unreal.py` che avvia il web server a ogni apertura dell'editor.

```
ue_engine_list  →  ue_project_create  →  ue_editor_open  →  ue_status
```

### Progetto esistente — tre passi

1. **Abilita i plugin**: `Edit ▸ Plugins` → *Python Editor Script Plugin* e
   *Remote Control API*. Riavvia quando richiesto.

2. **Autorizza il Python remoto.** Crea `Config/DefaultRemoteControl.ini`:

   ```ini
   [/Script/RemoteControlCommon.RemoteControlSettings]
   bAutoStartWebServer=True
   bAutoStartWebSocketServer=True
   RemoteControlHttpServerPort=30010
   bEnableRemotePythonExecution=True
   bAllowAnyRemoteFunctionCall=False
   +CustomAllowedRemoteFunctionCalls=(ClassPath="/Script/PythonScriptPlugin.PythonScriptLibrary")
   bAllowConsoleCommandRemoteExecution=False
   ```

   Sono **due controlli distinti** — uno sblocca l'oggetto, l'altro la chiamata
   di funzione — e falliscono con errori diversi. `DefaultEngine.ini` è il file
   sbagliato: `URemoteControlSettings` è dichiarata
   `UCLASS(config = RemoteControl)`. Riavvia l'editor.

   Sui motori che precedono alcune di queste chiavi, le chiavi vengono
   semplicemente ignorate — le versioni vecchie non filtravano quelle chiamate.

   Il server ascolta solo su `127.0.0.1`: l'esecuzione Python remota non è
   raggiungibile da fuori dalla macchina.

   Cosa può fare il server una volta collegato, e come aprirlo in sicurezza se
   davvero ti serve raggiungere l'editor da un'altra macchina, sta in
   [SECURITY.md](SECURITY.md).

   `bAllowConsoleCommandRemoteExecution` resta **disattivo**. Abilita
   `ExecuteConsoleCommand` *via web API*, che questo server non chiama mai: i
   comandi console che gli servono (`LiveCoding.Compile`,
   `WebControl.StartServer`, `HighResShot`) partono da dentro Python con
   `unreal.SystemLibrary.execute_console_command` e non passano da quel
   controllo. Le versioni precedenti di questo progetto lo mettevano a `True`;
   sui progetti esistenti si può portare a `False` senza perdere nulla.

3. **Verifica**: apri `http://127.0.0.1:30010/remote/info` nel browser. Se
   risponde con del JSON, sei collegato.

## Cosa sa fare

| Ambito | In sintesi |
|---|---|
| **Progetti** | Trova le installazioni del motore, crea progetti da specifica, gestisce i plugin |
| **Ciclo di vita editor** | Apertura (attendendo il bridge), stato, chiusura pulita |
| **C++** | Genera classi compilabili con il boilerplate scritto giusto, poi ci riaggancia i Blueprint |
| **Compilazione** | C++ in background; `ue_live_compile` ricompila **a editor aperto** via Live Coding |
| **Pacchetto** | `RunUAT BuildCookRun` → eseguibile autonomo, con indicazione della fase |
| **Asset** | Import `.glb`/`.gltf`/`.fbx`/`.wav`, elenco e ricerca nel Content Browser |
| **Livelli** | Creazione e apertura, spawn (anche in blocco), spostamento, eliminazione, proprietà sugli attori piazzati |
| **Materiali** | Costruzione del grafo, collegamento delle texture PBR, material instance, assegnazione agli attori |
| **Blueprint** | Creazione, componenti, variabili tipizzate con replication, class defaults, reparent, compilazione |
| **Networking** | Flag di replication, PIE multi-client, impostazioni di progetto |
| **Audio** | Import wav, MetaSound source, Sound Cue |
| **Asset gratuiti** | Download da Poly Haven, ambientCG e Kenney (tutti CC0), più qualunque URL diretto |
| **Riscontro visivo** | `ue_screenshot` restituisce la viewport **come immagine**: l'agente vede davvero cosa ha costruito |

Elenco completo dei parametri in [docs/TOOLS.it.md](docs/TOOLS.it.md).

### Aggirare il limite sui grafi Blueprint

**I grafi Blueprint non si possono costruire da Python.** `EdGraph.Nodes` è
protetta, i pin non sono esposti e non esiste un'API per collegarli: è un limite
del motore, non di questo server. Dettagli in
[docs/UNREAL-NOTES.md](docs/UNREAL-NOTES.md).

Quello che funziona: **mettere la logica in una classe C++ padre.** Il Blueprint
resta il contenitore di componenti e valori regolabili, il comportamento si
eredita.

```
ue_cpp_class_create      # scrive la classe, e l'intero modulo C++ se il
                         # progetto era Blueprint-only
ue_editor_close
ue_build_start           # poi ue_build_status finché running=false
ue_editor_open
ue_reparent_blueprint    # il Blueprint eredita il comportamento
```

Le variabili Blueprint con lo stesso nome di una `UPROPERTY` del nuovo padre
vengono assorbite, quindi i valori impostati nell'editor sopravvivono al
passaggio. Le funzioni `BlueprintCallable` diventano nodi richiamabili dal
grafo: l'agente costruisce il vocabolario che poi il designer collega a mano.

I grafi *materiale*, a differenza di quelli Blueprint, sono pienamente
programmabili: `ue_create_material` crea e collega davvero i nodi.

### Altri limiti

- **Compilare il C++ richiede l'editor chiuso**, a meno che la modifica tocchi
  solo il corpo delle funzioni: in quel caso `ue_live_compile` funziona a editor
  aperto.
- **Il packaging richiede sempre l'editor chiuso**: il passo di build riscrive le
  stesse DLL che l'editor tiene in memoria.
- **La disponibilità delle funzioni varia con la versione del motore** — vedi
  la [tabella di compatibilità](#compatibilit%C3%A0-con-le-versioni-di-unreal);
  `ue_status` riporta cosa supporta il motore in esecuzione.
- **I contenuti Fab/Marketplace** non hanno API pubblica. Quei tool si appoggiano
  al client community [`legendary`](https://github.com/derrod/legendary).

## Configurazione

| Variabile | Default | A cosa serve |
|---|---|---|
| `UE_MCP_HOST` / `UE_MCP_PORT` | `127.0.0.1` / `30010` | Endpoint Remote Control |
| `UE_MCP_TIMEOUT` | `180` | Timeout per chiamata, in secondi |
| `UE_MCP_ENGINE_DIRS` | — | Cartelle extra dove cercare le installazioni del motore |
| `UE_MCP_LIBRARY` | `~/UnrealAssetLibrary` | Dove finiscono gli asset scaricati |
| `UE_MCP_MAX_DOWNLOAD` | 4 GiB | Limite per singolo download |

Se il motore sta in un percorso insolito puoi anche mettere un file
`mcp_engine.txt` accanto al `.uproject` con dentro il percorso, oppure passare
`engine_root` esplicitamente.

## Sviluppo

I test girano **senza Unreal installato**: `tests/fake_unreal.py` sostituisce il
modulo `unreal` e `tests/fake_server.py` emula la Remote Control API *eseguendo
davvero* gli snippet generati — quindi coprono l'intera catena
tool → snippet → harness → risultato.

```bash
pip install -e ".[dev]"
pytest -q
ruff check .
```

Per aggiungere un tool: un helper riutilizzabile in `src/unreal_mcp/ue_side.py`
(lato editor), una funzione `@mcp.tool()` in `server.py` e un test in `tests/`.
Le convenzioni che conviene conoscere stanno in [CONTRIBUTING.md](CONTRIBUTING.md).

`ue_side.py` viene installato come modulo dentro l'editor e identificato
dall'hash del suo sorgente: le modifiche lato editor hanno effetto alla chiamata
successiva senza riavviare il server, e tutte le altre chiamate sono solo un
piccolo snippet che lo importa. Le modifiche a `server.py` o `local.py`
richiedono un riavvio del client.

## Problemi comuni

| Sintomo | Causa |
|---|---|
| `Nessuna risposta da http://127.0.0.1:30010` | Editor chiuso, o web server mai avviato → console: `WebControl.StartServer` |
| `Object Default__PythonScriptLibrary cannot be accessed remotely` | Manca `bEnableRemotePythonExecution` in `DefaultRemoteControl.ini` |
| `Executing function 'ExecutePythonCommandEx' is not allowed` | Manca la voce `CustomAllowedRemoteFunctionCalls` |
| `404 su /remote/object/call` | Plugin *Remote Control API* non abilitato |
| `NameError: name 'unreal' is not defined` | Plugin *Python Editor Script Plugin* non abilitato |
| `Unable to build while Live Coding is active` | `LiveCodingConsole.exe` sopravvive all'editor: va terminato (i tool di build lo fanno da soli) |
| `Nessuna installazione di Unreal Engine trovata` | Imposta `UE_MCP_ENGINE_DIRS`, aggiungi `mcp_engine.txt`, o passa `engine_root` |
| `ModuleNotFoundError: No module named 'mcp.server.fastmcp'` | È installata la `mcp` 2.x, mentre questo server usa la linea 1.x. `pip install "mcp<2"` — reinstallare il pacchetto lo fa da solo |

## Licenza

MIT — vedi [LICENSE](LICENSE).

## Riferimenti

- [Remote Control for Unreal Engine](https://dev.epicgames.com/documentation/en-us/unreal-engine/remote-control-for-unreal-engine)
- [Remote Control Quick Start](https://dev.epicgames.com/documentation/en-us/unreal-engine/remote-control-quick-start-for-unreal-engine)
- [Importing glTF files](https://dev.epicgames.com/documentation/en-us/unreal-engine/importing-gltf-files-into-unreal-engine)
- [API Poly Haven](https://github.com/Poly-Haven/Public-API) · [API ambientCG](https://docs.ambientcg.com/api/v2/full_json/)

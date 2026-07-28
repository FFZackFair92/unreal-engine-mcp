# Riferimento dei tool

49 tool, divisi su due livelli. Funziona su UE 5.0+ — i tool legati alla versione sono contrassegnati; `ue_status` riporta le `capabilities` del motore in esecuzione.

**Livello locale** — gira come processo sulla tua macchina. Trova i motori, crea
progetti, apre e chiude l'editor, compila il C++, produce il pacchetto, scarica
asset. Funziona a editor chiuso.

**Livello editor** — parla con un editor *in esecuzione* tramite la Remote
Control API (`http://127.0.0.1:30010`), eseguendo Python al suo interno. Richiede
l'editor aperto.

Le posizioni sono in centimetri (1 unità Unreal = 1 cm), Z è l'asse verticale. I
path degli asset seguono la convenzione Unreal (`/Game/...`).

---

## Motore e progetti (locale)

| Tool | Parametri | Cosa fa |
|---|---|---|
| `ue_engine_list` | — | Installazioni trovate via `UE_MCP_ENGINE_DIRS`, elenco dell'Epic Launcher e registro di Windows (comprese le build registrate a mano, identificate da GUID). |
| `ue_engine_templates` | `engine_version` | Template ufficiali inclusi nel motore (`TP_Blank`, `TP_ThirdPerson`, …). |
| `ue_project_create` | `name`, `directory`, `engine_version`, `template`, `blueprint_only`, `plugins`, `default_map`, `default_game_mode`, `description`, `force` | Crea un progetto già pronto per il bridge: `.uproject` con i plugin necessari, `DefaultRemoteControl.ini` con le due chiavi di sicurezza e `Content/Python/init_unreal.py`. Può copiare un template del motore, escludendone il `Source/` per restare Blueprint-only. |
| `ue_project_find` | `directory`, `max_depth` | Cerca i file `.uproject` sotto una cartella. |
| `ue_project_info` | `uproject` | Versione motore associata, plugin attivi, se il bridge è pronto, se il progetto ha C++. |
| `ue_project_set_plugins` | `uproject`, `enable`, `disable` | Abilita/disabilita plugin scrivendo nel `.uproject`. Richiede il riavvio dell'editor. |

## Ciclo di vita dell'editor (locale)

| Tool | Parametri | Cosa fa |
|---|---|---|
| `ue_editor_open` | `uproject`, `engine_version`, `wait_seconds`, `extra_args` | Avvia l'editor e **attende che il bridge risponda**, così la chiamata successiva è sicura. Il primo avvio compila gli shader e può durare minuti. |
| `ue_editor_status` | — | Se il processo editor è vivo (anche se avviato a mano), se Live Coding è attivo, se il bridge risponde. |
| `ue_editor_close` | `save_all`, `force` | Chiusura pulita: salva, poi `quit_editor` via bridge. Ripiega sulla terminazione del processo. |

## Compilazione e pacchetto (locale)

| Tool | Parametri | Cosa fa |
|---|---|---|
| `ue_build_start` | `uproject`, `engine_version`, `target`, `configuration` | Compila il modulo C++ in background. **Editor chiuso.** Termina prima `LiveCodingConsole.exe`, che sopravvive all'editor e tiene il lock sulle DLL. |
| `ue_build_status` | `tail_lines` | In corso o finita, codice di uscita, errori e warning del compilatore già estratti, coda del log. |
| `ue_live_compile` | `max_wait_seconds` | Ricompila **a editor aperto**, via Live Coding. Applica patch solo al corpo delle funzioni: aggiungere o modificare `UCLASS`/`UFUNCTION`/`UPROPERTY` cambia i dati di reflection e richiede comunque `ue_build_start`. |
| `ue_package_start` | `uproject`, `configuration`, `maps`, `output_dir`, `dedicated_server`, `engine_version` | Cook + build + stage + pak tramite `RunUAT BuildCookRun`. Produce un eseguibile autonomo. **Editor chiuso.** |
| `ue_package_status` | `tail_lines` | Fase corrente (Cook, Stage, Package, Archive), errori e percorso dell'`.exe` prodotto. |

Né build né packaging attendono la fine dentro la chiamata: supererebbero il
timeout MCP. Partono e si consulta il tool di stato.

## Diagnostica (editor)

| Tool | Parametri | Cosa fa |
|---|---|---|
| `ue_status` | — | Versione motore, progetto aperto, livello corrente, numero di attori. **Da chiamare per primo in ogni sessione.** |
| `ue_read_log` | `lines`, `only_errors` | Coda del log di Unreal, con filtro opzionale su errori e warning. |
| `ue_exec_python` | `code` | Python arbitrario dentro l'editor. La via di fuga: `unreal` e tutti gli helper `mcp_*` sono disponibili; assegna a `result` per restituire un valore. |
| `ue_save_all` | — | Salva il livello corrente e tutti gli asset modificati. |

## Asset e livelli (editor)

| Tool | Parametri | Cosa fa |
|---|---|---|
| `ue_import_assets` | `files`, `destination`, `replace_existing`, `import_as_skeletal` | Importa `.glb`/`.gltf`/`.fbx`/`.wav` tramite il framework Interchange. |
| `ue_import_audio` | `files`, `destination` | Importa file `.wav` come SoundWave. |
| `ue_list_assets` | `path`, `recursive`, `class_filter` | Elenca gli asset sotto un path, filtrabili per nome di classe. |
| `ue_new_level` | `path`, `template` | Crea un livello e lo apre. |
| `ue_open_level` | `path` | Apre un livello esistente. |

## Attori (editor)

| Tool | Parametri | Cosa fa |
|---|---|---|
| `ue_spawn_actor` | `class_ref`, `location`, `rotation`, `scale`, `label` | Spawna da nome di classe, path `/Script/...`, path di un Blueprint o asset static mesh. La `label` è il modo con cui gli altri tool lo ritrovano. |
| `ue_list_actors` | `name_contains`, `class_contains` | Elenca gli attori del livello, con filtri opzionali. |
| `ue_set_actor_transform` | `label`, `location`, `rotation`, `scale` | Sposta, ruota e scala un attore identificato dalla label. |
| `ue_delete_actor` | `label` | Rimuove un attore dal livello. |

## Blueprint (editor)

| Tool | Parametri | Cosa fa |
|---|---|---|
| `ue_create_blueprint` | `package_path`, `name`, `parent_class` | Nuovo Blueprint col parent indicato (`Actor`, `Character`, `GameModeBase`, o un path completo). Idempotente. |
| `ue_add_component` | `blueprint_path`, `component_class`, `name` | Aggiunge un componente tramite `SubobjectDataSubsystem` e ricompila. |
| `ue_add_variable` | `blueprint_path`, `var_name`, `var_type`, `sub_type`, `replicated`, `instance_editable`, `default_value` | Variabile membro tipizzata, con flag di replication ed esposizione sulle istanze. Tipi: `bool`, `int`, `int64`, `float`, `string`, `name`, `text`, `byte`, `struct`, `object`, `class`. **UE 5.4+** — i motori precedenti non hanno l'API Python per farlo; il tool fallisce con un messaggio esplicito. |
| `ue_set_class_defaults` | `blueprint_path`, `properties` | Scrive i Class Defaults sul CDO. |
| `ue_compile_blueprint` | `blueprint_path` | Compila e salva. |
| `ue_set_replication` | `blueprint_path`, `replicates`, `replicate_movement`, `always_relevant` | Flag di rete sul CDO. |

**Non supportato: costruire i grafi Blueprint.** UE 5.8 non lo espone a Python —
`EdGraph.Nodes` è protetta, i tipi dei pin non sono esposti e non esiste un'API
per collegarli. Questi tool coprono variabili, componenti e default; la logica va
in C++ o costruita a mano nell'editor. Vedi [UNREAL-NOTES.md](UNREAL-NOTES.md).

## Play In Editor (editor)

| Tool | Parametri | Cosa fa |
|---|---|---|
| `ue_configure_pie` | `num_players`, `net_mode`, `one_process` | Numero di client e net mode (`standalone`, `listen_server`, `client`) per provare il multiplayer in locale. |
| `ue_start_pie` / `ue_stop_pie` | — | Avvia e ferma la sessione. |
| `ue_set_project_setting` | `section`, `key`, `value`, `config` | Scrive in `Config/Default<config>.ini`. Alcune impostazioni richiedono il riavvio dell'editor. |

## Audio (editor)

| Tool | Parametri | Cosa fa |
|---|---|---|
| `ue_create_metasound_source` | `package_path`, `name` | Asset MetaSound Source vuoto (richiede il plugin MetaSound). |
| `ue_create_sound_cue` | `package_path`, `name`, `wave_path` | Sound Cue, opzionalmente già collegato a un SoundWave importato. |

## Download di asset gratuiti (locale)

I download finiscono in `UE_MCP_LIBRARY` (default `~/UnrealAssetLibrary`),
vengono verificati con l'md5 pubblicato dove disponibile, hanno un limite di
dimensione (`UE_MCP_MAX_DOWNLOAD`) e gli archivi si estraggono con protezione
contro il path traversal.

| Tool | Fonte | Licenza |
|---|---|---|
| `preset_search_polyhaven` / `preset_download_polyhaven` | [Poly Haven](https://polyhaven.com) — HDRI, texture PBR, modelli. I download glTF si portano dietro `.bin` e texture. | CC0 |
| `preset_search_ambientcg` / `preset_download_ambientcg` | [ambientCG](https://ambientcg.com) — materiali PBR, HDRI, modelli. | CC0 |
| `preset_download_kenney` | [kenney.nl](https://kenney.nl) — pack low-poly. Nessuna API: il link si risolve dalla pagina. | CC0 |
| `preset_download_url` | Qualunque URL diretto (zip/glb/fbx/wav), estratto automaticamente. | dipende |
| `preset_extract_archive` | Uno zip/tar già su disco. I `.rar` non sono supportati dalla libreria standard. | — |
| `preset_library_list` | Elenca la libreria locale, pronta per `ue_import_assets`. | — |
| `preset_fab_list_vault` / `preset_fab_download` | Contenuti Fab/Marketplace acquistati. | la tua licenza Epic |

> **Avvertenza su Fab.** I contenuti acquistati stanno dietro il login Epic e non
> hanno API pubblica. Questi due tool si appoggiano al client community
> [`legendary`](https://github.com/derrod/legendary) (`pip install legendary-gl`,
> poi `legendary auth`). Senza, il tool spiega come scaricare dall'Epic Games
> Launcher. È software di terze parti, non una via ufficiale.

## Estenderlo con tool specifici del tuo progetto

Qualunque `local_tools.py` messo accanto a `server.py` viene importato
automaticamente all'avvio e può registrare tool aggiuntivi con `@mcp.tool()`:

```python
from .server import lit, mcp, run

@mcp.tool()
async def miogioco_bootstrap(root: str = "/Game/MioGioco") -> dict:
    """Componi le primitive in un workflow a una sola chiamata per il tuo gioco."""
    return await run(f"result = mcp_create_blueprint({lit(root + '/Blueprints')}, 'BP_MioGameMode', 'GameModeBase')")
```

Il file è in `.gitignore`: i workflow per-progetto (strutture di cartelle, serie
di Blueprint con replication e variabili tipizzate, popolamento di livelli)
restano sulla tua macchina e fuori dal repo pubblico. Stesso trattamento per i
loro test in `tests/test_local_tools.py`.

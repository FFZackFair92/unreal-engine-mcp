# Riferimento dei tool

162 tool, divisi su due livelli. Funziona su UE 5.0+ — i tool legati alla versione sono contrassegnati; `ue_status` riporta le `capabilities` del motore in esecuzione.

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

## C++ (locale)

| Tool | Parametri | Cosa fa |
|---|---|---|
| `ue_cpp_class_create` | `uproject`, `class_name`, `parent_class`, `module`, `properties`, `functions`, `with_tick`, `force` | Genera una classe compilabile col boilerplate Unreal scritto giusto: `UCLASS`, `GENERATED_BODY`, macro `MODULO_API`, header del parent corretto (`AIController.h` non sta sotto `GameFramework/`) e prefisso `A`/`U` secondo la gerarchia. Le proprietà replicate ottengono anche `GetLifetimeReplicatedProps` e i `DOREPLIFETIME` — dimenticarli non dà errori, semplicemente la replication non avviene. Se il progetto è Blueprint-only, viene creato l'intero modulo C++. |

Le voci di `properties` accettano `name`, `type` e, opzionali, `category`,
`default`, `replicated`, `rep_notify`, `read_only`. Quelle di `functions`
accettano `name`, `return_type`, `params`, `specifiers` (default
`BlueprintCallable`) e `body`: una funzione `BlueprintCallable` diventa
richiamabile dai grafi Blueprint, ed è così che la logica generata arriva nelle
mani di un designer.

È l'aggiramento del limite sui grafi Blueprint. Flusso completo:

```
ue_cpp_class_create → ue_editor_close → ue_build_start
→ ue_build_status (finché running=false) → ue_editor_open → ue_reparent_blueprint
```

## Compilazione e pacchetto (locale)

| Tool | Parametri | Cosa fa |
|---|---|---|
| `ue_build_start` | `uproject`, `engine_version`, `target`, `configuration` | Compila il modulo C++ in background. **Editor chiuso.** Termina prima `LiveCodingConsole.exe`, che sopravvive all'editor e tiene il lock sulle DLL. |
| `ue_build_status` | `tail_lines` | In corso o finita, codice di uscita, errori e warning del compilatore già estratti, coda del log. |
| `ue_live_compile` | `max_wait_seconds` | Ricompila **a editor aperto**, via Live Coding. Applica patch solo al corpo delle funzioni: aggiungere o modificare `UCLASS`/`UFUNCTION`/`UPROPERTY` cambia i dati di reflection e richiede comunque `ue_build_start`. |
| `ue_package_start` | `uproject`, `configuration`, `maps`, `output_dir`, `dedicated_server`, `engine_version` | Cook + build + stage + pak tramite `RunUAT BuildCookRun`. Produce un eseguibile autonomo. **Editor chiuso.** |
| `ue_build_unblock` | `dry_run`, `engine_version` | Trova — e con `dry_run=False` termina — i processi che tengono il lock globale di build di Epic, quello che `ue_build_status` segnala come `blocked`. Cerca sulla *riga di comando*, non sul nome dell'immagine: su UE 5 UnrealBuildTool è un assembly .NET dentro `dotnet.exe`, quindi `taskkill /IM UnrealBuildTool.exe` non lo trova mai. |
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
| `ue_delete_asset` | `path`, `force` | Elimina un asset o una cartella. Per default rifiuta se qualcosa lo referenzia — eliminarlo comunque lascia riferimenti rotti in livelli e Blueprint — ed elenca chi lo referenzia così puoi decidere. |
| `ue_rename_asset` | `path`, `new_path` | Sposta o rinomina un asset, aggiornando i riferimenti. |
| `ue_duplicate_asset` | `path`, `new_path` | Duplica un asset: il modo rapido per fare una variante. |
| `ue_make_folder` | `path` | Crea una cartella nel Content Browser. Idempotente. |

## Attori (editor)

| Tool | Parametri | Cosa fa |
|---|---|---|
| `ue_spawn_actor` | `class_ref`, `location`, `rotation`, `scale`, `label` | Spawna da nome di classe, path `/Script/...`, path di un Blueprint o asset static mesh. La `label` è il modo con cui gli altri tool lo ritrovano. |
| `ue_spawn_many` | `actors` | Spawna una lista di attori in **una sola chiamata e una sola transazione**. Ogni voce è `{class_ref, location, rotation, scale, label}`. Una voce che fallisce viene segnalata senza perdere le altre. |
| `ue_list_actors` | `name_contains`, `class_contains` | Elenca gli attori del livello, con filtri opzionali. |
| `ue_set_actor_transform` | `label`, `location`, `rotation`, `scale` | Sposta, ruota e scala un attore identificato dalla label. |
| `ue_set_actor_property` | `label`, `properties`, `component` | Imposta proprietà su un attore **già piazzato** o su un suo componente: la mesh, l'intensità di una luce, il raggio di un trigger. I vettori sono `{"x":…}`, i colori `{"r":…}`, e le stringhe `/Game/...` vengono caricate come asset. |
| `ue_list_actor_components` | `label` | Componenti di un attore piazzato, con nome e classe: dice cosa passare come `component` qui sopra. |
| `ue_delete_actor` | `label` | Rimuove un attore dal livello. |
| `ue_attach_actor` | `child_label`, `parent_label`, `socket`, `attach_rule` | Aggancia un attore a un altro, così muovere il padre muove anche il figlio. `attach_rule` è `KEEP_WORLD`, `KEEP_RELATIVE` o `SNAP_TO_TARGET`. |
| `ue_detach_actor` | `label`, `keep_world` | Sgancia un attore dal suo padre. |
| `ue_actor_hierarchy` | `label` | Albero padre/figlio degli attori del livello, o di un sottoalbero. |

Le modifiche agli attori sono avvolte in `ScopedEditorTransaction`: tutto quello
qui sopra è annullabile con Ctrl+Z da chi sta guardando l'editor.

## Blueprint (editor)

| Tool | Parametri | Cosa fa |
|---|---|---|
| `ue_create_blueprint` | `package_path`, `name`, `parent_class` | Nuovo Blueprint col parent indicato (`Actor`, `Character`, `GameModeBase`, o un path completo). Idempotente. |
| `ue_add_component` | `blueprint_path`, `component_class`, `name` | Aggiunge un componente tramite `SubobjectDataSubsystem` e ricompila. |
| `ue_add_variable` | `blueprint_path`, `var_name`, `var_type`, `sub_type`, `replicated`, `instance_editable`, `default_value` | Variabile membro tipizzata, con flag di replication ed esposizione sulle istanze. Tipi: `bool`, `int`, `int64`, `float`, `string`, `name`, `text`, `byte`, `struct`, `object`, `class`. **UE 5.4+** — i motori precedenti non hanno l'API Python per farlo; il tool fallisce con un messaggio esplicito. |
| `ue_set_class_defaults` | `blueprint_path`, `properties` | Scrive i Class Defaults sul CDO. |
| `ue_reparent_blueprint` | `blueprint_path`, `new_parent`, `remove_unused_variables` | Riassegna la classe padre, tipicamente a una classe C++ generata. Le variabili che coincidono con una `UPROPERTY` del nuovo padre vengono assorbite; le altre sopravvivono rinominate con `_0`. Riporta quali sono state assorbite. |
| `ue_compile_blueprint` | `blueprint_path` | Compila e salva. |
| `ue_set_replication` | `blueprint_path`, `replicates`, `replicate_movement`, `always_relevant` | Flag di rete sul CDO. Per il resto vedi [Networking](#networking-editor). |

**Non supportato: costruire i grafi Blueprint.** UE 5.8 non lo espone a Python —
`EdGraph.Nodes` è protetta, i tipi dei pin non sono esposti e non esiste un'API
per collegarli. La via d'uscita non è programmare il grafo ma non averne bisogno:
si genera una classe C++ padre con `ue_cpp_class_create`, si compila e ci si
riaggancia il Blueprint con `ue_reparent_blueprint`. Vedi
[UNREAL-NOTES.md](UNREAL-NOTES.md).

## Materiali (editor)

A differenza dei grafi Blueprint, **i grafi materiale sono pienamente
programmabili**: questi tool creano e collegano davvero i nodi.

| Tool | Parametri | Cosa fa |
|---|---|---|
| `ue_create_material` | `package_path`, `name`, `textures`, `scalars`, `two_sided` | Crea un materiale e collega le texture ai canali PBR (`base_color`, `normal`, `roughness`, `metallic`, `ambient_occlusion`, `emissive`, `opacity`). Con la chiave `"auto"` il canale si deduce dal nome del file — segue le convenzioni di ambientCG e Poly Haven, quindi gli asset scaricati si collegano da soli. Le normal map ricevono sRGB spento e il sampler giusto. |
| `ue_create_material_instance` | `package_path`, `name`, `parent_path`, `parameters` | Material Instance da un materiale padre. Un numero è uno scalare, `{"r","g","b"}` un colore, un bool uno static switch, un path `/Game/...` una texture. |
| `ue_assign_material` | `label`, `material_path`, `slot`, `component` | Assegna un materiale a un attore piazzato. |

## Console e rendering

| Tool | Parametri | Cosa fa |
|---|---|---|
| `ue_console_command` | `command`, `wait_seconds` | Esegue un comando console dell'editor e restituisce **cosa ha stampato**. I comandi console non restituiscono valori, scrivono nel log, quindi il tool confronta il log prima e dopo la chiamata; un comando che non stampa nulla lo dice esplicitamente invece di lasciare l'ambiguità. Passa dall'interprete Python dell'editor, non dal gate `bAllowConsoleCommandRemoteExecution` — stessa avvertenza di `ue_exec_python`. |
| `ue_render_sequence` | `uproject`, `sequence`, `config`, `map_path`, `output_dir`, `resolution`, `force` | Renderizza una Level Sequence tramite la Movie Render Queue in un processo headless `UnrealEditor-Cmd`. La MRQ nell'editor è asincrona e terrebbe occupato l'editor per tutto il render; un processo separato si segue come una build. `config` (un preset Movie Pipeline salvato) è come si scelgono formato, risoluzione e cartella di output — senza, la MRQ usa i default di progetto e potrebbe non scrivere nulla. |
| `ue_render_status` | `tail_lines`, `uproject`, `wait_seconds` | Avanzamento del render. `succeeded` si decide dai **file prodotti**, non dal codice di uscita: un run headless della MRQ può uscire con 0 avendo renderizzato zero frame. I file nuovi si trovano confrontando la cartella di output con uno snapshot preso all'avvio, non tramite data di modifica — confrontare l'orologio di un processo con quello di un filesystem su rete condivisa non trova niente. |

> Questi due sono i tool meno verificati del progetto. Le loro parti pure sono
> testate — mappatura del formato, costruzione del comando, validazione dei
> path, raccolta dell'output — ma nessun test qui ha mai renderizzato un frame
> con un motore reale.

## Camera della viewport (editor)

| Tool | Parametri | Cosa fa |
|---|---|---|
| `ue_get_camera` | — | Dove si trova la camera della viewport dell'editor e cosa sta guardando. Vale la pena chiamarlo **prima di spawnare**: i livelli reali sono spesso costruiti a migliaia di unità da `[0,0,0]`, quindi un attore piazzato all'origine può risultare fuori schermo e invisibile. |
| `ue_set_camera` | `location`, `rotation` | Sposta la camera. Passando solo uno dei due argomenti, l'altro resta invariato. |
| `ue_focus_actor` | `label`, `distance` | Inquadra un attore, come premere F nell'editor. Il complemento di `ue_screenshot` — senza, si fotografa qualunque cosa la camera stesse guardando per caso. |

## Screenshot (editor)

| Tool | Parametri | Cosa fa |
|---|---|---|
| `ue_screenshot` | `filename`, `width`, `height`, `return_image` | Cattura la viewport dell'editor e **restituisce l'immagine stessa** come `ImageContent` MCP, così il modello la vede davvero — un semplice percorso lascerebbe l'agente cieco quanto prima. Il PNG resta comunque su disco sotto `<Progetto>/Saved/Screenshots/MCP`. La cattura avviene uno o due frame dopo, quindi il tool attende il file e segnala se non è mai comparso. |

La risoluzione di default è volutamente modesta (960×540): il PNG viaggia
codificato in base64 dentro la risposta, e a 1280×720 spesso costa più
contesto di quanto l'immagine faccia risparmiare. Sopra `UE_MCP_MAX_SCREENSHOT`
(1.5 MB) l'immagine non viene allegata e il tool spiega perché.
`return_image=False` torna a restituire solo il percorso.

## Play In Editor (editor)

| Tool | Parametri | Cosa fa |
|---|---|---|
| `ue_configure_pie` | `num_players`, `net_mode`, `one_process` | Numero di client e net mode (`standalone`, `listen_server`, `client`) per provare il multiplayer in locale. |
| `ue_start_pie` | `mode` | Avvia la sessione. `play` (predefinito) è il Play vero, Alt+P: parte il GameMode, il PlayerController possiede il pawn, l'input del giocatore arriva. `simulate` è Simulate, Alt+S: il mondo gira ma nessuno possiede un pawn e l'input non è instradato — utile per guardare fisica o IA senza giocatore. |
| `ue_stop_pie` | — | Ferma la sessione, in entrambe le modalità. |
| `ue_set_project_setting` | `section`, `key`, `value`, `config` | Scrive in `Config/Default<config>.ini`. Alcune impostazioni richiedono il riavvio dell'editor. |

## Audio (editor)

| Tool | Parametri | Cosa fa |
|---|---|---|
| `ue_create_metasound_source` | `package_path`, `name` | Asset MetaSound Source vuoto (richiede il plugin MetaSound). |
| `ue_create_sound_cue` | `package_path`, `name`, `wave_path` | Sound Cue, opzionalmente già collegato a un SoundWave importato. |

## Reflection (editor)

La Python API di UE non ha un modo generico per elencare proprietà e funzioni
di una classe — `get_editor_property(nome)` richiede di conoscere già il
nome. Questi tool coprono quello che la API offre davvero: attraversare la
gerarchia di classi/struct e leggere i valori di un enum nativo.

| Tool | Parametri | Cosa fa |
|---|---|---|
| `ue_find_classes` | `parent`, `name_contains`, `limit` | Classi (native e Blueprint) derivate da `parent`, `parent` incluso. Le Blueprint del progetto compaiono col nome generato (`BP_PlayerCharacter_C`). |
| `ue_find_structs` | `parent`, `name_contains`, `limit` | Struct derivati da `parent` (`ScriptStruct`), `parent` incluso. |
| `ue_reflect_enum` | `enum_name` | Nome, valore numerico e display name di ogni voce di un enum nativo del motore (es. `"CollisionChannel"`, senza il prefisso `E`). Non copre gli enum Blueprint (`UserDefinedEnum`, asset in `/Game/...`): per quelli serve `ue_exec_python` + `unreal.load_asset(path)`. |

## UMG (editor)

| Tool | Parametri | Cosa fa |
|---|---|---|
| `ue_create_widget_blueprint` | `package_path`, `name`, `parent_class`, `editor_utility` | Asset Widget Blueprint vuoto (o Editor Utility Widget con `editor_utility=True`). `parent_class` accetta una classe C++ del progetto, per la via BindWidget. **L'albero che crea non ha radice** — vedi sotto. |

Per la logica vale ancora il workaround C++: dare al widget una classe C++
parent con proprietà `BindWidget` (`ue_cpp_class_create` →
`ue_reparent_blueprint`), con i nomi delle proprietà uguali a quelli dei
widget.

## Layout UMG (editor)

Anche questa sezione corregge quello che c'era scritto prima. `WidgetTree` *è*
una proprietà protetta — ma l'oggetto che ci sta dietro è un subobject del
Widget Blueprint, e si prende per nome: `find_object(wbp, "WidgetTree")`. Da
lì il layout si costruisce con l'API pubblica dei widget stessi
(`PanelWidget.add_child` e compagnia sono UFUNCTION vere, e funzionano sui
template dell'editor — cosa che nessuno aveva provato). Verificato dal vivo
su UE 5.8: CanvasPanel → VerticalBox → TextBlock + Button, con testo, colore,
padding e posizione, salvato e riletto da zero — gerarchia e valori intatti,
nomi dei widget presenti nel `.uasset`.

**Un limite è reale e resta.** `WidgetTree.RootWidget` è protetta anche in
scrittura, e nessuna UFUNCTION la imposta (cercata in tutte le classi
esposte). Quindi il *primo* widget di un albero vuoto non è creabile da
Python. Un Widget Blueprint fatto nel Widget Designer una radice ce l'ha; uno
fatto da `ue_create_widget_blueprint` no. La via pratica è
`ue_duplicate_asset` di un Widget Blueprint che ce l'ha, poi svuotarlo con
`ue_umg_remove_widget`.

I widget si indirizzano per nome (`Titolo`, `CanvasPanel_0`) — univoci dentro
un albero, e sono gli stessi che si vedono nel pannello Hierarchy.

| Tool | Parametri | Cosa fa |
|---|---|---|
| `ue_umg_tree_info` | `widget_blueprint_path` | La gerarchia: nomi, classi, figli, classe dello slot. `root: null` vuol dire albero vuoto. |
| `ue_umg_add_widget` | `widget_blueprint_path`, `widget_class`, `parent?`, `name?`, `slot?` | Crea un widget e lo mette sotto un pannello (la radice, di default). |
| `ue_umg_set_widget_property` | `widget_blueprint_path`, `widget`, `properties` | Testo, colore, visibilità, brush… Le stringhe diventano `FText` dove serve. Restituisce `applied` e `failed` separati. |
| `ue_umg_set_slot` | `widget_blueprint_path`, `widget`, `properties` | Il layout dentro il pannello che lo contiene. |
| `ue_umg_remove_widget` | `widget_blueprint_path`, `widget` | Toglie un widget e tutto quello che contiene. |

**Le chiavi dello slot dipendono dal pannello**, e `ue_umg_tree_info` riporta
`slot_class` per sapere quale hai davanti:

- `CanvasPanelSlot` — `position` `[x, y]`, `size` `[x, y]`, `z_order`,
  `alignment`, `auto_size`.
- `VerticalBoxSlot` / `HorizontalBoxSlot` — `padding`,
  `horizontal_alignment`, `vertical_alignment`.

`padding` e `position` arrivano entrambi come lista dal ponte MCP, ma il
motore vuole due struct diversi: una lista di 2 numeri diventa `Vector2D`,
una di 4 (o un dict con `left`/`top`/`right`/`bottom`) diventa un `Margin`.

```
ue_duplicate_asset("/Engine/Sequencer/DefaultBurnIn", "/Game/UI/WBP_Menu")
ue_umg_tree_info("/Game/UI/WBP_Menu")            # trova la radice, svuotala
ue_umg_add_widget("/Game/UI/WBP_Menu", "VerticalBox", name="Colonna",
                  slot={"position": [80, 80], "size": [500, 300]})
ue_umg_add_widget("/Game/UI/WBP_Menu", "TextBlock", parent="Colonna", name="Titolo")
ue_umg_set_widget_property("/Game/UI/WBP_Menu", "Titolo", {"Text": "Menu principale"})
ue_umg_set_slot("/Game/UI/WBP_Menu", "Titolo", {"padding": [8, 8, 8, 12]})
```

## Grafo Blueprint (editor)

Tre scorciatoie per compiti specifici. L'authoring dei nodi sta nella
[sezione successiva](#authoring-del-grafo-blueprint-editor).

| Tool | Parametri | Cosa fa |
|---|---|---|
| `ue_bp_list_graphs` | `blueprint_path` | Nomi dei grafi di un Blueprint (EventGraph, UserConstructionScript, funzioni...). |
| `ue_bp_list_events` | `blueprint_path` | Eventi visibili su un Blueprint (custom, ereditati overridabili, di interfaccia), ciascuno con `is_implemented`. |
| `ue_bp_add_event_override` | `blueprint_path`, `event_name`, `x`, `y` | Aggiunge (o ritrova) il nodo di un evento ereditato overridabile; restituisce path e pin. |
| `ue_bp_add_function_graph` | `blueprint_path`, `func_name` | Grafo funzione vuoto con nodi Entry/Return di default. |

## Authoring del grafo Blueprint (editor)

**UE 5.8+.** `ue_status` lo riporta come `capabilities.blueprint_graph_authoring`;
sui motori che non ce l'hanno questi tool falliscono con una spiegazione, e la
risposta resta la classe C++ padre.

Questa sezione corregge quello che c'era scritto qui prima. Il muro era vero —
`EdGraph.Nodes` è una proprietà protetta, e lo è tutt'ora — ma la conclusione
che se ne era tratta era sbagliata: `Nodes` non serve toccarla, perché
`unreal.BlueprintGraphEditor` manipola il grafo dall'esterno, come fa
l'editor stesso. Verificato dal vivo su UE 5.8: BeginPlay → PrintString →
Branch con una variabile booleana che alimenta `Condition`, fili exec
collegati, letterale scritto su `InString`, Blueprint compilato
`BS_UP_TO_DATE` senza errori né warning, salvato e riletto da zero con le
connessioni al loro posto.

Un nodo si indirizza col suo **nome oggetto** (`K2Node_CallFunction_0`), che
ogni tool restituisce quando lo crea. I *titoli* seguono la lingua
dell'editor ("Ramo" per Branch) e non vanno usati come chiave. I nodi evento,
che esistono già nel grafo, hanno l'alias `event:<NomeMembro>` — es.
`event:ReceiveBeginPlay`.

| Tool | Parametri | Cosa fa |
|---|---|---|
| `ue_bp_graph_info` | `blueprint_path`, `graph_name?` | Nodi, pin, connessioni ed errori di compilazione. Il punto di partenza: dà i nomi oggetto che servono a tutto il resto. |
| `ue_bp_add_call_function` | `blueprint_path`, `function_path`, `graph_name?`, `position?` | Nodo di chiamata a funzione. `function_path` è `/Script/<Modulo>.<Classe>:<Funzione>`, es. `/Script/Engine.KismetSystemLibrary:PrintString`. |
| `ue_bp_add_branch` | `blueprint_path`, `graph_name?`, `position?` | Nodo Branch — `Condition` in ingresso, `then`/`else` in uscita. |
| `ue_bp_add_custom_event` | `blueprint_path`, `event_name`, `graph_name?`, `position?` | Custom Event. Solo nei grafi evento: una funzione non può contenerne. |
| `ue_bp_add_variable_node` | `blueprint_path`, `variable_name`, `mode`, `graph_name?`, `position?`, `class_path?` | Nodo Get o Set di una variabile membro (creala prima con `ue_add_variable`). |
| `ue_bp_add_node_by_name` | `blueprint_path`, `node_name`, `graph_name?`, `position?` | Un nodo qualunque della palette, per `Categoria\|Nome`. Ultima spiaggia — vedi la trappola della localizzazione. |
| `ue_bp_list_palette` | `blueprint_path`, `graph_name?`, `contains?`, `limit?` | Cerca per sottostringa fra i nodi aggiungibili. |
| `ue_bp_connect` | `blueprint_path`, `from_node`, `from_pin`, `to_node`, `to_pin`, `graph_name?` | Collega un pin di uscita a uno di ingresso. I tipi incompatibili vengono riportati entrambi. |
| `ue_bp_break_pin` | `blueprint_path`, `node`, `pin`, `graph_name?` | Stacca tutti i fili di un pin, e dice quanti erano. |
| `ue_bp_set_pin_value` | `blueprint_path`, `node`, `pin`, `value`, `graph_name?` | Valore letterale di un pin di ingresso non collegato. |
| `ue_bp_remove_node` | `blueprint_path`, `node`, `graph_name?` | Cancella un nodo e i suoi fili. |

**Due trappole, trovate entrambe dal vivo.**

*La palette è localizzata.* Su un editor italiano il Branch è
`Utilità|ControlloDiFlusso|Ramo` — passare `Utilities|FlowControl|Branch` non
restituisce niente. Per questo i tool tipizzati (`ue_bp_add_branch`,
`ue_bp_add_call_function`, `ue_bp_add_variable_node`) sono la via principale e
`ue_bp_add_node_by_name` è l'uscita di sicurezza; quando serve, trova la
stringa esatta con `ue_bp_list_palette`.

*Unreal non valida i valori dei pin.* Scrivere `"non_un_bool"` su un pin
booleano viene accettato e memorizzato così com'è. Per questo
`ue_bp_set_pin_value` rilegge sempre il pin e restituisce quello che c'è
davvero — guarda quello, invece di fidarti che la chiamata sia andata a buon
fine.

Un esempio completo, da zero a grafo funzionante:

```
ue_create_blueprint("/Game/MyGame", "BP_Porta", "Actor")
ue_add_variable("/Game/MyGame/BP_Porta", "Aperta", "bool")
n = ue_bp_add_call_function("/Game/MyGame/BP_Porta",
                            "/Script/Engine.KismetSystemLibrary:PrintString")
ue_bp_connect("/Game/MyGame/BP_Porta", "event:ReceiveBeginPlay", "then", n.node, "execute")
ue_bp_set_pin_value("/Game/MyGame/BP_Porta", n.node, "InString", "Porta pronta")
ue_bp_graph_info("/Game/MyGame/BP_Porta")     # controlla errors == []
```

## Animazione (editor)

A differenza di UMG e del grafo Blueprint, qui la scrittura funziona
davvero: `BlendParameters`/`SampleData` di un BlendSpace sono array di
struct ordinari, non protetti — confermato dal vivo creando un asset,
riempiendolo, salvando e ricaricandolo da zero su un progetto reale
(Remy_Skeleton, BS_Remy_Locomozione). L'AnimGraph di un Anim Blueprint resta
però un EdGraph come gli altri — stesso muro, quindi
`ue_create_anim_blueprint` crea solo l'asset.

| Tool | Parametri | Cosa fa |
|---|---|---|
| `ue_skeleton_info` | `skeleton_path` | Ossa e socket dalla reference pose di uno Skeleton. |
| `ue_anim_sequence_info` | `anim_path` | Durata, numero di frame, notify track/event, sync marker e nomi curve di un AnimSequence. |
| `ue_create_blend_space_1d` | `package_path`, `name`, `skeleton_path`, `axis_name`, `axis_min`, `axis_max`, `grid_num`, `samples` | Crea un BlendSpace1D con un asse e, opzionalmente, i suoi sample (`[{"value": float, "animation": path}, ...]`). Solo 1D per ora — BlendSpace (2D) usa la stessa struttura dati ma non è stata verificata dal vivo in questa fase. |
| `ue_create_anim_montage` | `package_path`, `name`, `source_animation_path` | AnimMontage che incapsula un AnimSequence esistente, con il suo slot di default. |
| `ue_create_anim_blueprint` | `package_path`, `name`, `skeleton_path`, `parent_class` | Asset Anim Blueprint vuoto associato a uno Skeleton. L'AnimGraph va disegnato a mano nell'Anim Blueprint Editor. |

## Niagara / VFX (editor)

Stesso muro di sempre: `EmitterHandles` di `NiagaraSystem` è protetta —
niente aggiunta di emitter o moduli via Python, confermato dal vivo anche su
template popolati della libreria di sistema del motore. Leggere gli emitter
e i parametri esposti di un sistema *esistente* funziona invece davvero, a
livello di asset — non serve un PIE in esecuzione né un componente
istanziato in scena.

| Tool | Parametri | Cosa fa |
|---|---|---|
| `ue_create_niagara_system` | `package_path`, `name` | Asset Niagara System vuoto. L'emitter stack va costruito a mano nel Niagara Editor. |
| `ue_niagara_system_info` | `system_path` | Emitter (nome, abilitato, lightweight) e parametri utente esposti (nome, tipo) di un Niagara System esistente. |

## Gameplay: fisica, navmesh, AI (editor)

Fisica/collisione e navmesh sono pienamente scriptabili — verificato dal
vivo. Blackboard e Behavior Tree rompono il pattern "i grafi sono protetti"
di UMG/Blueprint/Niagara: il loro albero (`RootNode`, `Children`,
`Decorators`, `Services`) è davvero scrivibile via Python, perché sotto non
c'è un vero `EdGraph` ma semplici `UObject` e struct. EQS resta bloccato
come UMG/Blueprint/Niagara — solo l'asset vuoto è scriptabile. L'AI
Perception si aggiunge con `ue_add_component` generico; `SensesConfig` va
impostato a mano nel pannello Details del Blueprint (`EditDefaultsOnly`, non
raggiungibile in modo affidabile dal component template via Python).

I nodi del Behavior Tree si indirizzano con un path a punti fatto di indici
dei figli a partire dalla radice: `"root"` è la radice stessa, `"0"` il suo
primo figlio, `"0.1"` il secondo figlio di quel primo figlio, e così via.
`ue_bt_add_node` restituisce il path del nodo appena creato, da riusare
nelle chiamate successive.

| Tool | Parametri | Cosa fa |
|---|---|---|
| `ue_set_component_physics` | `actor`, `component`, `simulate_physics?`, `collision_enabled?`, `collision_profile?` | Simulazione fisica e collisione di un componente. `collision_enabled` accetta `NoCollision`/`QueryOnly`/`PhysicsOnly`/`QueryAndPhysics`/`QueryAndProbe`/`ProbeOnly`, senza distinzione tra maiuscole/underscore. |
| `ue_component_physics_info` | `actor`, `component` | Stato attuale di simulate/collision-enabled/collision-profile. |
| `ue_nav_rebuild` | — | Rigenera il navmesh (comando console `RebuildNavigation`). Serve almeno un `NavMeshBoundsVolume` nel livello: piazzalo con `ue_spawn_actor`. |
| `ue_nav_query_point` | `origin`, `radius` | Un punto raggiungibile a caso sul navmesh entro un raggio da un'origine. |
| `ue_nav_find_path` | `start`, `end` | Pathfinding sincrono sul navmesh — non serve il PIE in esecuzione. |
| `ue_create_blackboard` | `package_path`, `name` | Asset Blackboard Data vuoto. |
| `ue_blackboard_add_key` | `blackboard_path`, `key_name`, `key_type` | Aggiunge una chiave (`object`/`class`/`bool`/`int`/`float`/`string`/`name`/`vector`/`rotator`/`enum`). |
| `ue_blackboard_info` | `blackboard_path` | Elenca le chiavi di un Blackboard Data esistente. |
| `ue_create_behavior_tree` | `package_path`, `name`, `blackboard_path?`, `root_composite?` | Crea un Behavior Tree con un nodo radice composite già impostato (Selector di default), opzionalmente collegato a un Blackboard. |
| `ue_bt_add_node` | `bt_path`, `parent_path`, `node_class`, `index?` | Aggiunge un nodo composite o task (dedotto dalla classe base) come figlio di un composite esistente. |
| `ue_bt_add_decorator` | `bt_path`, `node_path`, `decorator_class` | Aggiunge un decorator al child link di un nodo (non valido su `"root"`). |
| `ue_bt_add_service` | `bt_path`, `node_path`, `service_class` | Aggiunge un service a un nodo composite (solo Selector/Sequence, non i task). |
| `ue_bt_set_node_property` | `bt_path`, `node_path`, `property_name`, `value` | Imposta una proprietà su un nodo. Gestisce in automatico i campi bindable da blackboard (struct `FValueOrBBKey_*`, es. `BTTask_Wait.WaitTime`) scrivendo nel valore di default fisso. |
| `ue_bt_info` | `bt_path` | Dump ricorsivo dell'albero: nodi, decorator, service, path. |
| `ue_create_eqs_asset` | `package_path`, `name` | Asset Environment Query vuoto. `Options` è protetta — la query va costruita a mano nell'EQS Editor. |

## GAS: Gameplay Ability System (editor)

Richiede il plugin `GameplayAbilities` — abilitalo con
`ue_project_set_plugins` e riavvia l'editor prima che queste classi esistano
in Python.

GameplayEffect e AttributeSet sono Blueprintable "normali": funzionano già
con `ue_create_blueprint` generico (`parent_class="GameplayEffect"` o
`"AttributeSet"`), nessun tool dedicato serve. GameplayAbility ha invece un
asset dedicato (`GameplayAbilityBlueprint`, non un `Blueprint` semplice), da
cui `ue_create_gameplay_ability`. Aggiungere un attributo
(`GameplayAttributeData`) a un AttributeSet funziona già con
`ue_add_variable` — passa il path completo dello struct come `sub_type`
(`/Script/GameplayAbilities.GameplayAttributeData`, non è nella whitelist di
nomi corti). La logica vera dell'abilità (`ActivateAbility` e i suoi nodi)
resta un grafo Blueprint — non scriptabile, come ovunque altrove.

**Il muro, e come è stato aggirato**: il modo normale di costruire un
modifier — `GameplayModifierInfo.Attribute`/`.ModifierOp` via
`set_editor_property` — è bloccato ("cannot be edited on instances"), e
`GameplayAttribute.AttributeName` è read-only. `ue_ge_add_modifier` lo
aggira costruendo l'intero struct `GameplayModifierInfo` in una volta sola
via `import_text` — la stessa tecnica già in uso in questo file per
`EdGraphPinType`. Verificato dal vivo end-to-end: costruito un modifier che
punta a un attributo reale su un AttributeSet Blueprint reale, salvato il
GameplayEffect, ricaricato l'asset da zero — il modifier era davvero lì,
valori inclusi. Non verificato in PIE, solo che persiste sull'asset.

| Tool | Parametri | Cosa fa |
|---|---|---|
| `ue_create_gameplay_ability` | `package_path`, `name`, `instancing_policy?`, `net_execution_policy?` | Crea un GameplayAbility Blueprint (asset dedicato). Imposta opzionalmente InstancingPolicy/NetExecutionPolicy. |
| `ue_create_gameplay_effect` | `package_path`, `name`, `duration_policy?`, `period?` | Crea un GameplayEffect Blueprint (Blueprint generico, parent GameplayEffect). Imposta opzionalmente DurationPolicy e l'intervallo di applicazione periodica. |
| `ue_ge_add_modifier` | `ge_path`, `attribute_set_path`, `attribute_name`, `modifier_op`, `magnitude` | Aggiunge un modifier che collega un attributo di un AttributeSet Blueprint esistente, un'operazione (`add`/`add_final`/`multiply`/`divide`/`multiply_compound`/`override`) e una magnitudine fissa. Solo `ScalableFloat` costante — niente curve o attribute-based magnitude per ora. |
| `ue_ge_add_component` | `ge_path`, `component_class` | Aggiunge un `GameplayEffectComponent` (es. `AssetTagsGameplayEffectComponent`) a un GameplayEffect. Solo l'aggiunta — configurare tag/condizioni al suo interno non è coperto, usa `ue_exec_python` o l'editor. |
| `ue_ge_info` | `ge_path` | Duration policy, periodo, modifier (attributo/operazione/magnitudine) e GameplayEffectComponent di un GameplayEffect esistente. |

## Networking (editor)

`ue_set_replication` decide *se* un attore replica; questi decidono *quanta
banda costa*. È tutto `get/set_editor_property` normale sul CDO del
Blueprint — nessuna proprietà protetta qui, verificato dal vivo su UE 5.8
rileggendo i nomi delle proprietà dentro il `.uasset` salvato.

| Tool | Parametri | Cosa fa |
|---|---|---|
| `ue_set_net_config` | `blueprint_path`, `dormancy?`, `net_update_frequency?`, `min_net_update_frequency?`, `net_priority?`, `net_cull_distance?`, `only_relevant_to_owner?`, `net_use_owner_relevancy?`, `net_load_on_client?` | Dormancy (`awake`/`initial`/`dormant_all`/`dormant_partial`/`never`), frequenze di aggiornamento, priorità di replication e relevancy. Incrementale: tocca solo i parametri che gli passi. `net_cull_distance` è in cm e viene elevata al quadrato per te, che è come Unreal la memorizza. |
| `ue_net_info` | `blueprint_path` | Tutto quanto sopra più `replicates`/`replicate_movement`/`always_relevant`, e quali componenti replicano. |
| `ue_set_component_replication` | `blueprint_path`, `component_name`, `replicates` | Un attore replicato *non* replica i suoi componenti da solo: questo è l'interruttore. Il suffisso `_GEN_VARIABLE` dei template è gestito per te. |
| `ue_set_component_default` | `blueprint_path`, `component_name`, `property_name`, `value` | Scrive una proprietà sul *template* di un componente, non su un'istanza piazzata. |

`ue_set_component_default` chiude un buco lasciato aperto dalla fase gameplay:
una proprietà `EditDefaultsOnly` come `SensesConfig` di un
AIPerceptionComponent viene rifiutata su un attore spawnato ("cannot be edited
on instances"), e arrivare al template del componente sembrava impossibile.
Non lo è: `SubobjectDataBlueprintFunctionLibrary.get_object` trasforma un
handle di subobject nel template. Verificato dal vivo — una configurazione di
senso Sight con raggi personalizzati scritta da Python, salvata e ritrovata
nel `.uasset`.

## Landscape (editor)

**Un landscape non si può creare da Python.** Verificato dal vivo su UE 5.8:
spawnare `Landscape` restituisce un `LandscapePlaceholder` — un attore vuoto,
senza componenti, senza target layer, nemmeno i metodi di `ALandscape`. Le
classi che lo creano davvero (`LandscapeSubsystem`, `LandscapeEditorObject`,
`ActorFactoryLandscape`) esistono nel motore ma non sono esposte al suo
Python. Il terreno va creato una volta con Landscape Mode nell'editor; da lì in
poi tutto il resto è scriptabile da qui.

| Tool | Parametri | Cosa fa |
|---|---|---|
| `ue_landscape_list` | — | I landscape nel livello corrente. Lista vuota = non c'è niente su cui lavorare. |
| `ue_landscape_info` | `label?` | Componenti, materiale, target layer di pittura, edit layer, grass. `label` si omette se il livello ha un solo landscape. |
| `ue_landscape_import_heightmap` | `image_path`, `label?`, `rt_format?`, `from_rg_channel?` | Sovrascrive l'heightmap da un file immagine sul disco. Porta prima l'immagine alla risoluzione del landscape: niente la riscala. |
| `ue_landscape_import_weightmap` | `layer_name`, `image_path`, `label?`, `rt_format?` | Dipinge un target layer da un'immagine in scala di grigi. Il layer deve già esistere: i target layer vengono dal materiale del landscape. |
| `ue_landscape_export_heightmap` | `output_dir`, `file_name`, `label?`, `resolution?`, `rt_format?`, `into_rg_channel?` | Esporta l'heightmap sul disco. `RGBA8` scrive un PNG, i formati float scrivono HDR. |
| `ue_landscape_set_material` | `material_path`, `label?` | Assegna il materiale del landscape — che è anche ciò che definisce i layer dipingibili. |
| `ue_landscape_set_grass` | `enabled`, `label?` | Accende o spegne il sistema di erba procedurale. |

Unreal accetta un heightmap solo come `TextureRenderTarget2D`, quindi questi
tool se lo costruiscono: `import_file_as_texture2d` →
`begin_draw_canvas_to_render_target` → `Canvas.draw_texture`. La catena è
verificata dal vivo fino al render target compreso (un PNG a gradiente 64×64
riletto pixel per pixel con i valori giusti); l'ultima chiamata,
`landscape_import_heightmap_from_render_target`, **non** lo è, perché non
c'era nessun landscape su cui provarla e Python non può crearne uno.

`RGBA8` dà 256 livelli di altezza. Per un heightmap a 16 bit vero usa
`RGBA16f` o `RGBA32f` con `from_rg_channel=True`, che è come Unreal impacchetta
16 bit di altezza in due canali.

## PCG: Procedural Content Generation (editor)

La sorpresa della roadmap di parità. Dopo che Blueprint, UMG e Niagara hanno
sbattuto tutti contro lo stesso muro del grafo protetto, il PCG risulta
**pienamente scriptabile** — nodi, archi, posizioni e proprietà dei nodi.
Verificato dal vivo su UE 5.8 costruendo Input → SurfaceSampler →
StaticMeshSpawner, salvando e ricaricando l'asset da zero: nodi, archi e
`points_per_squared_meter` c'erano ancora, e i nomi dei nodi compaiono nel
`.uasset`.

Il motivo è lo stesso dei Behavior Tree: un grafo PCG è un grafo di oggetti
dati veri (`UPCGNode` + `UPCGEdge`), non un `UEdGraph` di nodi K2 con il
contenuto vero dietro una proprietà protetta. L'`UPCGEditorGraph` è solo la
sua rappresentazione visiva, e non serve toccarlo.

Richiede il plugin PCG — abilitalo con `ue_project_set_plugins`.

| Tool | Parametri | Cosa fa |
|---|---|---|
| `ue_create_pcg_graph` | `package_path`, `name` | Asset PCGGraph vuoto, con i suoi nodi Input e Output già dentro. |
| `ue_pcg_add_node` | `graph_path`, `settings_class`, `position?` | Aggiunge un nodo e restituisce nome e pin. In PCG il tipo di un nodo *è* la sua classe di settings: `PCGSurfaceSamplerSettings`, `PCGStaticMeshSpawnerSettings`, `PCGCreatePointsGridSettings`… |
| `ue_pcg_connect` | `graph_path`, `from_node`, `from_pin`, `to_node`, `to_pin` | Collega un pin di uscita a uno di ingresso. `"input"` e `"output"` sono alias dei due nodi del grafo. I nomi di pin sbagliati vengono rifiutati con l'elenco di quelli veri. |
| `ue_pcg_disconnect` | `graph_path`, `from_node`, `from_pin`, `to_node`, `to_pin` | Rimuove un collegamento, e dice se ce n'era davvero uno. |
| `ue_pcg_remove_node` | `graph_path`, `node` | Toglie un nodo e i suoi collegamenti. |
| `ue_pcg_set_node_property` | `graph_path`, `node`, `property_name`, `value` | Scrive una proprietà sulle settings del nodo (`points_per_squared_meter`, `seed`, il path di una mesh…). |
| `ue_pcg_graph_info` | `graph_path` | Nodi (nome, classe di settings, pin, posizione) e archi. È così che si scoprono i nomi dei pin prima di collegare. |
| `ue_pcg_spawn_volume` | `graph_path`, `label?`, `location?`, `size?` | Piazza un PCGVolume col grafo attaccato. `size` è in cm; il brush di default è 200 cm per lato, quindi diventa scala dell'attore. |
| `ue_pcg_generate` | `label`, `force` | Rigenera il PCG su un attore. Da chiamare dopo aver modificato il grafo. |
| `ue_pcg_cleanup` | `label`, `remove_components` | Cancella ciò che il PCG ha generato, lasciando grafo e volume al loro posto. |

I nomi dei pin sono le etichette visibili, spazi compresi — `"Bounding Shape"`,
non `BoundingShape`. Chiedili a `ue_pcg_graph_info` invece di indovinarli.

## Foliage (editor)

Interamente scriptabile — ma non dalla porta che ci si aspetta.
`EditorFoliageLibrary` e `FoliageEditorSubsystem` **non esistono** nella Python
API di UE 5.8 (verificato con `hasattr` dal vivo, non dedotto). Quello che
esiste è meglio: `InstancedFoliageActor.add_instances` /
`remove_all_instances` sono UFUNCTION statiche vere, e i
`FoliageInstancedStaticMeshComponent` dell'`InstancedFoliageActor` del livello
espongono tutta la superficie di query e rimozione per istanza.

> **Non usare `FoliageStatistics`.** È la libreria che *sembra* fatta per
> contare le istanze, e nel mondo dell'editor risponde sempre 0 — provata dal
> vivo su un box che ne conteneva davvero cinque, con entrambi i world context
> plausibili. È una libreria di gameplay e vuole un mondo di gioco.
> `ue_foliage_query` passa invece dai componenti, e risponde bene senza PIE.

| Tool | Parametri | Cosa fa |
|---|---|---|
| `ue_create_foliage_type` | `package_path`, `name`, `mesh_path`, `properties?` | Crea un FoliageType da una static mesh — la "specie": quale mesh, e con quali regole (densità, scala casuale, allineamento alla normale, collisione). |
| `ue_set_foliage_property` | `foliage_type_path`, `property_name`, `value` | Scrive una proprietà e rilegge quello che è finito davvero lì. |
| `ue_foliage_add_instances` | `foliage_type_path`, `transforms` | Piazza istanze alle trasformate date. Accetta `{location, rotation, scale}` o le sole posizioni. |
| `ue_foliage_scatter` | `foliage_type_path`, `center`, `radius`, `count`, `seed?`, `align_to_ground`, `z_offset` | Sparge N istanze a caso in un cerchio, appoggiando ognuna al terreno con un line trace. È il tool per riempire una zona. |
| `ue_foliage_list` | — | Quello che è davvero piazzato nel livello: mesh, componente, numero di istanze. |
| `ue_foliage_query` | `foliage_type_path`, `center`, `radius`, `limit` | Le istanze dentro una sfera, con le trasformate in world space. Il conteggio è sempre esatto; `limit` taglia solo quello che torna. |
| `ue_foliage_remove` | `foliage_type_path`, `center?`, `radius?` | Toglie istanze — tutte, o solo quelle dentro una sfera. |
| `ue_create_foliage_spawner` | `package_path`, `name`, `foliage_types?`, `tile_size?` | Un ProceduralFoliageSpawner: la ricetta (quali specie, in competizione come). |
| `ue_foliage_spawn_volume` | `spawner_path`, `label?`, `location?`, `size?` | Piazza un ProceduralFoliageVolume con lo spawner attaccato — dove la ricetta si applica. |
| `ue_foliage_simulate` | `label`, `clear` | Fa girare (o azzera) la simulazione procedurale su un volume. |

`ue_foliage_scatter` usa la radice quadrata sul raggio apposta: senza, i punti
casuali si addensano al centro del cerchio.

## Authoring del Sequencer (editor)

Fino alla 0.9.0 il sequencer andava in una direzione sola —
`ue_render_sequence` renderizza una sequenza che ha costruito qualcun altro.
Questi tool la costruiscono.

Nessun muro, contro le aspettative maturate su UMG e Niagara:
`MovieSceneSequenceExtensions`, `MovieSceneBindingExtensions`,
`MovieSceneTrackExtensions` e `MovieSceneSectionExtensions` sono esposte per
intero, e i canali hanno `add_key` / `get_keys`. Verificato dal vivo su UE 5.8
costruendo una sequenza a 30 fps con un attore possessato, una track di
trasformata con due chiavi su `Rotation.Y` e una di visibilità, salvandola e
ritrovando tutto nel `.uasset`.

**Due trappole, entrambe gestite dai tool:**

1. **I nomi dei canali hanno un suffisso numerico instabile.** La stessa
   sezione di trasformata ha dato `Location.Z_0` la prima volta e
   `Location.Z_3` la seconda, nella stessa sessione di editor. I canali si
   indicano *senza* suffisso — `"Location.Z"`.
2. **I nomi visualizzati di track e binding sono localizzati**, esattamente
   come la palette dei nodi Blueprint: su editor italiano la track di
   trasformata si chiama "Trasforma". Le track si indirizzano per tipo o per
   indice, mai per nome visualizzato.

| Tool | Parametri | Cosa fa |
|---|---|---|
| `ue_create_level_sequence` | `package_path`, `name`, `fps?`, `length_frames?` | Level Sequence vuota, con frame rate e range di playback impostati. |
| `ue_sequence_info` | `sequence_path` | Binding, track, sezioni e canali. Chiamalo prima di mettere chiavi: è così che si sa come si chiamano i canali e quali indici usare. |
| `ue_sequence_add_actor` | `sequence_path`, `label`, `spawnable` | Lega un attore del livello. Con `spawnable=True` la sequenza si porta dietro una copia e la crea e distrugge da sé — quello che serve a una cinematica autonoma. |
| `ue_sequence_add_track` | `sequence_path`, `binding`, `track_type`, `start?`, `end?` | Aggiunge una track *e la sua prima sezione* (una track senza sezione non anima niente). Alias: `transform`, `visibility`, `audio`, `animation`, `camera_cut`, `event`, `fade` — o il nome esatto della classe. |
| `ue_sequence_add_key` | `sequence_path`, `binding`, `channel`, `frame`, `value`, `track?`, `track_type?`, `section`, `interpolation?` | Mette una chiave e rilegge tutte le chiavi del canale. |
| `ue_sequence_set_range` | `sequence_path`, `start?`, `end?`, `fps?` | Cambia range di playback e frame rate. |
| `ue_sequence_remove` | `sequence_path`, `binding`, `track?`, `track_type?` | Toglie una track — o l'intero binding, se non se ne indica nessuna. |
| `ue_sequence_open` | `sequence_path`, `close` | Apre (o chiude) la sequenza nella finestra del Sequencer. I tool scrivono sull'asset; questo è il modo di guardare il risultato. |

## Flow: incatenare chiamate a tool (lato server)

Un flow è una lista di chiamate a tool scritta in YAML (o JSON) ed eseguita in
una sola chiamata MCP. Vive interamente lato server: non tocca l'editor se non
attraverso i tool che invoca, e con `dry_run=True` non lo tocca affatto.

Il motivo è il contesto, non la velocità. Una scena si costruisce quasi sempre
con la stessa sequenza di dieci o venti chiamate, e farla passare dal modello
una alla volta gli lascia in memoria diciannove risposte JSON che non gli
servono più.

```yaml
variables:
  base: {x: 0, y: 0, z: 100}
steps:
  - tool: ue_spawn_actor
    args: {class_ref: StaticMeshActor, location: "${base}", label: Cubo}
    save: cubo
  - tool: ue_set_actor_transform
    args: {label: "${cubo.label}", scale: [2, 2, 2]}
  - tool: ue_screenshot
    when: {exists: cubo.label}
```

| Tool | Parametri | Cosa fa |
|---|---|---|
| `ue_flow_run` | `flow`, `variables?`, `dry_run`, `stop_on_error` | Esegue il flow. `flow` è il testo YAML/JSON, oppure il path di un file. |

Ogni passo accetta `tool`, `args`, `save` (la variabile in cui mettere il
risultato), `when`, `continue_on_error` e `name`. Nei valori, `${nome}` e
`${nome.chiave.0}` riprendono quello che un passo precedente ha salvato: una
stringa fatta *solo* di riferimento conserva il tipo del valore (un dict resta
un dict), mentre un riferimento dentro a una frase più lunga viene interpolato
come testo.

`when` è un booleano, un `${riferimento}`, oppure `{equals: [a, b]}`,
`{not_equals: [a, b]}`, `{exists: percorso}` — niente `eval`, per scelta.

Non ci sono cicli né espressioni, ed è voluto: la logica sta in chi scrive il
flow, non nel flow. La prima volta usa `dry_run=True`, che valida forma, nomi
dei tool e riferimenti senza eseguire niente.

> **PyYAML è una dipendenza opzionale.** JSON è un sottoinsieme di YAML, quindi
> i flow in JSON funzionano con l'installazione nuda; quelli in YAML chiedono
> `pip install pyyaml` invece di fallire dentro il parser.

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
| `preset_fab_status` | Se il ponte verso Epic è utilizzabile: client installato, login fatto. | — |
| `preset_fab_list_vault` | Contenuti Fab/Marketplace acquistati, filtrabili per titolo. | la tua licenza Epic |
| `preset_fab_download` | Scarica un pack acquistato sul disco, senza installarlo. | la tua licenza Epic |
| `preset_fab_install` | Scarica **e installa** un pack nel progetto, poi rilegge l'Asset Registry. | la tua licenza Epic |

> **Avvertenza su Fab.** I contenuti acquistati stanno dietro il login Epic e non
> hanno API pubblica. Questi tool si appoggiano al client community
> [`legendary`](https://github.com/derrod/legendary) (`pip install legendary-gl`,
> poi `legendary auth`). È software di terze parti, non una via ufficiale.
>
> A `preset_fab_install` non serve se il pack ce l'hai già: passagli una cartella o
> uno zip invece dell'app name, e installa quello che l'Epic Games Launcher o la
> finestra Fab dell'editor hanno già scaricato.

L'installazione legge com'è fatto il pack invece di indovinarlo: nei pack del vault
il `Content` può stare sotto `data/`, dentro una cartella con la versione del
motore, oppure non esserci affatto perché è un plugin con il suo `.uplugin`. Il
contenuto finisce in `Content/<subfolder>` (cioè `/Game/<subfolder>`), i plugin in
`Plugins/` e vengono abilitati nel `.uproject`; `Binaries` e `Intermediate`
precompilati restano fuori, perché appartengono a un'altra build. L'Asset Registry
viene riletto sui path nuovi, così gli asset compaiono nel Content Browser senza
riavviare — un **plugin**, invece, viene caricato solo all'avvio, e se ha `Source`
C++ va prima compilato (`ue_build_start`).

```
preset_fab_status -> preset_fab_list_vault(query="soul") -> preset_fab_install("SoulCity")
```

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

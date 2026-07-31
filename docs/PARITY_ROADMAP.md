# Roadmap di parità con ue-mcp (db-lyon)

Obiettivo: coprire le 21 categorie / 569+ azioni di [db-lyon/ue-mcp](https://github.com/db-lyon/ue-mcp),
mantenendo l'architettura di questo repo (tool `@mcp.tool()` in `server.py` che
generano snippet eseguiti dentro l'editor via helper `mcp_*` in `ue_side.py`).

Nessun editor era raggiungibile nella sessione in cui è stata scritta questa
roadmap (`ue_status` → nessuna risposta sul multicast): l'implementazione di
ogni fase procede "best effort" sulla base della Python API di UE nota, e va
**validata contro un editor reale** appena disponibile, con lo stesso
approccio già documentato in CHANGELOG 0.6.0 (i fallimenti veri si vedono solo
lì, non nella suite con `fake_unreal`).

## Copertura attuale (v0.6.0, 72 tool)

| Categoria db-lyon | Stato qui | Tool esistenti |
|---|---|---|
| Levels / Actors | Buono | `ue_spawn_actor`, `ue_spawn_many`, `ue_list_actors`, `ue_set_actor_transform`, `ue_set_actor_property`, `ue_attach_actor`, `ue_detach_actor`, `ue_actor_hierarchy`, `ue_delete_actor`, `ue_new_level`, `ue_open_level` |
| Blueprints (creazione/componenti/variabili) | Buono | `ue_create_blueprint`, `ue_add_component`, `ue_add_variable`, `ue_set_class_defaults`, `ue_compile_blueprint`, `ue_reparent_blueprint` |
| Blueprints (node graph) | **Assente per scelta esplicita** | Bypassato via `ue_cpp_class_create` → build → `ue_reparent_blueprint`; vedi nota architetturale sotto |
| Materiali | Buono | `ue_create_material`, `ue_create_material_instance`, `ue_assign_material` (grafo materiale pienamente scriptabile) |
| Asset (CRUD, import) | Buono | `ue_import_assets`, `ue_list_assets`, `ue_delete_asset`, `ue_rename_asset`, `ue_duplicate_asset`, `ue_make_folder` |
| Audio | Oltre db-lyon | `ue_import_audio`, `ue_create_metasound_source`, `ue_create_sound_cue` |
| Editor / viewport / PIE / sequencer render | Buono | `ue_console_command`, `ue_exec_python`, `ue_screenshot`, `ue_get_camera`, `ue_set_camera`, `ue_focus_actor`, `ue_configure_pie`, `ue_start_pie`, `ue_stop_pie`, `ue_render_sequence`, `ue_render_status` |
| Build/package/progetto (locale) | Oltre db-lyon | `ue_engine_list`, `ue_project_create`, `ue_editor_open/close`, `ue_build_start/status`, `ue_package_start/status`, `ue_cpp_class_create`, `ue_live_compile` |
| Download asset gratuiti | Oltre db-lyon | `preset_search_polyhaven`, `preset_download_polyhaven`, `preset_search_ambientcg`, `preset_download_ambientcg`, `preset_download_kenney`, `preset_fab_*`, `preset_download_url`, `preset_extract_archive` |
| Networking | Minimo | Solo `ue_set_replication` (dormancy/relevancy/net priority mancanti) — colmato dalla fase 8 |
| **Animazione** (anim BP, montage, blendspace, skeleton) | **Assente** | — |
| **VFX / Niagara** | **Assente** | — |
| **Landscape** | **Assente** | — |
| **PCG** | **Assente** | — |
| **Gameplay: fisica/collisione/navmesh/behavior tree/EQS/percezione** | **Assente** | — |
| **GAS** (Gameplay Ability System) | **Assente** | — |
| **UI / UMG** (widget, editor utility widget) | **Assente** | — |
| **Reflection** (classi/struct/enum, gameplay tag) | **Assente** | — |

## Fasi

Ordine pensato per valore/complessità, non per numero di categoria db-lyon.
Ogni fase = tool nuovi in `server.py` + helper `mcp_*` in `ue_side.py` + test
con `fake_unreal` + riga in `docs/TOOLS.md` + voce in `CHANGELOG.md`.

1. **Reflection & introspezione** — ✅ fatta il 31/07/2026. `ue_find_classes`,
   `ue_find_structs`, `ue_reflect_enum`. Niente listing generico di
   proprietà/funzioni: non esiste nella Python API di UE (verificato dal
   vivo), solo attraversamento di gerarchia classi/struct + valori enum
   nativi.
2. **UI / UMG** — ✅ fatta il 31/07/2026, ma ridimensionata: `ue_create_widget_blueprint`
   crea l'asset (anche Editor Utility Widget), non il layout. Il `WidgetTree`
   è una proprietà protetta nella Python API — stesso muro del punto 3 sotto,
   scoperto verificando dal vivo prima di scrivere codice. Layout a mano nel
   Widget Designer; logica via classe C++ parent con `BindWidget`.
3. **Blueprint node graph** — ✅ fatta il 31/07/2026, ridimensionata come
   previsto dopo la fase 2: `ue_bp_list_graphs`, `ue_bp_list_events`,
   `ue_bp_add_event_override`, `ue_bp_add_function_graph`. Confermato dal
   vivo che `Nodes` di `EdGraph` è protetta come il `WidgetTree` — niente
   nodi arbitrari (Print String, Branch, chiamate a funzione libere), niente
   elenco nodi di un grafo qualunque. Un modo per collegare pin esiste
   (`try_create_connection`, testato funzionante) ma è stato scartato: gli
   unici nodi raggiungibili sono nodi evento, che hanno solo pin di output
   (verificato anche su un evento con 8 parametri) — nessun pin di input da
   nessuna parte, quindi nessuna connessione valida possibile con gli
   strumenti disponibili. Il workaround C++ resta la via per la logica vera.
4. **Animazione** — ✅ fatta il 31/07/2026, e senza ridimensionamenti:
   `ue_skeleton_info`, `ue_anim_sequence_info`, `ue_create_blend_space_1d`,
   `ue_create_anim_montage`, `ue_create_anim_blueprint`. Sorpresa positiva
   rispetto alle fasi 2-3: i dati di BlendSpace (`BlendParameters`,
   `SampleData`) sono array di struct ordinari, non protetti — scrivere
   funziona davvero, verificato dal vivo su asset reali del progetto
   (Remy_Skeleton, BS_Remy_Locomozione) salvando e ricaricando da zero.
   L'AnimGraph di un Anim Blueprint resta comunque un EdGraph come gli
   altri: `ue_create_anim_blueprint` crea solo l'asset.
5. **Niagara / VFX** — ✅ fatta il 31/07/2026, forma identica alla fase 2/3:
   `ue_create_niagara_system`, `ue_niagara_system_info`. `EmitterHandles` di
   `NiagaraSystem` protetta, confermato anche su template popolati del
   motore — niente authoring dell'emitter stack. Ma
   `NiagaraFunctionLibrary.get_all_emitters`/`get_all_user_parameters`
   leggono davvero un sistema esistente a livello di asset (niente PIE
   necessario) — introspezione confermata su un template reale con 2
   emitter.
6. **Gameplay** — ✅ fatta il 31/07/2026. `ue_set_component_physics`,
   `ue_component_physics_info`, `ue_nav_rebuild`, `ue_nav_query_point`,
   `ue_nav_find_path`, `ue_create_blackboard`, `ue_blackboard_add_key`,
   `ue_blackboard_info`, `ue_create_behavior_tree`, `ue_bt_add_node`,
   `ue_bt_add_decorator`, `ue_bt_add_service`, `ue_bt_set_node_property`,
   `ue_bt_info`, `ue_create_eqs_asset`. Fisica/collisione e navmesh
   pienamente scriptabili. Sorpresa della fase: a differenza di
   UMG/Blueprint/Niagara, l'albero di un Behavior Tree (RootNode, Children,
   Decorators sul child link, Services sui composite) è davvero scrivibile
   via Python — persistenza confermata risalvando e ricaricando l'asset.
   EQS resta bloccato come gli altri grafi (solo asset vuoto). AI Perception
   si aggiunge con `ue_add_component` generico, ma `SensesConfig` va
   configurato a mano (stesso limite di WidgetTree/EdGraph).
7. **GAS** — ✅ fatta il 31/07/2026. `ue_create_gameplay_ability`,
   `ue_create_gameplay_effect`, `ue_ge_add_modifier`, `ue_ge_add_component`,
   `ue_ge_info`. `PIANO_GIOCO.md` conferma che il gioco prevede davvero
   abilità con cooldown ("Strato 2", Fase 5 del piano). Il plugin
   `GameplayAbilities` non era abilitato: abilitato via
   `ue_project_set_plugins` e verificato con l'editor riavviato — **resta
   abilitato nel progetto**. GameplayEffect e AttributeSet sono Blueprint
   "normali", già coperti da `ue_create_blueprint` generico; GameplayAbility
   ha un asset dedicato (`GameplayAbilityBlueprint`, `GameplayAbilitiesBlueprintFactory`).
   Aggiungere un attributo (`GameplayAttributeData`) a un AttributeSet
   funziona già con `ue_add_variable` (path completo come `sub_type`).
   **Il muro trovato inizialmente** — `GameplayModifierInfo.Attribute`/
   `.ModifierOp` "cannot be edited on instances", `GameplayAttribute.AttributeName`
   read-only, l'unico modo normale di collegare un modifier a un attributo
   bloccato — **è stato superato**: costruendo l'intero struct
   `GameplayModifierInfo` con `import_text` in una volta sola (stessa tecnica
   già in uso per `EdGraphPinType`), il parser testuale bypassa la
   restrizione sulla singola proprietà. Verificato dal vivo end-to-end:
   modifier reale (attributo `Health`, `ModifierOp=AddBase`, magnitudine
   -10) su un GameplayEffect vero, salvato e ricaricato da zero — c'era
   davvero. Non verificato in PIE, solo la persistenza sull'asset.
   `GEComponents` scrivibile allo stesso modo delle fasi precedenti.
   L'AnimGraph/EventGraph dell'abilità resta un grafo Blueprint normale, non
   scriptabile — stesso limite di sempre.
8. **Networking esteso** — ✅ fatta il 31/07/2026, senza ridimensionamenti.
   `ue_set_net_config`, `ue_net_info`, `ue_set_component_replication`,
   `ue_set_component_default`. Dormancy, frequenze di update, priorità,
   cull distance e relevancy sono tutte `get/set_editor_property` normali
   sulla CDO: nessun muro, persistenza verificata rileggendo i nomi delle
   proprietà dentro il `.uasset` salvato. **La fase ha anche riaperto un
   limite chiuso male nella fase 6**: il template di un componente di
   Blueprint *è* raggiungibile, via
   `SubobjectDataBlueprintFunctionLibrary.get_object` sul dato ottenuto da
   `k2_find_subobject_data_from_handle`. Quindi `SensesConfig` di un
   AIPerceptionComponent si scrive da Python (verificato dal vivo con una
   config Sight 1500/1800, salvata e ritrovata nel `.uasset`), e
   `ue_set_component_default` lo generalizza a qualunque proprietà
   `EditDefaultsOnly` di qualunque componente.
9. **Landscape** — ✅ fatta il 31/07/2026, ed è la fase più ridimensionata
   di tutte. `ue_landscape_list`, `ue_landscape_info`,
   `ue_landscape_import_heightmap`, `ue_landscape_import_weightmap`,
   `ue_landscape_export_heightmap`, `ue_landscape_set_material`,
   `ue_landscape_set_grass`. **Creare** un landscape da Python non si può:
   `spawn_actor_from_class(unreal.Landscape)` dà un `LandscapePlaceholder`
   vuoto, e `LandscapeSubsystem`/`LandscapeEditorObject`/
   `ActorFactoryLandscape` esistono nel motore (trovate con `ClassIterator`)
   ma non sono esposte al Python. Su un landscape già creato con Landscape
   Mode invece funziona tutto. Il ponte file immagine → render target
   (`import_file_as_texture2d` → `Canvas.draw_texture`) è verificato dal
   vivo pixel per pixel; **l'ultimo anello no**: in quattroCantoni non
   esiste nessun landscape su cui provare
   `landscape_import_heightmap_from_render_target`, e Python non può
   crearne uno per provare. È l'unico pezzo di tutta la roadmap consegnato
   senza verifica dal vivo, ed è marcato come tale anche in `docs/TOOLS.md`.
10. **PCG** — ✅ fatta il 31/07/2026, ed è **la sorpresa della roadmap**.
    `ue_create_pcg_graph`, `ue_pcg_add_node`, `ue_pcg_connect`,
    `ue_pcg_disconnect`, `ue_pcg_remove_node`, `ue_pcg_set_node_property`,
    `ue_pcg_graph_info`, `ue_pcg_spawn_volume`, `ue_pcg_generate`,
    `ue_pcg_cleanup`. Dopo fase 2, 3 e 5 ci si aspettava l'ennesimo grafo
    protetto; invece il grafo PCG è pienamente scriptabile — nodi, archi,
    posizioni, proprietà — con persistenza confermata salvando e
    ricaricando l'asset da zero, e con PCGVolume/`generate`/`cleanup`
    provati su un volume vero nel livello. Il motivo è lo stesso dei
    Behavior Tree: `UPCGNode`/`UPCGEdge` sono oggetti dati veri, non un
    `UEdGraph` di nodi K2 con il contenuto dietro una proprietà protetta.

Landscape e PCG erano in coda perché quattroCantoni non è, allo stato,
dichiarato un progetto open-world/procedurale. È esattamente il motivo per
cui la fase 9 è l'unica non verificabile fino in fondo: senza un terreno nel
progetto non c'è niente su cui provare l'ultima chiamata.

## Stato finale (10 fasi su 10, 127 tool)

Le 21 categorie di db-lyon sono coperte, con tre tipi di esito diversi e
tutti dichiarati:

| Copertura | Categorie |
|---|---|
| Piena, verificata dal vivo | Levels/Actors, Blueprint (dati), Materiali, Asset, Audio, Editor/viewport/PIE/render, Build/package, Reflection, Animazione (dati), Gameplay (fisica, navmesh, Blackboard, Behavior Tree), GAS, Networking, **PCG** |
| Parziale per un limite reale della Python API di UE | Grafo Blueprint, UMG, Niagara, EQS — creabili come asset, non authorabili nel grafo/albero |
| Parziale per impossibilità di creazione | Landscape — guidabile ma non creabile da Python |

Quello che resta fuori non è un pezzo di roadmap non fatto: è il muro dei
sistemi node-graph-based del motore (`EdGraph.Nodes`, `WidgetTree`,
`EmitterHandles`, `EnvQuery.Options` — tutte proprietà protette), verificato
caso per caso e non assunto. La via per la logica vera resta il workaround
C++ (`ue_cpp_class_create` → build → `ue_reparent_blueprint`).

## Nota architetturale: perché il node graph blueprint è stato evitato finora

`server.py` lo dichiara esplicito nelle istruzioni del server: i grafi
Blueprint *non* sono scriptabili con l'approccio attuale, e si aggira il
limite mettendo la logica in una classe C++ (`ue_cpp_class_create` → build →
`ue_reparent_blueprint`). La fase 3 tenta di chiudere questo gap in modo
diretto; se la Python API di UE non lo permette in modo affidabile, il
workaround C++ resta la via consigliata e va lasciato documentato come tale.

**Aggiornamento dopo la fase 2**: la fase 2 ha trovato lo stesso identico
muro sul `WidgetTree` di UMG (`get_editor_property` su una proprietà protetta
dell'engine, non sui binding Python — non è un caso isolato dei grafi
Blueprint). Aspettativa realistica per la fase 3: è probabile che anche il
grafo Blueprint (nodi K2, pin, edge) sia protetto allo stesso modo, e che la
fase si concluda confermando il workaround C++ come via definitiva invece di
aggiungere tool di editing diretto. Da verificare dal vivo, non da
assumere.

**Aggiornamento dopo la fase 3**: confermato, stesso muro (vedi sopra). Ma
**la fase 4 ha rotto il pattern**: i dati di animazione (BlendSpace,
AnimSequence via `AnimationLibrary`) sono array di struct ordinari, non
protetti — scrivere funziona davvero. La proprietà protetta non è quindi
universale, è specifica dei sistemi node-graph-based (Blueprint, Material —
quest'ultimo però pienamente scriptabile per altra via — e UMG). Prima di
assumere che una fase futura sia bloccata o sbloccata, va sempre verificato
dal vivo caso per caso: né "tutto è protetto" né "niente è protetto" sono
assunzioni sicure.

**Aggiornamento a roadmap chiusa (fasi 8-10)**: la regola "verifica caso per
caso" ha pagato altre due volte. La fase 8 ha smentito una conclusione della
fase 6 — il template di un componente di Blueprint *è* raggiungibile, e con
esso le proprietà `EditDefaultsOnly` date per non scrivibili. E la fase 10 ha
smentito l'aspettativa generale: il grafo PCG, che sembrava il candidato più
ovvio a essere protetto, è il più scriptabile di tutti. Il discrimine non è
"grafo sì / grafo no": è se sotto c'è un `UEdGraph` di nodi K2 (protetto —
Blueprint, UMG, Niagara, EQS) o un grafo di oggetti dati (scrivibile —
Behavior Tree, PCG).

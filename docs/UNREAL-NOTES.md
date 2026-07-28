# Unreal 5.8 automation notes

Things that cost real time to discover while building this. Every item below was
verified against a running UE 5.8, not inferred from documentation.

## Remote Control has two independent security gates

Enabling the plugin is not enough. Both of these are needed, and they fail with
different messages, one after the other:

```ini
; Config/DefaultRemoteControl.ini
[/Script/RemoteControlCommon.RemoteControlSettings]
bEnableRemotePythonExecution=True
bAllowAnyRemoteFunctionCall=False
+CustomAllowedRemoteFunctionCalls=(ClassPath="/Script/PythonScriptPlugin.PythonScriptLibrary")
```

- Without `bEnableRemotePythonExecution` the **object** is refused:
  `Object Default__PythonScriptLibrary cannot be accessed remotely`
  (see `FRemoteControlModule::CanBeAccessedRemotely`).
- Without the allowlist the **function call** is refused:
  `Executing function 'ExecutePythonCommandEx' is not allowed`
  (see `WebRemoteControlInternalUtils::ValidateFunctionCall`).

The targeted allowlist is preferable to `bAllowAnyRemoteFunctionCall=True`, which
would expose every `UFUNCTION` in the project to HTTP calls.

A third flag, `bAllowConsoleCommandRemoteExecution`, looks like it belongs in
that set and does not. Epic documents it as *"Enable calling
'ExecuteConsoleCommand' through the web api"* — it gates one HTTP route, not
console commands in general. Code running inside the editor, including
`unreal.SystemLibrary.execute_console_command`, is unaffected, so a bridge that
executes Python and issues its console commands from there can leave it off.

Note the file: `URemoteControlSettings` is declared `UCLASS(config = RemoteControl)`
in the `RemoteControlCommon` module, so it reads **`DefaultRemoteControl.ini`** —
putting these keys in `DefaultEngine.ini` does nothing. Both changes need an
editor restart.

## Live Coding keeps a lock after the editor closes

`LiveCodingConsole.exe` survives the editor. A build then fails with
`Unable to build while Live Coding is active` even though nothing is open. Kill
the process before compiling.

Live Coding itself is usable from the bridge (`LiveCoding.Compile` as a console
command) and is the fast path for iteration — but it only patches function
bodies. Adding or changing `UCLASS`, `UFUNCTION` or `UPROPERTY` alters reflection
data and requires a full rebuild with the editor closed.

## Copying a project rebinds the engine by GUID

Duplicate a project folder and reopen it, and Unreal rewrites
`EngineAssociation` from `"5.8"` to a GUID like `{129D8DB7-...}`. That GUID maps
to a path under `HKCU\SOFTWARE\Epic Games\Unreal Engine\Builds`, where the
**values** are `GUID = path` — unlike `HKLM\SOFTWARE\EpicGames\Unreal Engine`,
whose *subkeys* hold an `InstalledDirectory` value. Two different shapes; code
reading only the second finds nothing.

## Blueprint variables: use the official API

`UBlueprint.NewVariables` is not exposed to Python. The supported route is:

```python
unreal.BlueprintEditorLibrary.add_member_variable(blueprint, unreal.Name(name), pin_type)
unreal.BlueprintEditorLibrary.set_blueprint_variable_replication(blueprint, name, mode)
unreal.BlueprintEditorLibrary.set_blueprint_variable_instance_editable(blueprint, name, True)
```

## EdGraphPinType is built with import_text

Its properties are not exposed — `set_editor_property("pin_category", ...)`
fails. Build it from the struct serialisation format instead:

```python
pin = unreal.EdGraphPinType()
pin.import_text('(PinCategory="real",PinSubCategory="double")')          # float
pin.import_text('(PinCategory="object",PinSubCategoryObject="/Script/Engine.Actor")')
pin.import_text('(PinCategory="struct",PinSubCategoryObject="/Script/CoreUObject.Vector")')
```

## Blueprint node graphs cannot be authored from Python

`EdGraph.Nodes` is protected, `EdGraphPin` is not exposed, K2 node classes can be
instantiated but not inserted into a graph, and there is no pin-linking API.
Variables, components and defaults are scriptable; **logic is not**.

The way around it is not to script the graph but to avoid needing one: generate
a C++ parent class, compile, and reparent the Blueprint onto it. The Blueprint
keeps its components and its editor-set values; the behaviour is inherited.
`UFUNCTION(BlueprintCallable)` members then show up as nodes a human can wire.

## Material graphs, unlike Blueprint graphs, *are* scriptable

`MaterialEditingLibrary` exposes the whole thing — `create_material_expression`,
`connect_material_property`, `recompile_material`, and the material-instance
parameter setters. Nodes can be created, positioned and linked from Python.

One trap: a normal map needs both `srgb = False` on the texture and
`SAMPLERTYPE_NORMAL` on the sampler node. Set only one and the map is sampled as
colour, which looks subtly wrong rather than obviously broken.

## Parent class headers are not all under GameFramework/

`AIController.h` sits at the root of the `AIModule` include path, components
live under `Components/`, `UObject` under `UObject/Object.h`. Getting this wrong
produces a compiler error that never names the class you were trying to derive
from.

The `A`/`U` prefix cannot be derived by stripping the first letter either:
`ActorComponent` starts with an `A` but the class is `UActorComponent`. The
prefix follows the hierarchy — `A` for `AActor` descendants, `U` for every other
`UObject` — so it needs a lookup, not string surgery.

## Replicated properties fail silently without DOREPLIFETIME

Marking a `UPROPERTY` as `Replicated` and forgetting to add it to
`GetLifetimeReplicatedProps` compiles without complaint. The value simply never
replicates, and the bug surfaces much later as desync.

## Sending helpers with every call is expensive

`ExecutePythonCommandEx` with `fileExecutionScope: "Private"` runs each snippet
in a fresh scope, but the *process* is shared: `sys.modules` persists between
calls. Installing helpers once as a module and importing them afterwards turns
every call from "parse and execute 750 lines" into "look up a dict".

Key it by a hash of the source, and have the lightweight snippet report back
when the module is missing or stale rather than assuming — the editor can
restart underneath you at any point.

## Edits from a script are not undoable unless you ask

Anything done through the Python API lands outside the transaction buffer, so
Ctrl+Z does nothing. Wrapping the work in `unreal.ScopedEditorTransaction`
registers it as one named entry in the undo stack. For an agent editing someone
else's level, this is the difference between a tool you can trust and one you
cannot.

## Blueprint components live in the subobject system

Components added to a Blueprint are not readable as properties on the CDO. Go
through `SubobjectDataSubsystem`, and expect the gather call to return the same
component more than once — deduplicate by name:

```python
subsystem = unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)
for handle in subsystem.k2_gather_subobject_data_for_blueprint(blueprint):
    data = subsystem.k2_find_subobject_data_from_handle(handle)
    component = unreal.SubobjectDataBlueprintFunctionLibrary.get_object(data)
```

## LevelEditorPlaySettings is reachable but partly unwritable

The class is missing from the `unreal` module; the CDO is still reachable, and
its properties answer to **PascalCase** names only:

```python
settings = unreal.load_object(None, "/Script/UnrealEd.Default__LevelEditorPlaySettings")
settings.set_editor_property("PlayNumberOfClients", 5)     # works
settings.set_editor_property("PlayNetMode", 1)             # fails: ByteProperty on an unexposed enum
```

`EPlayNetMode` is not exposed at all, so the net mode has to go through
`Config/DefaultEditorPerProjectUserSettings.ini` and takes effect on the next
editor start.

## Reparenting a Blueprint onto a C++ class is clean

If the Blueprint has variables with the same names as the new parent's
`UPROPERTY`s, Unreal absorbs the exact matches and renames the rest with a `_0`
suffix. `remove_unused_variables` then clears the leftovers, since a Blueprint
with no graphs uses none of them.

## Unreal compiles with warnings as errors

Shadowing an inherited member is fatal. Naming a local variable `Character`
inside an `AController` subclass fails the build:

```
error C4458: declaration of 'Character' hides class member
```

## Redirecting a build log through cmd is fragile

Paths contain spaces and nested quoting through `cmd /c "... > file"` silently
loses the redirection, leaving an empty log while the build appears to do
nothing. Write a small `.bat`/`.sh` wrapper and run that instead.

## Reading the log can eat your own output

If the bridge frames results with sentinels and a tool returns log lines, those
lines may contain sentinels from previous calls. Search from the **last**
sentinel backwards, and neutralise the markers in returned log text.

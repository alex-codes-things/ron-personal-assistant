# Ron v0.9 Agent Core

Ron v0.9 turns the existing safe tool runner into a more cohesive assistant brain without replacing the wake word, voice, face, reminders, Spotify, or Ron Network systems.

## Architecture

```text
User / Voice
    |
AgentCoreRouter
    |
AgentCorePlanner
    |---- SkillCatalog
    |---- deterministic rules
    |---- bounded local-model plan (max 4 steps)
    |
PermissionAwareRegistry
    |
AgentCoreService
    |---- WorkingMemory
    |---- AgentTaskManager (persistent background tasks)
    |---- ManagedProcessManager (real OS processes Ron started)
    |
Approved tools
    |---- system / apps / media / files / reminders
    |---- workspace
    |---- Ron Network devices
```

## Skills

Skills are named bundles over the existing allow-listed tools. The initial catalog includes:

- `system`
- `volume`
- `apps`
- `browser`
- `music`
- `spotify`
- `files`
- `git`
- `reminders`
- `workspace`
- `tablet`
- `network`

A skill does not bypass the tool registry. Every individual action still goes through argument validation, preflight, permission checks, timeouts, cancellation and result bounding.

## Working memory

`runtime/data/working_memory.json` stores a tiny, expiring context window for recent executable context only:

- latest task ID
- latest managed process ID
- active workspace
- recent successful tool plan
- recent target such as an app/folder/device

The default TTL is six hours. It is deliberately not a second permanent chat-history database.

This enables phrases such as:

- `How's that task?`
- `Cancel the last task.`
- `Do that again.`
- `Run the tests.`

## Workspace skill

The Ron workspace skill can:

- read Git branch/dirty state
- detect ADB/Nexus availability when ADB is installed
- read the Ron Face state from Ron Network
- open the repository in VS Code (or the project folder as fallback)
- start `pytest -q` as a tracked process

Useful requests:

- `Prepare my workspace.`
- `Open the Ron project.`
- `Check the repo status.`
- `Run the tests.`
- `How are the tests doing?`
- `Stop that test run.`

## Background work

There are two different concepts on purpose:

1. **AgentTaskManager** — persists and executes bounded multi-step assistant tasks one at a time.
2. **ManagedProcessManager** — tracks actual local processes Ron started, such as a test run.

Process logs are written below `runtime/logs/processes/`.

## System awareness

The existing battery/performance tools remain the trusted source for CPU, memory and disk state. Agent Core also adds a bounded read-only view of heavyweight Windows processes, plus deterministic routing for conversational questions such as:

- `Why are the fans so loud?`
- `Can I run a game without stopping you?`

Ron checks live performance state instead of guessing.

## Ron Network

The new `get_network_devices` tool exposes the existing trusted Ron Network registry to the agent. It can report device state and capabilities without granting a discovered device control authority.

Examples:

- `Is the Nexus connected?`
- `What Ron devices are online?`
- `Check Ron Network status.`

## Permission levels

The existing `ToolRisk` remains the low-level safety signal. Agent Core translates it into three user-facing permission tiers:

| Tier | Typical actions | Confirmation |
| --- | --- | --- |
| Safe | read state, volume, reversible local controls | normally no |
| Moderate | apps, services, external/process actions | normally no |
| Sensitive | destructive/high-impact actions | always yes |

`PermissionAwareRegistry` enforces confirmation for sensitive actions even if a future tool author forgets to set `requires_confirmation=True`.

## Failure behavior

Agent Core keeps the current fail-safe rules:

- unknown tools are rejected
- all arguments are schema validated
- all steps preflight before a multi-step task starts
- sensitive actions require confirmation
- local-model plans cannot invent tool names
- workspace commands never accept arbitrary shell input from the user
- network discovery does not grant trust
- working-memory failure never blocks Ron
- process logs stay under `runtime/`

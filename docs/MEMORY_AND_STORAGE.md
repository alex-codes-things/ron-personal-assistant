# Ron Memory and Storage

Ron 0.5 builds memory intelligence on top of the drive-aware storage foundation. The
external drive expands Ron's long-term memory but is never required for Ron to start,
recall compact indexed facts, or perform normal local work.

## Memory layers

1. **Working memory** remains in RAM and Ron's bounded chat history.
2. **Core memory** lives under `runtime/memory/core/` on the laptop and contains only
   small indexes, settings, storage identity and facts that must remain available offline.
3. **Long-term memory** lives on the external `RON_STORAGE` volume. Full explicit facts,
   project knowledge, experiences, optional episodic conversations and visual-memory
   records are stored there. Normal chat turns are not blindly archived anymore.

## External-drive identity

Ron does not trust a Windows drive letter. The first initialized storage drive receives
`.ron-storage.json` with a random persistent `storage_id`. The laptop remembers that ID in
`runtime/memory/core/storage_binding.json`.

If another drive is later presented as `RON_STORAGE`, Ron refuses to write to it when the
identity does not match the bound drive.

Run the one-time initializer from the project environment:

```powershell
python scripts/setup_storage.py E:\
```

If the original HDD is permanently being replaced, rebind only as an explicit recovery
action. Ron keeps a backup of the previous local binding and restores it automatically if
the new drive fails verification:

```powershell
python scripts/setup_storage.py F:\ --rebind
```

`--rebind` changes which drive Ron trusts; it does **not** copy memory from the old drive.

You can rename the Windows volume to `RON_STORAGE` so Ron can discover it automatically.
Alternatively set `RON_STORAGE_PATH` to the drive root.

## Disconnect and recovery behavior

Storage exposes four states:

- `online` — external long-term memory is healthy.
- `degraded` — drive is missing or a write failed; Ron continues locally.
- `recovering` — the drive returned and queued writes are being verified/synced.
- `error` — the drive was found but failed identity, read/write, or recovery checks.

When the drive is unavailable, long-term writes are atomically saved under
`runtime/memory/storage_queue/`. The queue defaults to 512 MiB and is intentionally
bounded so a forgotten HDD cannot silently fill the laptop SSD.

On reconnect Ron verifies the queue checksum, atomically writes the external file,
verifies the destination checksum, updates the queue database, and only then removes the
local fallback copy.

Forget operations use the same disconnect-safe idea. If a memory is forgotten while the
HDD is offline, Ron removes it from the local catalog immediately and stores a deletion
tombstone in `queue.sqlite`. The bound drive is deleted from only after it reconnects. A
new write to the same path cancels an older deletion tombstone, preventing delete/write
races.

## Long-term external layout

```text
RON_STORAGE/
├── .ron-storage.json
├── Memory/
│   ├── Conversations/
│   ├── Knowledge/
│   ├── People/
│   ├── Projects/
│   ├── Experiences/
│   └── Archives/
├── Visual_Memory/
│   ├── Screenshots/
│   │   ├── Coding/
│   │   ├── Applications/
│   │   ├── Errors/
│   │   └── General/
│   ├── Thumbnails/
│   └── Analysis/
├── AI/
│   ├── Models/
│   ├── Embeddings/
│   ├── Voice/
│   └── Vision/
├── Devices/
├── Logs/
├── Backups/
└── System/
```

## Local layout

```text
runtime/memory/
├── core/
│   ├── core_memory.sqlite
│   ├── memory_catalog.sqlite
│   └── storage_binding.json
└── storage_queue/
    ├── queue.sqlite
    └── objects/
```

The local catalog stores compact searchable metadata, not the full long-term archive.
That lets Ron find useful earlier context while the external drive is connected and still
retain a small amount of useful indexing when it is not.

Active AI and voice models intentionally remain in the laptop's existing `runtime/models/`
path. Moving live models to a mechanical external drive would make Ron slower and make
voice/AI availability depend on the USB cable. The external `AI/` folders are reserved for
future model archives, backups and optional cold storage.

## Memory intelligence

Explicit memory commands are deterministic and do not need an LLM round trip:

- `Remember that my amp is a Fender Mustang LT25`
- `What do you remember about my amp?`
- `What do you remember?`
- `Forget about the Fender Mustang` (requires a confirmation before deletion)

Terminal shortcuts are also available: `/memories`, `/remember TEXT`, `/recall QUERY` and
`/forget QUERY`. The same natural-language phrases work through voice because microphone
input uses the same `RonAssistant` entry point.

Normal conversation uses conservative automatic learning by default. Ron can promote a
small set of high-signal user statements such as stable equipment facts, preferences,
instruments, active projects and explicit Ron project requirements. Automatic learning:

- learns only from the user's text, never from Ron's generated answer;
- refuses to persist password/API-key/token/PIN-like secrets;
- ignores obviously temporary facts such as "today", "right now" or "for now";
- deduplicates exact durable memories;
- stores learned facts at lower importance than explicit `remember` commands.

Set `RON_MEMORY_AUTO_LEARN=off` to disable automatic learning completely. Explicit memory
commands still work.

For ordinary chat, the memory catalog searches only durable knowledge, people, project and
experience records. Conversation archives are excluded from automatic context, and generic
prompts such as "hello" do not cause recent memories to be injected. When the HDD is
offline, explicit recall can still return the compact local summary and clearly marks that
the full record is unavailable.

## Visual memory foundation

`VisualMemoryService` already accepts PNG/JPEG/WebP screenshot bytes plus metadata such
as application, window title, project, problem-solving session, category, tags, summary
and analysis. Image files and JSON analysis are stored separately so large image blobs
never live inside SQLite. Both sides can be read back with checksum verification.

`MemoryService.remember_experience()` can link one or more visual-memory IDs to a resolved
problem and solution. That is the foundation for a later coding flow such as: capture an
error, inspect attempts, mark the problem solved, then reuse the successful experience
when a similar error appears again.

The default mode is `on_request`. `off` and `session` are also defined. This release does
**not** implement unattended continuous screen capture. A later capture tool can call the
existing service for commands such as "Ron, look at this" without redesigning storage.

## Environment settings

- `RON_STORAGE_PATH` — optional explicit drive root.
- `RON_STORAGE_QUEUE_LIMIT_MB` — local fallback maximum, default `512`.
- `RON_STORAGE_CHECK_SECONDS` — reconnect check interval, default `10`.
- `RON_STORAGE_MIN_FREE_GB` — external free-space reserve, default `5`.
- `RON_LOCAL_MIN_FREE_MB` — laptop free-space reserve before fallback writes, default `1024`.
- `RON_MEMORY_AUTO_LEARN` — `conservative` (default) or `off`.
- `RON_VISUAL_MEMORY_MODE` — `off`, `on_request`, or `session`.

## Failure rule

Memory is a supporting subsystem. A memory-write failure is logged and surfaced through
storage health, but it must not turn an otherwise successful chat reply or agent action
into a failed user request.

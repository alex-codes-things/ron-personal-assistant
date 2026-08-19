# Ron project structure

The repository is intentionally split into a few human-readable areas.

```text
Ron/
├── ron/          # Ron's Python brain and desktop runtime
├── tablet/       # Native Android app for the Nexus 7 face/tools screen
├── runtime/      # Generated/private data, models, logs and recordings
├── scripts/      # Setup, diagnostics and helper commands
├── tests/        # Automated checks
├── docs/         # Architecture and project notes
├── pyproject.toml
└── README.md
```

## Inside `ron/`

```text
ron/
├── app.py         # Starts/stops every subsystem
├── assistant.py   # One user-facing assistant entry point
├── chat.py        # Conversation history + responses
├── core.py        # Events + coordinator
├── routing.py     # Decides chat vs agent
├── terminal.py    # Terminal interface
├── reminders.py   # Reminder storage/scheduling
├── agent/         # Planning, task execution and approved computer tools
├── ai/            # Ollama/local-model client and inference scheduling
├── display/       # Nexus face state + LAN/USB tablet transport
├── network/       # Device registry, LAN discovery, health + shared protocol
├── integrations/  # External app integrations such as Spotify
└── voice/         # Microphone, wake word, VAD and Whisper transcription
```

The goal is simple: files that are part of Ron's normal runtime live in `ron/`; generated files live in `runtime/`; device code lives in `tablet/`; one-off developer helpers live in `scripts/`.

## Ron Network

The optional `ron/network/` layer discovers and tracks local companion devices without putting network calls in the path of local assistant commands. See [`NETWORK.md`](NETWORK.md).

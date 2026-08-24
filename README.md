# Ron

Ron is a local-first personal assistant built in Python, with a native Android tablet face.
The project is organised so the root stays readable and generated files stay out of the way.

## Project map

- **`ron/`** — Python assistant: Agent Core, AI, chat, voice, network awareness and display connection
- **`tablet/`** — Nexus 7 Android face and quick-action app
- **`runtime/`** — generated/private data, downloaded models, logs and recordings
- **`scripts/`** — setup, diagnostics, benchmarks and installers
- **`tests/`** — automated tests
- **`docs/`** — architecture and structure notes

See [`docs/STRUCTURE.md`](docs/STRUCTURE.md), [`docs/NETWORK.md`](docs/NETWORK.md), and
[`docs/AGENT_CORE.md`](docs/AGENT_CORE.md) for the main architecture notes.

## Ron v0.9 Agent Core

The v0.9 Agent Core adds named skills, short-term working memory, bounded multi-step
planning, managed long-running processes, deeper system awareness, Ron Network device
skills, and human-readable permission levels.

Examples:

```text
I'm going to work on Ron. Get everything ready.
Run the tests.
How are the tests doing?
Why are the fans so loud?
Is the Nexus connected?
How's that task?
```

The existing wake word, voice, face, reminders, Spotify integration, local tools and
Ron Network remain independent. Agent Core coordinates them rather than replacing them.

## Start Ron

Create/activate a Python 3.12 virtual environment, then install Ron in editable mode:

```powershell
python -m pip install -e ".[voice,desktop-preview,dev]"
python -m ron
```

Optional setup helpers:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_voice.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\setup_local_ai.ps1
python .\scripts\setup_spotify.py
powershell -ExecutionPolicy Bypass -File .\scripts\install_tablet_face.ps1
```

## Runtime files

Ron keeps machine-specific/generated content under `runtime/` instead of scattering
`data`, `models`, `logs` and `recordings` across the project root. These files are
intentionally ignored by Git. Agent Core working memory and process logs also stay under
`runtime/`, so they are not committed.

## Tablet

Open the `tablet/` directory in Android Studio if you want to edit the Nexus app directly.
The normal installer is `scripts/install_tablet_face.ps1`.

After pairing, the Nexus can connect over the local LAN. Ron discovers it automatically
when router broadcasts are allowed; `RON_FACE_HOST` can be set as a manual fallback. The
existing USB/ADB transport remains available and local Ron does not depend on the tablet
or network being online.

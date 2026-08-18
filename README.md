# Ron

Ron is a local-first personal assistant built in Python, with a native Android tablet face.
The project is organised so the root stays readable and generated files stay out of the way.

## Project map

- **`ron/`** — Python assistant: agent, AI, chat, voice and display connection
- **`tablet/`** — Nexus 7 Android face and quick-action app
- **`runtime/`** — generated/private data, downloaded models, logs and recordings
- **`scripts/`** — setup, diagnostics, benchmarks and installers
- **`tests/`** — automated tests
- **`docs/`** — architecture and structure notes

See [`docs/STRUCTURE.md`](docs/STRUCTURE.md) for the detailed map.

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

Ron keeps machine-specific/generated content under `runtime/` instead of scattering `data`, `models`, `logs` and `recordings` across the project root. These files are intentionally ignored by Git.

## Tablet

Open the `tablet/` directory in Android Studio if you want to edit the Nexus app directly. The normal installer is `scripts/install_tablet_face.ps1`.

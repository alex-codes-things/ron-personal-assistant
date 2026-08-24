# Runtime files

Ron keeps generated/private files here so the project root stays clean.

- `data/` — reminders, task journal, Spotify tokens/config, tablet pairing
- `models/` — downloaded voice models
- `logs/` — rotating runtime logs
- `recordings/` — temporary/local voice recordings when used
- `network/` — optional generated network cache/state if future devices need it
- `memory/core/` — small local indexes, core facts and the bound storage identity
- `memory/storage_queue/` — verified fallback writes waiting for the external drive

These folders are created automatically or by setup scripts and are ignored by Git.

- `models/voice/tts/` — local Kokoro ONNX speech model and voice vectors downloaded only by the explicit voice setup script.

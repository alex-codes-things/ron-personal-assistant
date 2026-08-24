# Ron v0.13.0 - Conversational Core

This release combines the next voice, command-understanding, progress, context and diagnostics
upgrades without changing Ron's local execution safety model.

## What changed

- Groq Orpheus PCM is played while the HTTPS response is still arriving.
- Windows system speech is a fast emergency fallback before cold Kokoro.
- Strict, follow-up and continuous interaction profiles are explicit; strict is the default.
- The planner receives live allowlisted-tool availability and bounded verified action context.
- Tool progress ends with an honest completed or verified result.
- `/health` reports essential runtime state without running a slow cloud probe.
- Privacy-safe turn timings queue to `RON_STORAGE/Diagnostics/Performance`, even while the drive
  is disconnected. Prompts, transcripts and reply text are never written to that archive.
- A disconnected tablet face remains optional and does not make overall health fail by itself.

## Clean upgrade

1. Stop Ron with `Ctrl+C`.
2. Extract this release into a new `RonAI-v0.13.0` folder.
3. Copy your existing private `.env` and `runtime` folder into the new folder. Do not copy the old
   `.venv`.
4. In PowerShell, create and install a clean Python 3.12 environment:

   ```powershell
   py -3.12 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -e ".[voice,dev]"
   ```

5. Verify Groq speech and start Ron:

   ```powershell
   python .\scripts\check_groq_voice.py --play
   python -m ron
   ```

6. After one spoken command, enter `/latency` and `/health` in Ron's terminal.

No `.env` edit is required for an existing strict v0.12.2 setup. To state the new defaults
explicitly, add:

```dotenv
RON_INTERACTION_MODE=strict
RON_GROQ_TTS_STREAMING=true
RON_TTS_FAST_FALLBACK=true
```

Use `RON_INTERACTION_MODE=followup` only when you intentionally want a brief wake-free reply
window. Use `continuous` only when you intentionally want listening until the safety timeout.

# Ron v0.9.1 - Responsive Actions and Visible Work

This reliability update sits on top of the v0.9 conversational voice engine. It keeps the same
Whisper, Sherpa, Silero, Kokoro, router, safety gates, tool allowlist and storage architecture.

## What changed

- Every request reports concise `[WORKING]` stages in the terminal while Ron understands,
  plans, checks and runs it.
- Queued multi-step jobs continue to report task number, state, current step and completion.
- Opening an allowlisted application and using Windows media controls now happen immediately;
  they are no longer misclassified as long-running background jobs.
- Spotify playback control runs immediately. Track search/selection remains a background task
  because it can take longer and may need clarification.
- `unpause`, `resume`, `continue`, `carry on`, `keep playing` and similar current-media requests
  resolve to playback control without a chat-model detour.
- Unfamiliar short imperative phrasing can be mapped semantically to one approved tool. The
  prepared structured plan is reused after routing, avoiding a second local-model call.
- The cached spoken work cue begins after 0.9 seconds and uses `On it.` or
  `I'm checking that now.`
- Expected tablet reconnect failures are debug-only. The terminal receives one offline state
  change, one reconnection state change, and no repeating offline reminder by default.
- Repeated identical terminal notices are coalesced and the pending notice queue is bounded.

## Install

From the project folder in PowerShell, with Python 3.12 active:

```powershell
python -m pip install -e ".[voice,dev]"
python -m pytest
python -m ron
```

Keep the existing private `.env`. To use the new defaults explicitly:

```dotenv
RON_VOICE_THINKING_CUE=true
RON_VOICE_THINKING_CUE_DELAY=0.9
RON_VOICE_THINKING_CUES=On it.|I'm checking that now.
RON_FACE_REMINDER_MINUTES=0
```

`RON_FACE_REMINDER_MINUTES=0` disables periodic reminders, not the face connection. Set 5-240
only if periodic offline reminders are deliberately wanted. Connection state remains visible in
`/status` and the private rotating debug log remains available for diagnosis.

## Quick acceptance check

1. Start Ron with the tablet disconnected and confirm the terminal receives one face notice,
   not a warning on every reconnect attempt.
2. Say `Hey Ron, unpause the song` and confirm the current media toggles without a model delay or
   background task number.
3. Say `Hey Ron, open calculator` and confirm it opens immediately.
4. Ask for a normal answer and confirm `[WORKING] Understanding your request` appears, followed
   by the current planning/thinking stage.
5. Start a multi-step request and confirm task progress names the active step until completion.

## Storage

This update adds no model downloads and needs no major new space. Keep active voice models on the
laptop SSD for latency. Ron's large hard drive remains available for archives, optional models,
benchmarks, recordings you choose to retain, backups and long-term memory.

## Validation completed for this source package

- 207 automated tests passed.
- Ruff passed across the complete Python source, scripts and tests.
- Python bytecode compilation passed for `ron/` and `scripts/`.

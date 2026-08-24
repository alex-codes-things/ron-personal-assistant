# Ron v0.13.1 - Unified Voice and Clean Terminal

This repair addresses three visible problems: replies stopping at the old 180-character cloud
budget, `bm_george` cues appearing beside the configured Groq voice, and crowded terminal output.

## What changed

- Groq replies may use up to four 180-character parts and 700 speakable characters.
- Complete early sentences still begin synthesis before later model text is finished.
- Each later Groq part is prefetched while the current part is playing.
- The final part is reserved and clipped on a sentence boundary with a terminal handoff message.
- All normal cues use the configured Groq voice and provider-specific persistent cache.
- Old local `bm_george` cache files remain harmless on disk and are no longer selected.
- The terminal now has aligned roles, cleaner progress rows, readable notices and turn spacing.

## Required `.env` values

```dotenv
RON_INTERACTION_MODE=strict
RON_VOICE_AUTO_FOLLOWUP=false
RON_GROQ_TTS_MAX_REQUESTS_PER_TURN=4
RON_GROQ_TTS_STREAMING=true
RON_TTS_FAST_FALLBACK=true
RON_TTS_MAX_CHARACTERS=700
```

`RON_GROQ_TTS_VOICE=daniel` continues to select Daniel for normal replies and every cached cue.
The Windows and Kokoro voices are emergency fallbacks only if Groq fails; they are never used to
prewarm normal acknowledgements.

## Upgrade

1. Stop Ron with `Ctrl+C`.
2. Extract v0.13.1 into a clean folder.
3. Copy the supplied private v0.13.1 `.env` into the project root and rename it to `.env`.
4. Copy your existing `runtime` folder if you want to preserve memories. Do not copy `.venv`.
5. Create a clean Python 3.12 environment and install:

   ```powershell
   py -3.12 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -e ".[voice,dev]"
   python -m ron
   ```

The first run may generate a few short Daniel cues in the background. Later runs load them from
cache immediately.

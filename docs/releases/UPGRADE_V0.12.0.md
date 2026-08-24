# Ron v0.12.0 - Groq Voice

This release targets the measured delays directly:

- local ASR: **13.94 seconds** -> Groq Whisper Turbo
- local first TTS chunk: **7.07 seconds for 11 characters** -> Groq Orpheus
- model answer: already **1.12 seconds** and remains on Groq GPT-OSS 120B

## Upgrade

1. Stop Ron with `Ctrl+C`.
2. Back up your private `.env` and `runtime` folder.
3. Extract this release into a clean `RonAI-v0.12.0` folder.
4. Copy only `.env` and any required `runtime` data/models from the old folder.
5. Activate Python 3.12 and install:

   ```powershell
   python -m pip install -e ".[voice,dev]"
   ```

6. Add the cloud voice variables from `docs/CLOUD_VOICE.md` to `.env`. The existing Groq key is
   reused; no second key or paid OpenAI account is needed.
7. Start Ron:

   ```powershell
   python -m ron
   ```

At startup, Ron prints `[CLOUD VOICE READY]` and explains which parts remain local. `/status`
shows the exact ASR and TTS providers. `/latency` reports the next real turn.

## Important limits

Groq's free allowance is quota-limited, not unlimited. The release uses one cloud TTS request per
reply by default and reads only a useful opening; the complete response remains in the terminal.
If the free allowance or internet is unavailable, the cold local fallback is attempted. Set
`RON_ASR_FALLBACK_LOCAL=false` or `RON_TTS_FALLBACK_LOCAL=false` if the local models have been
removed and you prefer a quick, clear cloud-offline message.

Wake detection, endpointing, normalization, permission checks, confirmations, memory and tool
execution remain local. Only finalized command audio and bounded reply text use Groq.

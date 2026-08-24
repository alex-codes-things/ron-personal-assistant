# Groq cloud voice

Ron v0.13 moves the two measured audio bottlenecks off the laptop while keeping the safety gate
local and playing speech while its response body is still arriving:

1. Sherpa KWS listens locally for **Hey Ron**.
2. Silero VAD detects locally when the command has ended.
3. Only that finalized post-wake command is encoded as an in-memory 16 kHz mono WAV and sent to
   Groq Whisper.
4. Ron's normal local router, confirmation rules and allowlisted tools decide what can run.
5. A bounded, display-cleaned opening from the reply is sent to Groq Orpheus. After the WAV
   format is validated, complete PCM packets are played immediately instead of waiting for the
   full response body. The full reply stays in the terminal.

Audio is not saved by the cloud adapter. The endpoint is fixed to Groq's official HTTPS API and
cannot be redirected through `.env`. API keys are excluded from object representations and error
messages.

## Recommended `.env`

```dotenv
RON_AI_PROVIDER=groq
GROQ_API_KEY=replace_with_your_own_key
RON_GROQ_MODEL=openai/gpt-oss-120b
RON_GROQ_REASONING_EFFORT=low
RON_AI_FALLBACK_LOCAL=true

RON_ASR_PROVIDER=groq
RON_ASR_FALLBACK_LOCAL=true
RON_GROQ_ASR_MODEL=whisper-large-v3-turbo
RON_GROQ_ASR_RETRY_MODEL=whisper-large-v3
RON_GROQ_ASR_TIMEOUT=15

RON_TTS_PROVIDER=groq
RON_TTS_FALLBACK_LOCAL=true
RON_GROQ_TTS_MODEL=canopylabs/orpheus-v1-english
RON_GROQ_TTS_VOICE=daniel
RON_GROQ_TTS_TIMEOUT=15
RON_GROQ_TTS_MAX_REQUESTS_PER_TURN=4
RON_GROQ_TTS_STREAMING=true
RON_TTS_FAST_FALLBACK=true
RON_TTS_MAX_CHARACTERS=700
RON_TTS_CHUNK_CHARACTERS=180
RON_INTERACTION_MODE=strict
```

`auto` is also valid for both providers. It chooses Groq when `GROQ_API_KEY` is present and local
speech when it is blank. `local` forces the old offline engine.

Before the first Orpheus request, the Groq organization admin must accept the model terms once at
<https://console.groq.com/playground?model=canopylabs%2Forpheus-v1-english>. Without that step,
Groq returns `model_terms_required` and Ron has to use the slow local fallback. This acceptance is
separate from creating the API key; it does not require a second key or an `.env` change.

## Free-plan behavior

v0.13.1 allows up to four Orpheus parts for a longer normal reply. Short answers still use only
the parts they need. Reusable acknowledgements and cues are generated once in the background and
then loaded from a provider-specific cache. Free-plan limits can change, so keep the request cap
appropriate for the current Groq allowance.

Short wake acknowledgements and progress cues are cached. On Windows, v0.13 can synthesize those
small cues with built-in system speech during background prewarm. If Groq fails before any audio
plays, the runtime tries that fast system voice before the optional cold local engine. A failed
stream is never replayed in full after partial audio, which avoids duplicated replies.

## Verify the upgrade

```powershell
python .\scripts\check_groq_ai.py
python .\scripts\check_groq_voice.py --play
python .\scripts\benchmark_speech.py
python -m ron
```

Say one normal command, wait for the reply, then enter `/latency` and `/health`. A successful cloud
turn should show Groq in `/status`, ASR far below the previous 13.94 seconds, and separate
`first audio byte` and `first audio` timings far below the previous 22.30 seconds. Exact times
depend on the network and current Groq service load.

The official API references are Groq's
[speech-to-text guide](https://console.groq.com/docs/speech-to-text) and
[text-to-speech guide](https://console.groq.com/docs/text-to-speech).

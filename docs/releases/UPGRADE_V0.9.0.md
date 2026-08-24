# Ron v0.9.0 - Conversational Voice Engine

This upgrade replaces the fixed accuracy-heavy voice path with a responsive,
adaptive pipeline. It does not remove Ron's existing router, safety checks,
normalizer, tablet events, memory, or agent tools.

## What changes

- 32 ms microphone frames reduce KWS/VAD scheduling jitter.
- Stateful resampling avoids a distortion seam at every Windows audio callback.
- Silero endpoints after 380 ms of silence instead of carrying the old 550 ms delay.
- Whisper uses beam 1 normally and beam 5 only when the first result is uncertain.
- Quiet microphone audio gets conservative DC removal and bounded gain before Whisper.
- Fast wake handoff checks audio after the KWS hit, so a quickly spoken short command is
  not discarded merely because the full segment was short.
- Ron automatically listens for six seconds after a normal answer. Another wake phrase
  is not required during that window.
- Voice-originated AI replies have a smaller 192-token budget.
- Kokoro synthesizes and speaks the opening sentence first.
- Later TTS chunks are synthesized during playback, using one continuously open speaker
  stream to avoid gaps between sentences.
- A prewarmed `One moment.` or `Let me check.` cue plays only if local AI inference takes
  longer than 1.4 seconds. The cue never starts fresh TTS inference during an AI response.
- `/status` includes the most recent ASR time for live latency checking.

## Install

From the RonAI project folder in PowerShell, with the Python 3.12 environment active:

```powershell
python -m pip install -e ".[voice,dev]"
python -m pytest
```

No new model family is required. The existing `distil-large-v3`, Sherpa KWS, Silero VAD,
and Kokoro files are reused.

## Recommended `.env` additions

Keep the existing private `.env` file. Add or update this block:

```dotenv
RON_AUDIO_BLOCK_MS=32
RON_VOICE_RESPONSIVE_END_SILENCE=0.38
RON_WAKE_FAST_SEGMENT_SECONDS=0.90
RON_WAKE_FAST_POST_SECONDS=0.68

RON_VOICE_AUTO_FOLLOWUP=true
RON_VOICE_AUTO_FOLLOWUP_WAIT=6.0
RON_VOICE_THINKING_CUE=true
RON_VOICE_THINKING_CUE_DELAY=1.4
RON_VOICE_THINKING_CUES=One moment.|Let me check.

RON_WHISPER_FAST_BEAM_SIZE=1
RON_WHISPER_RETRY_ENABLED=true
RON_WHISPER_RETRY_BEAM_SIZE=5
RON_WHISPER_RETRY_CONFIDENCE=0.52

RON_CHAT_VOICE_MAX_OUTPUT_TOKENS=192
RON_TTS_CHUNK_CHARACTERS=180
RON_TTS_PREFETCH_CHUNKS=true
```

`RON_VOICE_END_SILENCE` is the old v0.8 setting and is no longer read. The new variable
name makes upgraded installations receive the responsive endpoint even when their existing
`.env` still contains the old 0.55 value.

`RON_WHISPER_BEAM_SIZE=5` is accepted as a legacy retry-beam preference. It no longer forces
every utterance through beam 5. `RON_WHISPER_RETRY_BEAM_SIZE` takes precedence when present.

## Safe dry validation

Run these without allowing any phrase to execute a tool:

```powershell
python .\scripts\test_microphone.py --seconds 6
python .\scripts\test_wake_word.py --seconds 60
python .\scripts\benchmark_voice.py
python .\scripts\benchmark_speech.py
python .\scripts\calibrate_recognition.py
```

Then start Ron normally and test this sequence:

1. Say `Hey Ron` and confirm the acknowledgement is immediate.
2. Say `Hey Ron, status` quickly and confirm `status` is not swallowed.
3. Ask a normal question and confirm the first spoken sentence starts before the old full-reply
   delay.
4. Reply naturally during the six-second follow-up window without saying `Hey Ron` again.
5. Say `that's all` and confirm the follow-up window closes.
6. Type `/status` and record the displayed `last ASR` time.

## SSD and 3.5 TB hard drive roles

Keep these on the laptop SSD:

- active Sherpa wake model;
- active Silero model;
- active Whisper model;
- Kokoro model and voice data;
- tiny acknowledgement/thinking-cue cache.

Use the 3.5 TB drive for optional models, archives, backups, benchmark audio, and recordings
that you explicitly choose to retain. Normal room audio remains in RAM and is discarded.

## Validation completed for this source package

- 203 automated tests passed.
- Ruff passed on every changed Python file.
- Python bytecode compilation passed.

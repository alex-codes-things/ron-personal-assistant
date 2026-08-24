# Ron v0.10.0 - Streaming Conversation Loop

This update makes Ron's live voice turn concurrent without relaxing wake, confidence, tool, or
confirmation safety gates.

## What changed

- Complete AI sentences stream directly into Kokoro; Ron no longer waits for the full answer
  before beginning to speak.
- Kokoro synthesis can overlap local model generation and prepares the next sentence during
  playback.
- The live microphone thread remains responsive while AI and TTS run on a separate response
  thread.
- Barge-in is wake-gated: say `Hey Ron, stop`, `Hey Ron, wait`, or another configured exact
  interruption. Ambient speech and Ron's own speakers cannot enter normal follow-up routing
  during an active reply.
- Windows media now exposes explicit `play`, `pause`, and `resume`. With the Windows media-session
  API available, Ron checks state first and does nothing when playback is already correct.
- The planner retains a tiny context containing only verified successful actions. This resolves
  safe references such as `pause it`, `next one`, and `open that again` without inventing tools.
- Live progress updates replace one `Ron • ...` terminal line instead of printing each stage.
- `/latency` reports the latest voice turn's ASR, first token, first audio, answer-ready, and total
  timings.

## New environment settings

```dotenv
RON_VOICE_BARGE_IN=true
RON_VOICE_INTERRUPT_PHRASES=stop|wait|hold on|quiet|cancel that|never mind|that's enough
RON_TTS_STREAMING=true
RON_TTS_CONCURRENT_SYNTHESIS=true
```

The defaults are enabled. The voice setup installs the Windows SDK binding only on Windows; it
is used for state-aware media control and is not loaded on other platforms.

## Install and verify

```powershell
python -m pip install -e ".[voice,dev]"
python -m pytest
python -m ron
```

Try a normal question, then run `/latency`. During a longer spoken answer say `Hey Ron, stop`.
Finally play music and say `Hey Ron, resume the song` twice; the second request should report that
the media is already playing instead of toggling it to pause.

## Storage

The Windows media binding is small compared with the speech models. Keep active Whisper, KWS,
VAD and Kokoro files on the laptop SSD for latency. Ron's large hard drive remains the right place
for archives, optional model copies, benchmark corpora, recordings you explicitly retain,
backups, and long-term memory.

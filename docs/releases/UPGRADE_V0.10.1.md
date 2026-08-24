# Ron v0.10.1 - Event-Driven Voice Handoff

This update removes the remaining pauses between finishing work, speaking the result, and reopening
the microphone. It keeps Ron's existing wake, confidence, tool, and confirmation safety gates.

## What changed

- The speech stream no longer holds the output lock while waiting for the model's first sentence.
- Approved actions speak one pre-generated progress cue, such as `Opening it now`, without adding
  another TTS inference to the task path.
- The assistant returns as soon as the complete answer is queued; speech completion is tracked by
  an event instead of blocking AI/tool handling.
- Voice diagnostics distinguish `processing`, `speaking`, and `readying microphone`.
- Detector reset follows the real playback-complete event and the default echo guard is 120 ms.
- `Hey Ron, <new request>` can replace a generated reply. If a computer action has started, Ron
  lets that action reach its safe result boundary and then automatically starts the retained request.
- Kokoro's native CPU pool defaults to two threads so simultaneous local chat and opening-sentence
  synthesis do not starve each other.
- Common progress and short result phrases are cached on the laptop SSD. The large Ron drive is
  still reserved for archives, optional model copies, benchmarks, retained recordings and memory.

## Environment additions

```dotenv
RON_VOICE_ACCEPT_NEW_TURN=true
RON_VOICE_ACTION_CUES=true
RON_TTS_CPU_THREADS=2
RON_TTS_ECHO_GUARD=0.12
```

These are enabled in the supplied `.env`. Set `RON_VOICE_ACTION_CUES=false` if you prefer silent
tool progress, or `RON_VOICE_ACCEPT_NEW_TURN=false` to keep the older stop-only barge-in behaviour.

## Install and verify

```powershell
python -m pip install -e ".[voice,dev]"
python -m pytest
python -m ron
```

Try `Hey Ron, open Spotify`; Ron should acknowledge the running action and speak its result with no
full-answer reset pause. During a longer answer, say `Hey Ron, what time is it`; the new request
should replace the generated reply and begin automatically.

# Ron Voice Input v1

Ron's voice input is an optional local adapter around the same assistant used by
the terminal. It does not own a second router, chatbot, agent, tool registry or
conversation history.

## Runtime path

```text
microphone callback -> bounded frame queue -> sherpa KWS + Silero VAD
    -> one complete faster-whisper transcription
    -> wake verification + deterministic correction
    -> RonAssistant -> existing router -> chat or approved agent tools
```

Only keyword spotting and VAD run continuously. Full ASR runs once after a
complete utterance. Ollama begins only after ASR finishes, and obvious tool
requests continue to bypass Ollama through the deterministic planner.

Terminal and voice calls share `RonAssistant`'s turn lock. This gives the two
input threads one ordered assistant turn at a time without allowing routing,
confirmation or agent state to race.

## Wake-word defence

The configured phrase is `HEY_RON`. Sherpa's acoustic threshold and token
boosting are configurable independently. Its keyword file contains only the
phone tokens and result label, so `RON_WAKE_THRESHOLD` genuinely controls
sensitivity. Silero endpoints speech but does not veto a wake detection in the
same audio block; this prevents a quiet or very short phrase from being lost.
The completed transcript must still begin with a configured wake alias before
anything is routed. Failed verification executes nothing and starts a cooldown.

The default transcript aliases are deliberately narrow: `hey ron` and the
common ASR rendering `hey run`. The acoustic keyword remains only `HEY_RON`.
Household tests should include television, music, normal family conversations,
`Aaron`, `Ronald`, `Hey John` and `Hey, run` before lowering the threshold.
The default acoustic threshold is `0.35`; the safe wake-only test can temporarily
try `0.25` when calibrating a quiet microphone or accent.

Wake recognition is convenience, not authentication. Existing tool argument,
risk, confirmation, timeout, preflight and rollback controls remain mandatory.

## Complete utterances

Audio callbacks copy small frames into a bounded queue and return. Model work,
file access, tablet communication and Ollama calls are forbidden inside the
callback. A worker resamples unsupported device rates to 16 kHz.

Silero endpointing combines frames until a speech segment is complete. The
default 550 ms trailing silence avoids fixed multi-second delays while keeping
words together. A wake-only segment opens a 2.5-second command-start window, so
both forms work:

```text
Hey Ron, open Spotify.
Hey Ron ... open Spotify.
```

No arbitrary one-second clip is transcribed separately.

## Transcription and correction

Faster-whisper uses CPU INT8, one beam and no repeated partial decoding. The
model is loaded once and warmed once. Its model directory is local-only at
runtime; setup must download it in advance.

The normalizer keeps the raw text, corrected text, wake phrase, correction
notes and confidence. It contains narrow corrections for known failures such
as:

```text
open spot the fi       -> open Spotify
set colume two twenty  -> set volume to twenty
open up latin text human -> open a blank text document
```

Corrections are context-specific. An unresolved `set volume two` asks whether
the user meant 2 percent or an unfinished `to ...`; it never executes a guess.
The LLM is not used to rewrite routine tool commands.

## Continuous chat

`Start a chat` enables the existing longer conversation history and opens a
short no-wake reply window after every completed response. `End chat`, `Stop
chat`, `Go to sleep`, `That's all`, a timeout, or microphone loss closes it.
The agent remains reachable inside continuous chat because every accepted turn
still uses the normal router.

## Performance controls

- KWS uses one inference thread and the 160 ms chunk-8 int8 encoder.
- Whisper and Ollama share the inference scheduler instead of competing.
- One speech segment receives one Whisper pass.
- Whisper stays resident; models are not swapped during a session.
- The hotword list is limited to 20 short terms.
- The face receives asynchronous semantic events; voice never waits for ADB.
- Queues, audio duration, text length, retries and shutdown waits are bounded.

Target simple-command latency after the user stops speaking is 0.8-1.6 seconds.
The benchmark script chooses between `small.en` accuracy and `base.en` speed on
the actual computer rather than assuming published performance.

## Failure and privacy rules

- Missing packages or models disable only voice.
- A missing or disconnected microphone produces one restrained warning and
  exponential background retry.
- Low-confidence, empty, oversized or failed transcription executes nothing.
- Unexpected subsystem failures are logged privately and restarted safely.
- The tablet face, terminal, agent, reminders and local AI remain independent.
- Room audio stays in RAM and is discarded. Diagnostic recording is not enabled
  by the normal application.
- Model downloads happen only in the explicit setup script; normal runtime is
  offline.

## Acceptance gate

Before voice is treated as stable on the target computer:

1. At least 95 of 100 deliberate wakes must succeed.
2. At least 98 of 100 commands must retain their opening words.
3. Prepared command-intent tests must reach at least 97 percent accuracy.
4. An eight-hour household test must execute no false command.
5. Simple tool work should begin within the target latency range.
6. Terminal input must survive every simulated voice failure.
7. Wi-Fi-off operation must work after setup.
8. Long-running tests must show bounded queues and clean shutdown.

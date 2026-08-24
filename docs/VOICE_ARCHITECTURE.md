# Ron Conversational Voice Engine v6

Ron's voice input is an optional hybrid adapter around the same assistant used by
the terminal. It does not own a second router, chatbot, agent, tool registry or
conversation history.

## Runtime path

```text
microphone callback -> fixed 32 ms frames -> sherpa KWS + responsive Silero VAD
    -> Groq Whisper Turbo -> uncertainty-only Whisper Large V3 retry
    -> wake verification + deterministic correction
    -> RonAssistant -> existing router -> chat or approved agent tools
    -> bounded formatting -> live Groq Orpheus PCM stream -> sounddevice output
    -> live RMS levels -> existing tablet speech animation
```

Only keyword spotting and VAD run continuously. Cloud ASR receives one complete,
post-wake utterance. The common path uses the turbo model; the accuracy model is
reserved for uncertainty. Local Faster-Whisper remains cold until a cloud failure.

Every assistant turn also emits concise terminal stages: understanding, conversational thinking,
safe planning, preflight and execution. Ordinary media synonyms take the deterministic fast path.
An unfamiliar short imperative may use the allowlisted agent planner as the semantic route check;
the prepared structured plan is then reused for execution so the same request is never classified
by one model and planned again by another.

Terminal and voice calls share `RonAssistant`'s turn lock. This gives the two
input threads one ordered assistant turn at a time without allowing routing,
confirmation or agent state to race.

## Cloud-first speech output

A voice-originated request calls the same `RonAssistant` as terminal input, but marks the chat
turn as spoken. That changes phrasing rather than capability: Ron leads with a short, natural
spoken answer and uses a calm, precise, original British-assistant style with restrained dry
wit when appropriate. It is intentionally not a clone or imitation of any named actor or
fictional character. The complete response still exists in the terminal.

The output layer then creates a separate speakable version. Fenced code is not read character
by character, long replies are bounded with the remainder left in the terminal, URLs and long
Windows paths are referred to as items on screen, and common technical abbreviations are
expanded for pronunciation.

Groq Orpheus is the default when a Groq key exists, using `daniel`. A normal turn sends one
bounded opening and leaves the complete answer in the terminal. Ron validates the incremental
RIFF/WAV structure and begins playback as soon as complete PCM frames arrive. Windows system
speech is the fast emergency fallback; Kokoro ONNX `bm_george` remains cold unless both the cloud
and quick fallback are unavailable.

Speech synthesis uses the shared inference scheduler after the assistant response has finished,
so Ollama, Whisper, and TTS do not intentionally compete for CPU inference at the same time.
The opening sentence is synthesized separately and spoken first. While it plays, the next chunk
is synthesized in a bounded one-worker pipeline. A cached thinking cue can play after a genuinely
slow response delay without performing fresh model inference.
Playback is synchronous for voice turns: the microphone capture is muted while Ron speaks, the
queued pre-roll is cleared, and a short post-output echo guard runs before room listening resumes.
This prevents Ron from hearing his own speakers and responding to himself.

`SPEECH_STARTED`, `SPEECH_LEVEL`, and `SPEECH_ENDED` events already understood by the tablet
are driven from the actual output waveform. Mouth travel therefore follows real RMS amplitude
rather than a purely simulated talking animation.

Important output settings:

```text
RON_TTS_ENABLED=true
RON_TTS_PROVIDER=auto
RON_TTS_FALLBACK_LOCAL=true
RON_GROQ_TTS_MODEL=canopylabs/orpheus-v1-english
RON_GROQ_TTS_VOICE=daniel
RON_GROQ_TTS_MAX_REQUESTS_PER_TURN=4
RON_GROQ_TTS_STREAMING=true
RON_TTS_FAST_FALLBACK=true
RON_TTS_VOICE=bm_george
RON_TTS_SPEED=0.94
RON_TTS_LANGUAGE=en-gb
RON_TTS_GAIN=1.0
RON_TTS_MAX_CHARACTERS=700
RON_TTS_LEVEL_MS=40
RON_TTS_CHUNK_CHARACTERS=180
RON_TTS_PREFETCH_CHUNKS=true
RON_TTS_ECHO_GUARD=0.12
RON_TTS_OUTPUT_DEVICE=
RON_TTS_MODEL=
RON_TTS_VOICES=
```

## Wake-word defence

The configured phrase is `HEY_RON`. Sherpa's acoustic threshold and token
boosting are configurable independently. Its keyword file contains only the
phone tokens and result label, so `RON_WAKE_THRESHOLD` genuinely controls
sensitivity. Silero endpoints speech but does not veto a wake detection in the
same audio block; this prevents a quiet or very short phrase from being lost.
The completed transcript must still begin with a configured wake alias before
anything is routed. Failed verification executes nothing and starts a cooldown.

The acoustic keyword remains only `HEY_RON`. After that acoustic gate fires, transcript
verification accepts a deliberately small alias set plus a high-threshold fuzzy match. This
lets renderings such as `hey run`, `hey wrong`, or a close accented variation pass without
turning fuzzy text matching into the wake detector itself.
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
default 380 ms trailing silence avoids fixed multi-second delays while keeping
words together. A wake-only segment opens an eight-second command-start window, so
both forms work:

```text
Hey Ron, open Spotify.
Hey Ron ... open Spotify.
```

No arbitrary one-second clip is transcribed separately.

## Transcription and correction

Groq `whisper-large-v3-turbo` is the default when a Groq key exists. Ron sends a 16 kHz mono WAV
from memory, asks for `verbose_json`, and uses segment confidence/no-speech metadata in the same
safety checks as local ASR. Uncertainty can retry with `whisper-large-v3`. Faster-Whisper CPU
INT8 `distil-large-v3` remains a cold local fallback with beam 1/5 adaptive decoding.

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

## Interaction profiles

`RON_INTERACTION_MODE=strict` requires a fresh wake phrase after every completed command and is
the default. `followup` opens a bounded wake-free window after normal replies. `continuous`
starts in bounded continuous listening. The spoken `Start a chat` and `End chat` controls still
change the chat session at runtime. Every accepted turn uses the normal router and safety rules.

A wake-only `Hey Ron` is a separate, safe handoff: it may acknowledge and accept the immediately
following command without changing the selected profile.

## Performance controls

- KWS uses one inference thread and the 160 ms chunk-8 int8 encoder.
- Cloud Whisper, model generation and Orpheus do not share the laptop inference scheduler.
- Local Whisper/Kokoro models stay unloaded during normal cloud operation.
- One speech segment receives one turbo pass unless confidence requires one accuracy retry.
- The hotword list is limited to 20 short terms.
- The face receives asynchronous semantic events; voice never waits for ADB.
- TTS text/audio remains bounded. During streamed output the microphone accepts only a fresh
  acoustic wake phrase, which enables safe interruption without opening ambient follow-ups.
- Queues, audio duration, text length, retries and shutdown waits are bounded.

Recognition is accuracy-first. `scripts/benchmark_voice.py` compares `small.en` with
`distil-large-v3` on the actual computer instead of assuming published latency.
`scripts/calibrate_recognition.py` is a dry-run phrase suite that shows the raw transcript,
deterministic correction, similarity score and confidence without routing or executing tools.

## Failure and privacy rules

- A cloud outage automatically attempts the configured cold local fallback.
- Missing input packages/models disable only voice input; missing Kokoro files disable only fallback speech.
- A missing or disconnected microphone produces one restrained warning and
  exponential background retry.
- Low-confidence, empty, oversized or failed transcription executes nothing.
- Unexpected subsystem failures are logged privately and restarted safely.
- The tablet face, terminal, agent, reminders and selected AI provider remain independent.
- Room audio stays in RAM and is discarded. Only a finalized post-wake utterance is uploaded;
  diagnostic recording is not enabled by the normal application.
- Local model downloads happen only in the explicit setup script.

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
9. Ron must never wake on or transcribe his own speaker output during normal TTS playback.
10. Spoken mouth levels should visibly track real output amplitude without affecting terminal use.

## Conversational wake handoff (v0.8.0)

A wake-only utterance and a wake-plus-command utterance deliberately take different paths:

```text
Hey Ron! -> KWS -> ASR/normalizer verifies wake-only -> cached spoken acknowledgement
         -> echo guard -> LISTENING -> follow-up ASR (no second wake phrase required)

Hey Ron, open Spotify -> KWS -> ASR/normalizer -> command -> assistant/router directly
```

Acknowledgements are deterministic local phrases rather than LLM generations, so the wake response is bounded, predictable and does not spend chat-model inference. Short TTS audio is cached for reuse. The follow-up timeout starts only after the acknowledgement returns, so Ron does not consume the user's response window while he is speaking.

## Low-latency wake path (v0.8.1)

The critical wake path is deliberately split from full ASR:

```text
microphone -> tiny Sherpa KWS -> short wake-only VAD segment -> cached acknowledgement
                                                   |
                                                   +-> follow-up microphone -> Whisper -> normalizer -> Ron
```

A short KWS-confirmed wake-only segment is allowed to open listening, but never to execute a tool. Longer wake+command segments still pass through Whisper and the normal wake-verification/correction pipeline. This makes false-positive cost small while avoiding a large-model transcription before the acknowledgement.

The `high` sensitivity profile is intended for Alex's calibrated South African/Afrikaans-English speech. The keyword generator emits the original `AA1` pronunciation plus an `AO1` pronunciation when supported by the KWS token vocabulary. A `balanced` mode remains available through `RON_WAKE_SENSITIVITY=balanced` if the room proves too noisy.

## Conversational latency engine (v0.9.0)

v0.9 removes four sources of artificial waiting while preserving the local safety gates:

1. PortAudio delivers predictable 32 ms frames. A stateful fallback resampler preserves audio
   continuity when a Windows microphone cannot open natively at 16 kHz.
2. Silero endpoints after 380 ms of trailing silence. Whisper performs a one-beam first pass;
   only uncertain speech pays for the five-beam accuracy retry.
3. Fast wake-only handoff requires both a short complete segment and a short interval after the
   KWS hit. A compact wake-plus-command can no longer be discarded solely because it was spoken
   quickly.
4. Kokoro speaks the opening sentence first and prefetches later sentence chunks during playback.
   Spoken LLM turns have their own smaller output budget, and a cached cue fills only waits longer
   than the configured thinking-cue delay.

After a normal reply, Ron listens for another utterance for six seconds. That follow-up is routed
through the same normalizer, router, agent safety rules, and conversation history without another
wake phrase. `go to sleep`, `that's all`, `end chat`, or `stop chat` closes the handoff.

The 3.5 TB external drive is used for cold data: archives, optional models, recordings that the
user explicitly chooses to retain, and benchmark corpora. KWS, VAD, active Whisper, Kokoro, and
their live caches remain on the laptop SSD because HDD seeks would directly increase latency.

## Streaming conversation loop (v0.10.0)

The live microphone thread now dispatches an accepted request to one bounded response thread and
immediately returns to wake detection. `ChatService` already exposes model tokens; v0.10 feeds
those tokens into a sentence buffer. As soon as a complete sentence arrives, Kokoro synthesizes
it and opens one continuous output stream. The next sentence is synthesized underneath current
playback.

While a response is active, continuous-chat and automatic-follow-up audio cannot reach ASR. Only
a new Sherpa `Hey Ron` detection opens transcription. Exact configured interruption phrases stop
current speech; ordinary commands are asked to wait until the current turn is safe to accept.
The underlying model/tool operation is not force-killed, so interruption never bypasses a safety
gate or leaves a partially executed tool.

Each voice turn records bounded diagnostic marks for ASR, routing/planning stages, first token,
first audio, completed answer, and total time. Use `/latency` to inspect the latest turn rather
than guessing which stage caused a pause.

## Event-driven reply handoff (v0.10.1)

The streaming worker waits for its first complete sentence without holding the speech lock. This
lets a pre-generated action cue play immediately while routing or a tool is still working. The
prompt handler closes the sentence queue without waiting for playback; a completion event carries
the lifecycle through separate `processing`, `speaking`, and `readying microphone` states.

When playback ends, the voice thread requests detector reset, discards captured speaker tail and
uses only a 120 ms echo guard before follow-up listening. It does not pay a fixed polling delay.
During an active turn a fresh wake-gated command is retained as the next request. Local-model
generation is cancelled at its stream boundary; a tool side-effect is never force-killed and the
retained request begins as soon as that safe boundary returns.

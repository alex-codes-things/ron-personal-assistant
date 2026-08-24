# Ron

Ron is a hybrid cloud/local personal assistant built in Python, with a native Android tablet
face. Language understanding, finalized-command transcription and reply speech can run through
Groq while wake detection, silence detection, memory, confirmations and every computer action
remain controlled by the laptop.
The project is organised so the root stays readable and generated files stay out of the way.

## Unified voice and clean terminal (v0.13.1)

v0.13.1 removes the apparent 180-character speech cutoff. Spoken replies can use up to four
bounded Groq requests (700 speakable characters by default), begin from complete early sentences,
and reserve their last part so a longer reply ends deliberately with the rest left in the
terminal—never halfway through a word or sentence. Each later Groq part is prepared underneath
current playback to prevent a new synthesis pause between parts.

Wake acknowledgements, thinking cues, action cues and normal replies now share the configured
Groq Orpheus voice. Persistent cue caches include the provider, model and Groq voice, so an old
`bm_george` cache cannot be mistaken for Daniel. One-time Groq cues are generated in the
background without holding the live playback lock.

The terminal has a compact Ron banner, aligned `You ›`, `Ron ›` and working-state rows, readable
system categories, and intentional spacing between turns. It remains lightweight and works in
the normal V S Code PowerShell terminal.

See [`docs/releases/UPGRADE_V0.13.1.md`](docs/releases/UPGRADE_V0.13.1.md) for the required
`.env` values.

## Conversational core and live speech (v0.13.0)

v0.13 removes the full-audio download from the normal Groq speech path. Ron validates the
streaming RIFF/WAV header, then sends complete PCM frames to the already-open speaker stream as
they arrive. `/latency` now distinguishes the first received audio bytes from audible playback.
If Groq speech fails before playback begins, Windows system speech provides a fast emergency
voice before Ron considers loading cold Kokoro. If a stream breaks after speech has begun, Ron
does not repeat the whole reply in another voice.

Voice behavior is now explicit. `RON_INTERACTION_MODE=strict` is the default and requires
**Hey Ron** before every completed command. `followup` briefly accepts one wake-free next turn,
while `continuous` listens until its bounded safety timeout. A wake-only **Hey Ron** still opens
the normal short command window in every mode.

The approved tool planner now sees live capability availability and keeps a bounded 15-minute
history of verified actions, so references such as `pause it` and `open that again` survive an
unrelated command without allowing the model to invent computer access. Work output includes an
honest completed or verified result. `/health` combines voice, speech, tools, storage, latency,
AI and the optional tablet into one report. Timing-only performance records are archived through
Ron's resilient storage queue under `Diagnostics/Performance`; prompts, transcripts and replies
are never included.

The new defaults work with an existing v0.12 `.env`. These optional lines make them explicit:

```dotenv
RON_INTERACTION_MODE=strict
RON_GROQ_TTS_STREAMING=true
RON_TTS_FAST_FALLBACK=true
```

See [`docs/releases/UPGRADE_V0.13.0.md`](docs/releases/UPGRADE_V0.13.0.md) for the clean upgrade
steps.

## Free Groq intelligence and voice (v0.12.2)

Ron now selects AI with `RON_AI_PROVIDER=auto|groq|openai|ollama`. The recommended mode streams
`openai/gpt-oss-120b` through Groq's free cloud plan. v0.12 also removes the two measured laptop
bottlenecks: finalized commands use `whisper-large-v3-turbo`, and bounded spoken replies use the
Orpheus `daniel` voice. Wake-word and endpoint detection remain local. In `auto` mode Ron uses
the same `GROQ_API_KEY` for all three cloud services and keeps the old local models cold.

The cloud model receives text and can propose an allowlisted plan, but it never receives direct
control of the computer. Ron's local registry still validates tool names, arguments, risk and
confirmation before anything runs. Hidden reasoning is excluded from the stream so the first
useful sentence reaches speech quickly; low reasoning effort is the default for normal voice use.

Groq ASR and TTS are dependency-free HTTP clients with bounded in-memory WAV data. No raw command
recording is written to disk. If cloud speech fails, local Whisper/Kokoro can load as a cold
fallback; neither consumes CPU during normal cloud operation. Each reply uses one bounded TTS
request by default so a streaming answer cannot waste the free voice allowance sentence by
sentence. The full answer always remains visible in the terminal.

To enable the free Groq mode, create a key at <https://console.groq.com/keys> and add this to your
private `.env`:

```dotenv
RON_AI_PROVIDER=groq
GROQ_API_KEY=replace_with_your_own_groq_key
RON_GROQ_MODEL=openai/gpt-oss-120b
RON_GROQ_REASONING_EFFORT=low
RON_AI_FALLBACK_LOCAL=true
RON_ASR_PROVIDER=groq
RON_ASR_FALLBACK_LOCAL=true
RON_TTS_PROVIDER=groq
RON_TTS_FALLBACK_LOCAL=true
RON_GROQ_TTS_VOICE=daniel
RON_GROQ_TTS_MAX_REQUESTS_PER_TURN=1
RON_GROQ_TTS_STREAMING=true
RON_TTS_FAST_FALLBACK=true
RON_INTERACTION_MODE=strict
```

Then verify the connection and start Ron:

```powershell
python .\scripts\check_groq_ai.py
python .\scripts\check_groq_voice.py --play
python -m ron
```

Orpheus requires its model terms to be accepted once by the Groq organization admin. If the
voice check reports `model_terms_required`, sign in and accept them at
<https://console.groq.com/playground?model=canopylabs%2Forpheus-v1-english>, then rerun the check.
v0.12.1 preserves Groq's safe JSON reason in the terminal instead of hiding every rejected
speech request behind a generic HTTP 400 warning.
v0.12.2 also accepts Orpheus's unknown-length streaming WAV header and validates the actual
downloaded PCM frames, preventing valid short replies from being rejected as impossibly long.

Keep `.env` private and never commit or share it. See
[`docs/HYBRID_CLOUD_AI.md`](docs/HYBRID_CLOUD_AI.md) for provider choices, safe fallback and
cleanup.

## Conversational wake handoff (v0.8.0)

Ron can now handle the wake phrase as a natural two-stage interaction. Saying **"Hey Ron!"** by itself produces a short local acknowledgement such as **"Yes?"**, then Ron waits for the request without requiring the wake phrase again. Saying **"Hey Ron, open Spotify"** in one breath skips the acknowledgement and routes the command immediately. The acknowledgement phrases are cached/prewarmed locally, low-confidence wakes remain silent, and the tablet returns to its listening expression while the follow-up window is open.

v0.9 adds a conversational voice engine around that handoff. Recognition now starts with a
fast one-beam decode and uses the slower accuracy pass only when confidence or intent is
unclear. The wake-only shortcut checks both utterance length and audio captured after the KWS
hit so short commands are not swallowed. Replies are synthesized sentence-first, later speech
chunks are prepared during playback, slow AI turns get a cached restrained thinking cue, and a
six-second automatic follow-up window makes ordinary back-and-forth conversation possible
without repeating the wake phrase. Active voice models stay on the laptop SSD; the external
drive remains ideal for archives, optional model downloads, benchmarks, and retained recordings.

v0.9.1 removes another source of perceived delay: short, bounded actions such as opening an
allowlisted application or controlling the current media now execute immediately instead of
being placed on the background queue. Ron prints live `[WORKING]` stages while understanding,
planning, checking and running a request. Fast rules cover common media language such as
`unpause the song`, while unfamiliar short commands get one allowlisted semantic tool-resolution
pass whose result is reused by the planner—there is no second routing-model round trip. Tablet
disconnect retries stay in the private debug log; the terminal sees one state-change notice and
no periodic reminder by default.

v0.10 removes the full-answer wait from spoken conversation. Complete model sentences flow into
Kokoro while the rest of the answer is still being generated, and the live microphone loop no
longer blocks behind AI or speech work. Say `Hey Ron, stop` to cut off the current spoken reply
safely. Explicit Windows `play`, `pause`, and `resume` commands use current media-session state
when available, so `resume` cannot accidentally pause a song that is already playing. Ron also
remembers the last verified action for short follow-ups such as `pause it`, `next one`, and
`open that again`. Live work stages replace one terminal status line, and `/latency` reports ASR,
first-token, first-audio, answer-ready, and total turn timings.

v0.10.1 removes the remaining handoff stalls. The reply stream no longer reserves the speaker
while waiting for its first sentence, approved actions can speak a cached status cue such as
`Opening it now`, and AI generation returns control as soon as speech is queued. Voice status now
distinguishes processing, speaking, and microphone preparation. A fresh wake-gated command can
replace a generated reply; an action already changing the computer is allowed to finish safely,
then the new request starts automatically. Kokoro uses a small CPU pool and the post-speech guard
is reduced to 120 ms before the detector reset and natural follow-up window.

The voice defaults work without editing an existing `.env`. Optional tuning is available through
`RON_TTS_STREAMING`, `RON_TTS_CPU_THREADS`, `RON_VOICE_ACTION_CUES`,
`RON_VOICE_ACCEPT_NEW_TURN`, and `RON_VOICE_INTERRUPT_PHRASES`.

## Project map

- **`ron/`** — Python assistant: agent, AI, chat, voice, network awareness and display connection
- **`tablet/`** — Nexus 7 Android face and quick-action app
- **`runtime/`** — generated/private data, downloaded models, logs and recordings
- **`scripts/`** — setup, diagnostics, benchmarks and installers
- **`tests/`** — automated tests
- **`docs/`** — architecture and structure notes

See [`docs/STRUCTURE.md`](docs/STRUCTURE.md) for the detailed map, [`docs/NETWORK.md`](docs/NETWORK.md) for the optional LAN layer, and [`docs/MEMORY_AND_STORAGE.md`](docs/MEMORY_AND_STORAGE.md) for resilient long-term memory.

## Start Ron

Ron loads the project-root `.env` **before any service is constructed**. Explicit
PowerShell/OS variables still win over `.env`, and diagnostics never print secret values.
This means the voice model, personal wake profile, chosen TTS voice, memory settings and other
machine-specific options in `.env` are active during normal `python -m ron` use.

Create/activate a Python 3.12 virtual environment, then install Ron in editable mode:

```powershell
python -m pip install -e ".[voice,desktop-preview,dev]"
python -m ron
```

Optional setup helpers:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_voice.ps1
# Only needed when using Ollama or keeping a local fallback:
powershell -ExecutionPolicy Bypass -File .\scripts\setup_local_ai.ps1
python .\scripts\check_groq_ai.py
python .\scripts\setup_spotify.py
powershell -ExecutionPolicy Bypass -File .\scripts\install_tablet_face.ps1
```

## Runtime files

Ron keeps machine-specific/generated content under `runtime/` instead of scattering `data`, `models`, `logs` and `recordings` across the project root. These files are intentionally ignored by Git.

## Memory

Ron can use durable memory directly without an LLM round trip. Try phrases such as `Remember that my amp is a Fender Mustang LT25`, `What do you remember about my amp?`, or `Forget about the Fender Mustang`. Forgetting requires confirmation and remains safe if the external memory drive is disconnected.

Conservative automatic learning is enabled by default for stable user facts and project context. It never auto-saves Ron's own generated replies or credential-like secrets. See [`docs/MEMORY_AND_STORAGE.md`](docs/MEMORY_AND_STORAGE.md) for the full behavior and settings.


## Voice recognition

With a Groq key, recognition defaults to Groq `whisper-large-v3-turbo`; uncertain results can
retry through `whisper-large-v3`. This replaces the measured 13.94-second laptop decode with a
network request while leaving the local acoustic wake gate in front of it. Local
Faster-Whisper `distil-large-v3` remains a cold fallback and uses beam size 1, retrying with beam
size 5 only when the first transcript is uncertain.
The wake transcript check tolerates close accented renderings only after the acoustic `Hey Ron`
detector has already fired, and deterministic correction handles a small allow-list of common
application/control mistakes without asking an LLM to guess tool commands. `small.en` remains a
fallback if the stronger model is too slow on the target laptop.

After voice setup, compare models and run the no-execution accent check:

```powershell
python .\scripts\benchmark_voice.py
python .\scripts\benchmark_speech.py
python .\scripts\calibrate_recognition.py
```

The calibration script never routes a phrase to Ron; it only shows what the recognizer heard.

v0.7.3 guarantees that this same calibrated normalizer is used by live voice input and continuous chat. When a correction is applied, the terminal prints an auditable `[VOICE CORRECTED]` line before the corrected prompt reaches the normal assistant/router. Joined wake renderings such as `Heyron` are derived safely from the configured wake phrase after acoustic KWS confirmation, and the observed `Galway Grohl` title error is corrected only inside the matching Spotify play command.

## Spoken voice

With a Groq key, Ron defaults to Orpheus `daniel`. Token streaming collects one useful bounded
opening and sends a single TTS request, replacing the measured 7.07-second Kokoro delay before an
11-character sentence. The full response remains visible in the terminal. The speech layer
strips display-only markdown/code, mentions when more detail is in the terminal, and drives the
Nexus mouth from the real output waveform. `bm_george` remains the cold local fallback.

Run the normal voice setup to install/download everything explicitly:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_voice.ps1
python .\scripts\audition_voices.py
```

The local audition script still cycles through `bm_george`, `bm_fable`, `bm_daniel`, and
`bm_lewis`. For cloud speech, change `RON_GROQ_TTS_VOICE` to `austin`, `daniel`, or `troy` for a
male voice, or `autumn`, `diana`, or `hannah` for a female voice. Missing speech files or a cloud
outage never disables terminal chat, memory, or agent tools.

## Tablet

Open the `tablet/` directory in Android Studio if you want to edit the Nexus app directly. The normal installer is `scripts/install_tablet_face.ps1`.

After pairing, the Nexus can connect over the local LAN. Ron discovers it automatically when router broadcasts are allowed; `RON_FACE_HOST` can be set as a manual fallback. The existing USB/ADB transport remains available and local Ron does not depend on the tablet or network being online.

## Low-latency wake handoff (v0.8.1)

v0.8.1 removes full Whisper from the critical path for short KWS-confirmed wake-only utterances. `Hey Ron!` can now hand off directly to a cached local acknowledgement, then reopen the microphone with a short acknowledgement-specific echo guard. The follow-up timeout begins only after the acknowledgement finishes. The tiny wake detector also uses an accent-friendly sensitivity profile and multiple compatible pronunciations of `Ron`; longer one-shot wake+command utterances still go through the full ASR verification and correction pipeline.

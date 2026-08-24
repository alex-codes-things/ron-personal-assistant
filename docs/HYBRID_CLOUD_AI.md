# Free and Hybrid Cloud AI

Ron v0.11.1 moved the slow language-model workload off the laptop without handing cloud AI
control of the computer. The recommended provider is Groq's free plan with
`openai/gpt-oss-120b`.

Ron v0.12 applies the same cloud-first pattern to finalized-command transcription and bounded
reply speech. See [`CLOUD_VOICE.md`](CLOUD_VOICE.md); wake detection, endpointing and every
computer action remain local.

## What stays on the laptop

- Hey Ron wake detection and microphone activity detection
- finalized command capture and optional cold Faster-Whisper fallback
- sound playback and optional cold George/Kokoro fallback
- long-term memory files and storage binding
- the tablet face, Spotify credentials and local network state
- deterministic commands, the tool allowlist, argument validation and confirmations
- the actual execution of every Windows, media, reminder and file-system action

For AI requests, Groq receives the bounded conversation and retrieved memory context already used
by Ron's chat client. When cloud voice is enabled, it also receives one finalized post-wake
utterance and bounded speakable reply text. Do not place passwords or other secrets in normal
conversation or saved memories. The request excludes model reasoning from the returned stream.
Groq's own data and account policies still apply.

## Provider modes

| Setting | Behaviour |
| --- | --- |
| `RON_AI_PROVIDER=groq` | Recommended free cloud mode using the configured Groq model. |
| `RON_AI_PROVIDER=auto` | Prefers Groq, then OpenAI, then Ollama, based on available keys. |
| `RON_AI_PROVIDER=openai` | Optional paid OpenAI API compatibility mode. |
| `RON_AI_PROVIDER=ollama` | Fully local mode. No cloud request is made. |

Recommended setup while first testing Groq:

```dotenv
RON_AI_PROVIDER=groq
GROQ_API_KEY=replace_with_your_own_groq_key
RON_GROQ_MODEL=openai/gpt-oss-120b
RON_GROQ_REASONING_EFFORT=low
RON_GROQ_TIMEOUT=30
RON_GROQ_MAX_PROMPT_CHARACTERS=24000
RON_AI_FALLBACK_LOCAL=true
```

The endpoint is fixed in code to the official HTTPS Groq API. A changed `.env` cannot redirect
the key to another server. The prompt-character bound is deliberately conservative because the
free plan currently allows 8,000 tokens per minute for this model.

## Setup

1. Open <https://console.groq.com/keys>, create a free account and create an API key.
2. Open the existing private `.env`; do not replace the whole file with `.env.example`.
3. Add the seven settings above and replace only the API-key placeholder.
4. Run `python .\scripts\check_groq_ai.py`.
5. Start Ron with `python -m ron`, then type `/status` to confirm the Groq provider.

Never paste the key into chat, a screenshot, Git, or a shared ZIP. Ron's `.gitignore` excludes
the private `.env`.

## Fallback and cleanup

Keep `RON_AI_FALLBACK_LOCAL=true` until the Groq connection check succeeds. Ollama stays cold and
uses no model CPU during normal Groq operation. If Groq is temporarily unreachable or the free
rate limit is reached before visible text appears, Ron can answer through the local model.

Once Groq is reliable, change `RON_AI_FALLBACK_LOCAL=false`. Only then, if disk space is more
important than offline conversation, remove the Ollama model:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\cleanup_project.ps1 -RemoveOllamaModel
```

Run the script without `-RemoveOllamaModel` to remove only Python/test caches. It preserves
`.env`, `.venv`, runtime memory, Whisper, Kokoro and tablet files. Do not remove
`runtime\models\voice`; those files provide the local recognition and George voice.

## Free-plan limits

At the time of this release Groq publishes these free limits for `openai/gpt-oss-120b`: 30
requests/minute, 1,000 requests/day, 8,000 tokens/minute and 200,000 tokens/day. They can change,
so check <https://console.groq.com/docs/rate-limits> if Ron reports a rate limit. A free plan is a
quota, not an unlimited or guaranteed service.

## Why voice should feel faster

Groq streams visible text as it arrives. Ron feeds complete early sentences into Kokoro while
later text is still downloading. Cloud inference does not hold Ron's local CPU scheduler, so
microphone transcription and speech synthesis are not queued behind an Ollama generation.

Use `/latency` to compare ASR, first-text and first-audio timings. If recognition remains the slow
stage, benchmark `small.en` as a lighter alternative to `distil-large-v3`; change only one stage
at a time.

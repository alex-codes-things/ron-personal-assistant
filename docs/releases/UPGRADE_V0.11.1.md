# Ron v0.11.1 - Free Groq Intelligence

This release replaces paid OpenAI as the recommended cloud path with Groq's free GPT-OSS 120B
service. OpenAI and fully local Ollama modes remain available.

## Changes

- Added a dependency-free, streaming Groq Chat Completions client.
- Added `groq` provider mode and made `auto` prefer Groq when both cloud keys exist.
- Added safe handling for rejected keys, free-plan rate limits, outages and malformed streams.
- Added low-effort GPT-OSS reasoning while excluding reasoning text from spoken responses.
- Added a 24,000-character normal prompt bound for the free tier.
- Extended cold Ollama fallback to explicit Groq and OpenAI provider modes.
- Added `scripts/check_groq_ai.py`, tests, documentation and `.env.example` settings.

The Groq service can propose text and plans but cannot directly execute a computer action. Ron's
existing local tool registry, validation and confirmations remain authoritative.

## Existing project upgrade

Keep your current `.env`, `.venv` and `runtime` folders when copying this release over the old
project. Then add:

```dotenv
RON_AI_PROVIDER=groq
GROQ_API_KEY=replace_with_your_own_groq_key
RON_GROQ_MODEL=openai/gpt-oss-120b
RON_GROQ_REASONING_EFFORT=low
RON_GROQ_TIMEOUT=30
RON_GROQ_MAX_PROMPT_CHARACTERS=24000
RON_AI_FALLBACK_LOCAL=true
```

Verify before removing anything:

```powershell
python .\scripts\check_groq_ai.py
python -m ron
```

Inside Ron, type `/status` and `/latency`. After several successful days you may set
`RON_AI_FALLBACK_LOCAL=false` and use `scripts\cleanup_project.ps1 -RemoveOllamaModel` if the local
model's disk space is needed.

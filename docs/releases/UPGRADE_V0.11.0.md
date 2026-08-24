# Ron v0.11.0 - Hybrid Cloud Intelligence

This release removes the local language model from Ron's normal latency path while preserving the
existing local voice, memory, tablet, tool allowlist and confirmation boundaries.

## Included changes

- Added `auto`, `openai` and `ollama` AI-provider modes.
- Added dependency-free OpenAI Responses API streaming with bounded inputs and safe errors.
- Added cloud-first operation with an optional cold Ollama fallback.
- Kept deterministic commands local and immediate.
- Kept all computer actions behind the existing local planner validation and tool registry.
- Prevented a partial cloud response from being mixed with local fallback text.
- Removed cloud inference from the CPU scheduler used by Whisper and Kokoro.
- Moved optional Kokoro phrase prewarming off the startup path.
- Added an API connection check, provider status, cleanup helper and configuration documentation.
- Kept `.env`, credentials, runtime data and downloaded models out of the release ZIP.

## Upgrade an existing `.env`

Do not replace the existing `.env`. Add:

```dotenv
RON_AI_PROVIDER=openai
OPENAI_API_KEY=replace_with_your_own_api_key
OPENAI_PROJECT_ID=
RON_OPENAI_MODEL=gpt-5.4-mini
RON_OPENAI_REASONING_EFFORT=none
RON_OPENAI_TIMEOUT=30
RON_AI_FALLBACK_LOCAL=false
```

For a temporary local fallback, use `RON_AI_PROVIDER=auto`,
`RON_AI_FALLBACK_LOCAL=true`, and `RON_MODEL_KEEP_ALIVE=2m` instead.

## Verify

```powershell
python -m pip install -e ".[voice,desktop-preview,dev]"
python .\scripts\check_cloud_ai.py
python -m pytest
python -m ron
```

Inside Ron, run `/status` and `/latency`. The status should name the OpenAI provider, and normal
voice questions should begin speech from the first streamed sentence instead of waiting for local
Ollama generation.

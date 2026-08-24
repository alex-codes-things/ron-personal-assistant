# Ron v0.12.1 - Groq Voice Error Repair

This maintenance release makes Groq speech failures actionable. Groq returns a structured JSON
reason for rejected requests, but v0.12.0 discarded it and printed only `HTTP 400`. Ron now shows
a bounded, secret-redacted reason and recognizes the one-time Orpheus model-terms requirement.

## Upgrade

1. Stop Ron with `Ctrl+C`.
2. Keep your existing private `.env` and `runtime` folder.
3. Extract v0.12.1 into a clean folder and copy those two items across.
4. Reinstall the editable project:

   ```powershell
   python -m pip install -e ".[voice,dev]"
   ```

5. While signed into Groq as the organization admin, open
   <https://console.groq.com/playground?model=canopylabs%2Forpheus-v1-english> and accept the
   Orpheus terms once.
6. Test Groq directly, without loading local Kokoro:

   ```powershell
   python .\scripts\check_groq_voice.py --play
   ```

7. Start Ron with `python -m ron`.

No `.env` change and no new API key are required for this repair.

# Ron v0.12.2 - Groq Streaming WAV Repair

Groq Orpheus returns a complete audio response but marks the RIFF and data sizes as
`0xFFFFFFFF`, meaning the length was unknown when the streaming WAV header was created. Python's
standard WAV reader therefore reports 2,147,483,647 frames even for a short reply. v0.12.1
mistook that placeholder for the real duration and rejected otherwise valid audio.

v0.12.2 calculates the duration from the PCM bytes actually downloaded. It still enforces the
16 MiB response limit, supported mono PCM format, valid sample rates, complete-frame alignment,
and maximum 120-second audio duration.

## Upgrade

1. Stop Ron with `Ctrl+C`.
2. Extract v0.12.2 into a clean folder.
3. Copy your existing private `.env` and `runtime` folder into it.
4. Activate Python 3.12 and reinstall:

   ```powershell
   python -m pip install -e ".[voice,dev]"
   ```

5. Test and play one direct Groq phrase:

   ```powershell
   python .\scripts\check_groq_voice.py --play
   ```

6. Start Ron with `python -m ron`.

No `.env` change or new model download is required.

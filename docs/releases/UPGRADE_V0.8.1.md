# Upgrade to Ron v0.8.1 — Low-latency wake handoff

v0.8.1 fixes the real-world delays found after v0.8.0.

## What changed

- The always-on Sherpa KWS now defaults to an accent-friendly `high` sensitivity profile. It keeps existing `.env` threshold/score values as upper bounds, so an old `0.35 / 1.5` configuration automatically runs at the more sensitive live profile without editing `.env`. The high profile lowers the trigger threshold and increases the keyword bonus score.
- The generated `Hey Ron` keyword now includes both `AA1` and rounded `AO1` pronunciations for `Ron` when the model vocabulary supports them. This better covers South African/Afrikaans-English pronunciation while mapping both to the same wake event.
- A short KWS-confirmed wake-only utterance can open the conversational handoff without waiting for `distil-large-v3` to transcribe the words `Hey Ron` again. This fast path cannot execute a tool; it can only make Ron acknowledge and listen.
- The acknowledgement phrases are prewarmed **before** live microphone listening begins. The tiny synthesized clips are also persisted under `runtime/cache/voice_ack/`, so later launches can reuse them without re-synthesizing.
- The special echo guard after `Yes?` is now 60 ms by default instead of using the normal 300 ms full-reply guard.
- The follow-up window is now 8 seconds and, importantly, starts **after** the acknowledgement has finished playing.
- Longer wake+command utterances still use the normal Whisper verification/correction path.

## Existing `.env`

You do not need to replace your `.env`. New settings have safe defaults when absent:

```env
RON_WAKE_SENSITIVITY=high
RON_WAKE_FOLLOWUP_WAIT=8.0
RON_WAKE_ACK_ECHO_GUARD=0.06
RON_WAKE_FAST_HANDOFF=true
RON_WAKE_FAST_SEGMENT_SECONDS=1.05
```

If the room produces too many false wake acknowledgements, set:

```env
RON_WAKE_SENSITIVITY=balanced
```

A false wake acknowledgement still cannot itself execute an action; Ron waits for a fresh follow-up utterance.

## Model placement

Keep the **active** wake, VAD, Whisper, and TTS models on the laptop SSD. The 3.5 TB external drive is excellent for alternate models, archives, visual memory, recordings, and future training data, but moving the models used on every interaction to a USB hard drive can make cold starts slower. Capacity is not the bottleneck in this wake path; latency and RAM residency are.

## Verify

```powershell
python -m pytest
python -m ron
```

Then say **only** `Hey Ron!`, wait for the acknowledgement, and answer immediately after it ends.

# Phase 8 CTCSS Tone Decoding

Marker: `PHASE8_CTCSS_TONE_GATE_V0_6G`

Phase 8 implements a target-frequency CTCSS detector using a 320 ms Goertzel
window over the existing 8 kHz mono PCM stream.

## Behavior

- Existing channels remain carrier-only after migration.
- Entering a CTCSS frequency enables tone observation.
- Checking **Tone Gate** requires the configured CTCSS tone before browser
  audio, activity history, or recording opens.
- Two consecutive detector matches establish lock.
- Three consecutive misses release lock.
- A 480 ms audio prebuffer preserves the beginning of a tone-gated call.
- A short tone-lock hold prevents brief detector dropouts from chopping speech.

## Status and activity fields

- Configured CTCSS frequency
- Detected CTCSS frequency
- Tone-gate-required state
- Lock state and confidence
- Detector power/dominance metrics
- Rejected and accepted frame counters

DCS remains configuration metadata only and is not decoded in this phase.

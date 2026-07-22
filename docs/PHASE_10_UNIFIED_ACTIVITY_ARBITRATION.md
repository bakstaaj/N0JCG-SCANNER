# Phase 10 Unified Activity and Audio Arbitration

Marker: `PHASE10_UNIFIED_ACTIVITY_ARBITRATION_V0_6I`

Phase 10 gives the main dashboard one normalized view across P25, analog 2 m,
and analog 70 cm.

## Unified API

`GET /api/activity/unified`

The response contains:

- browser audio owner
- active channel/talkgroup descriptor
- non-preemptive arbitration policy
- per-source service and audio statistics
- normalized P25 and analog recent history
- analog recording playback URLs

## Arbitration

The established policy remains **current transmission wins**. A source that
owns browser audio is never preempted. After the release hold expires, a 40 ms
acquisition window handles near-simultaneous new transmissions using this
tie-break order:

1. P25 voice
2. P25 control/audio
3. Analog 2 m
4. Analog 70 cm

The acquisition window only resolves a new owner; it does not interrupt an
existing transmission.

# Phase 6 Analog Activity History

Marker: `PHASE6_ANALOG_ACTIVITY_HISTORY_V0_6E`

Phase 6 adds transmission lifecycle tracking to both analog workers.

## Completed event fields

- Band / worker role
- RTL serial
- Channel ID and name
- Frequency and mode
- Start and end timestamps
- Duration
- Peak RMS
- Active PCM frame count
- End reason
- Stored CTCSS/DCS metadata
- Recording intent metadata

Only completed transmissions are appended to the bounded JSONL history. A
currently active transmission is exposed separately in worker status.

## API

- `GET /api/analog/activity`
- `POST /api/analog/activity/clear`

History is stored under `runtime/activity/analog_2m.jsonl` and
`runtime/activity/analog_70cm.jsonl`, capped at 1,000 events per band.

Recording and tone decoding are not implemented in this phase.

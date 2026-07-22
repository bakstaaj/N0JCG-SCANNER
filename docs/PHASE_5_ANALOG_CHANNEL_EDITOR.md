# Phase 5 Analog Channel Editor

Marker: `PHASE5_ANALOG_CHANNEL_EDITOR_V0_6D`

Phase 5 moves analog scanning configuration into the PI-SCANNER web interface.

## Channel fields

- Enabled
- Name
- Frequency
- FM / NFM / AM
- Priority
- RF gain
- RMS squelch threshold
- Activity hold
- Reply/resume delay
- Optional CTCSS metadata
- Optional DCS metadata
- Recording intent

CTCSS/DCS decoding and audio recording are not activated in this phase. Their
values are validated and stored for later receiver-worker extensions.

## API

- `GET /api/analog/config`
- `POST /api/analog/config/save`

Saving writes a timestamped backup and restarts only analog services that were
already running. OP25 and P25 profile files are not modified.

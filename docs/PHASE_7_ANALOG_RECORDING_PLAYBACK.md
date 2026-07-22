# Phase 7 Analog Recording and Playback

Marker: `PHASE7_ANALOG_RECORDING_PLAYBACK_V0_6F`

Phase 7 activates the channel-level `recording_enabled` field introduced in
Phase 5.

## Recording behavior

- Recording is off unless the channel's **Record** checkbox is enabled.
- Files are mono, 16-bit PCM WAV at 8 kHz.
- Recording begins on the first valid RMS activity frame.
- Quiet frames are retained while the transmission hold/reply window remains
  open.
- Maximum single-file duration is 15 minutes.
- Default retention is 14 days, 500 files per band, and 2 GB total.
- Completed activity records contain the recording URL, size, duration, and
  truncation state.

## API

- `GET /api/analog/recordings`
- `GET /api/analog/recordings/file?role=...&filename=...`
- `POST /api/analog/recordings/clear`
- `POST /api/analog/recordings/delete`

The Analog Activity table includes an in-browser audio player and download link
for recorded transmissions.

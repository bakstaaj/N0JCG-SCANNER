# PI P25 Scanner Architecture

## V0.1 architecture

The first implementation is a small Python web application that controls an external P25 decoder engine and exposes a minimal browser UI.

```text
Browser UI
   |
   | HTTP / JSON / optional audio stream
   v
Python backend
   |
   | process wrapper + generated config
   v
P25 decoder engine on Pi
   |
   | RTL-SDR control/voice receivers
   v
NooElec NESDR Nano 2+ radio(s)
```

## Decoder engine model

The backend should not initially implement a full P25 demodulator. It should:

1. validate local system configuration,
2. generate decoder-specific runtime files,
3. launch the decoder engine,
4. monitor process liveness,
5. parse status/log metadata when available,
6. expose a stable web/API contract, and
7. stop the decoder cleanly.

OP25 is the preferred first decoder target because it is Linux/Pi oriented and already supports P25 trunk-following workflows. SDRTrunk is a useful reference for behavior and protocol interpretation, but the Java GUI/application stack is intentionally outside V0.1 scope.

## One-SDR vs two-SDR mode

### One-SDR mode

One RTL-SDR alternates between the active control channel and assigned voice channels. This is useful for early testing and lower hardware cost, but it may miss control-channel grants while tuned away.

### Two-SDR mode

One RTL-SDR remains on the control channel while the second follows voice grants. This is the preferred operational model for trunked scanning.

Receiver roles:

- `p25_control`
- `p25_voice`

Persistent code must resolve roles from RTL EEPROM serials, not Linux runtime indexes.

## Minimal API contract

Initial endpoints:

- `GET /api/status`
- `GET /api/config`
- `POST /api/config`
- `POST /api/scanner/start`
- `POST /api/scanner/stop`

Initial status fields:

- `ok`
- `scanner_state`
- `decoder_engine`
- `receiver_roles`
- `active_control_frequency_hz`
- `active_voice_frequency_hz`
- `active_tgid`
- `active_talkgroup_label`
- `p25_phase`
- `encrypted`
- `muted`
- `last_event`
- `warnings`

## Audio model

V0.1 should prefer the simplest working path first. Acceptable first paths are:

- decoder plays audio to Pi default audio device, or
- backend exposes a local stream/file endpoint after decoder audio is stable.

Browser audio is desired, but decoder reliability comes first.

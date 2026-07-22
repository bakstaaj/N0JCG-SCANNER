# Scalable Dedicated-Control P25 Multi-Receiver Runtime

This feature preserves the validated P25 UI/backend checkpoint and changes only
the decoder launch and audio routing layers.

## Receiver policy

- `roles.p25_control.rtl_serial` remains the first OP25 channel.
- Every other connected RTL serial matching `^0000025[0-9]$` becomes a voice
  receiver.
- The configured `p25_voice` serial is placed first in the voice pool.
- With the present hardware, `00000251` is control and `00000252` is voice.
- Future `00000250`, `00000253`, and other matching serials are added
  automatically at scanner start.

The control channel receives a private whitelist containing only TGID `0`.
Because real P25 group IDs do not use that value, OP25 can decode control
signaling on that receiver but cannot assign a voice call to it. Voice receivers
inherit the normal system whitelist.

## Native OP25 topology

The wrapper generates a native `multi_rx.py` JSON configuration with one tunable
device and one channel per receiver. Every channel uses the same
`trunking_sysname`, allowing boatbod OP25 to share one P25 system state across
the dedicated control receiver and all voice receivers.

## Audio topology

Each receiver uses a stable UDP port based on its final serial digit:

- `00000250` -> `23500`
- `00000251` -> `23501`
- `00000252` -> `23502`
- ...
- `00000259` -> `23509`

`p25_audio_pool.py` listens to all ten ports, selects one non-silent source, and
forwards it to the existing raw browser-audio bridge on `127.0.0.1:23456`.
Streams are never mixed.

## Fallback

When the control receiver or all voice receivers are missing, the wrapper records
the reason in `runtime/op25/multi_rx_state.json` and executes the preserved
single-receiver `rx.py` command.

## UI and backend

No web files and no backend Python modules are modified by this feature.

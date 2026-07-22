# P25 Recovery Checkpoint — Confirmed 18081 UI

This checkpoint restores the PI-P25-SCANNER source to the exact state selected
through the isolated static UI review at:

`http://127.0.0.1:18081/`

## Source

- Source repository: `bakstaaj/PI-SCANNER`
- Source commit: `559b05d0610fc09ec7cb2683d7e72c30067e77ab`
- Source commit subject: `Add Phase 2 receiver inventory foundation`
- Destination repository: `bakstaaj/PI-P25-SCANNER`

## Raspberry Pi validation

Validated on July 22, 2026 against:

- Host: `PI-SDR`
- LAN address: `192.168.68.137`
- Active application path: `/home/pi/PI-P25-SCANNER`
- Backend/UI port: `8070`
- Raw browser-audio port: `8072`
- OP25 audio UDP destination: `127.0.0.1:23456`
- Control receiver: `00000251`
- Receiver inventory: seven of seven configured RTL receivers present

The validated runtime used the single-receiver OP25 `rx.py` path with browser
audio flags `-w -W 127.0.0.1 -u 23456`.

## Validation result

The restore completed with:

- correct UI served;
- backend API available;
- receiver inventory API reporting all seven expected receivers;
- raw browser-audio service active;
- WAV test tone and live stream endpoints available;
- scanner process stable for the full three-minute observation;
- intermittent control lock observed, with RF/control stability left as the next
  isolated area of work.

## Scope

This checkpoint intentionally contains source code only. Runtime configuration,
RadioReference credentials, named local settings, logs, diagnostic reports, and
device-specific state remain ignored and are not committed.

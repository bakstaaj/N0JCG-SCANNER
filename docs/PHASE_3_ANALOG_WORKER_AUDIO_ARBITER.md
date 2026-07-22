# Phase 3 Analog Worker and Browser Audio Arbitration

Marker: `PHASE3_ANALOG_WORKER_AUDIO_ARBITER_V0_6B`

## Delivered

- Configurable `analog_2m` worker using RTL serial `00000440`.
- Reserved `analog_70cm` definition using serial `00000144`.
- Source-aware audio arbitration on:
  - P25 control audio: UDP 23456
  - P25 voice audio: UDP 23457
  - Analog 2 m audio: UDP 23458
  - Analog 70 cm audio: UDP 23459
- First active/current transmission owns the browser stream until its source is
  quiet for the configured release interval.
- Backend status and start/stop API for the 2 m worker.
- Radio Setup UI controls and arbiter status.

## Initial channel

The Phase 3 template contains 146.520 MHz as a disabled test channel. The worker
service is installed but left stopped/disabled after deployment.

## API

- `GET /api/analog/status`
- `POST /api/analog/2m/start`
- `POST /api/analog/2m/stop`

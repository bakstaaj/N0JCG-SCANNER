# Isolated VHF Analog Scanner Worker

This phase adds a hardware-testable VHF scanner without changing the proven P25
audio path.

## Bound hardware

- Role: `analog_2m` (the project's VHF receiver role)
- RTL-SDR serial: `00000144`
- Channel source: the 31 enabled VHF rows in the cabin CSV configuration
- Worker PCM output: `127.0.0.1:23458`
- Separate browser audio: `http://DEVICE-IP:8073/audio.wav`
- Separate audio status: `http://DEVICE-IP:8073/api/audio/status`

The existing P25 browser stream remains on port 8072.

## Scanner behavior

The worker keeps one `rtl_tcp` process attached by serial number. It groups the
uploaded VHF channel list into FFT survey segments and evaluates energy only at
those configured frequencies. A candidate is retuned off-center, checked for
carrier SNR, frequency error, and real NFM audio activity, then demodulated in
the worker and forwarded as 8 kHz PCM to the unified audio arbitrator. When the
carrier or audio ends, the worker returns to FFT survey mode. Noise-only and
silent carriers are rejected temporarily so scanning can continue. Atomic
runtime status is written to `runtime/status/analog_2m.json`.

CTCSS and DCS values remain channel metadata. Tone gating is deferred until
the FFT, NFM audio, and noise-rejection path has passed on the cabin hardware.

## Safety boundary

The systemd units are included but are not installed or enabled by the
development implementation script. Deployment and hardware activation are a
separate validation step. The UHF receiver and shared P25/VHF/UHF arbitration
remain disabled.

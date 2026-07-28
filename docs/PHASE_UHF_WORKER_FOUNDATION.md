# Isolated UHF Analog Scanner Worker

This phase adds a hardware-testable UHF scanner without changing the proven P25
audio path.

## Bound hardware

- Role: `analog_70cm` (the project's UHF receiver role)
- RTL-SDR serial: `00000440`
- Channel source: the 7 enabled UHF rows in the cabin CSV configuration
- Worker PCM output: `127.0.0.1:23459`
- Separate browser audio: `http://DEVICE-IP:8074/audio.wav`
- Separate audio status: `http://DEVICE-IP:8074/api/audio/status`

The existing P25 browser stream remains on port 8072.

## Scanner behavior

The worker invokes `rtl_fm` by serial number, visits enabled channels in
priority/frequency order, ignores the initial tuner-settle interval, computes
PCM RMS squelch, holds on active audio, observes the configured reply delay,
and then resumes scanning. It writes atomic runtime status to
`runtime/status/analog_70cm.json`.

CTCSS and DCS values are preserved as channel metadata in this phase. Tone
gating is intentionally deferred until basic tuning, squelch, and audio quality
have passed on the cabin hardware.

## Safety boundary

The systemd units are included but are not installed or enabled by the
development implementation script. Deployment and hardware activation are a
separate validation step. The UHF receiver and shared P25/UHF/UHF arbitration
remain disabled.

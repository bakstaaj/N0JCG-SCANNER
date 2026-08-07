# PI Scanner v1.0.2

## Reliability fixes

- Removes the systemd ordering cycle between the unified audio arbitrator and
  the P25 audio pool.
- Removes obsolete standalone VHF and UHF audio-service dependencies from the
  analog worker units.
- Ensures the unified arbitrator owns UDP ports 23456, 23458, and 23459 after
  reboot.
- Keeps the standalone VHF and UHF browser-audio services disabled.

## Audio latency fix

- Restores raw PCM browser playback through `/audio.pcm`.
- Uses the Web Audio API rather than the browser's native WAV buffering.
- Caps queued playback at approximately 0.35 seconds to prevent accumulated
  VHF/UHF playback delay.

## Deployment

- Adds version-controlled production systemd unit files.
- Adds `tools/install_audio_runtime_units.sh` for repeatable deployment and
  validation.

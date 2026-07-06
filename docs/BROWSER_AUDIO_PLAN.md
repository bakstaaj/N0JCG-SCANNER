# V0.3C Browser Audio Plan

The PI-P25-SCANNER application uses the Raspberry Pi as the RF and decoder host.
Scanner listening audio should play on the browser host, not on the Pi audio
device.

## V0.3C scaffold

V0.3C proves the browser-side audio permission and playback path first:

- the dashboard includes a Browser Audio Output panel,
- the user must click Enable Browser Audio to satisfy browser autoplay policy,
- the browser creates a Web Audio API context, and
- the browser can play a generated test tone without any committed WAV assets.

No Pi speaker, ALSA output, HDMI audio, or desktop audio configuration is
required for this milestone.

## Next milestone

The next milestone is to bridge clear OP25 decoded audio into the browser audio
path. That should be added as a separate feature after the browser output path is
confirmed by the test tone.

Encrypted traffic remains out of scope for audio playback. Encrypted calls must
be muted/skipped only.

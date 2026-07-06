# V0.3 Browser Audio Plan

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

## V0.3D live bridge

V0.3D proves clear OP25 decoded audio can be delivered to a browser host:

- OP25 runs from the validated runtime marker,
- OP25 UDP audio output is directed to localhost on the Pi,
- a small Python bridge exposes a browser-readable WAV stream at `/audio.wav`,
- encrypted traffic remains muted/skipped by the OP25 crypt behavior, and
- audio playback happens on the browser host.

## V0.3E audio quality stabilization

V0.3E adds conservative stream-quality improvements to the bridge:

- a small jitter/prebuffer before audible packets,
- frame-boundary de-click smoothing,
- underrun and silence-chunk counters in `/api/audio/status`, and
- live-test options for tuning prebuffer and de-click behavior.

The first known-good values are `--prebuffer-chunks 8` and
`--declick-samples 12`. If audio sounds delayed but clean, reduce prebuffer. If
metallic clicks remain around transmission starts/stops, increase de-click
slightly. P25 vocoder artifacts caused by weak RF or marginal decode may still
be audible and should be treated separately from browser stream artifacts.

# Browser Audio Plan

The Raspberry Pi is the RF and OP25 decoder host. Scanner listening audio is
played on the browser host, not on the Pi speaker, HDMI audio, ALSA device, or
Pi desktop audio session.

## Proven milestones

- V0.3C proved browser-host playback with a generated Web Audio test tone.
- V0.3D proved real clear OP25 voice audio through the browser path.
- V0.3E attempted jitter buffering and de-click smoothing, but live testing made
  the audio worse, producing metallic/garbled voice-like bursts.
- V0.3F restores the raw V0.3D-style bridge as the working baseline.

## Current baseline

The working audio path is:

```text
OP25 UDP PCM audio -> Pi browser-audio bridge -> HTTP WAV stream -> browser host speakers
```

The bridge serves:

```text
/audio.wav
/api/audio/status
/test-tone.wav
```

The raw bridge intentionally does not smooth, de-click, or mix PCM frames across
P25 voice gaps. It fills no-audio periods with silence frames so the browser
stream stays open.

## Safety

Encrypted traffic remains out of scope for playback. Encrypted calls must be
muted/skipped only. The project must not attempt decryption, key recovery, key
loading, or bypass.

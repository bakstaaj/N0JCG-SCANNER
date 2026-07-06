# V0.3D OP25 Browser Audio Bridge

V0.3C proved that the browser host can create an audio context and play a test
tone. V0.3D starts the first live scanner-audio path while keeping the Raspberry
Pi as the RF/decoder host only.

## Design

The V0.3D live test uses OP25's UDP/Wireshark output mode instead of Pi speaker
audio. The audio bridge listens on the Pi loopback interface for OP25 UDP PCM
frames and exposes a browser-readable WAV stream.

Default ports:

- backend/UI: `8070`
- OP25 HTTP terminal: `18091` on Pi loopback
- browser audio bridge HTTP: `8072`
- OP25 UDP PCM input to bridge: `23456` and `23457` on Pi loopback

The browser stream URL during the live test is:

```text
http://<pi-ip>:8072/audio.wav
```

## How to run from MSYS2

```bash
cd ~/sdrdev/PI-P25-SCANNER
./tools/msys2_run_pi_browser_audio_live_test.sh --seconds 600
```

Open the printed `BROWSER_AUDIO_URL` while the script is running.

## What counts as success

- The browser opens the stream without an error.
- The bridge status shows OP25 UDP packets during active clear voice traffic.
- Clear voice is heard through the browser host speakers.

No packets during a quiet window is not automatically a failure; it may mean no
allowed clear talkgroup was active during the test. In that case, run the test
longer or temporarily broaden the talkgroup list for discovery.

## Safety scope

Encrypted calls remain muted/skipped only. This bridge must not attempt to load,
recover, bypass, infer, or use encryption keys.

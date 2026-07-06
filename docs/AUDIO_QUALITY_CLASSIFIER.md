# V0.3H Audio Quality Classifier

V0.3H keeps the raw OP25-to-browser audio bridge and adds a quality classifier.
The goal is to distinguish these cases after a live listening window:

- clear audio with normal gaps,
- no clear traffic during the test window,
- browser stream starvation or underruns,
- encrypted/protected traffic indicators,
- RF decode problems such as high BER, high D-Error, poor tuning, or possible simulcast distortion.

The classifier reads:

- the OP25 stderr log,
- the final browser-audio bridge `/api/audio/status` JSON,
- the live-test report.

It prints a `QUALITY_CLASSIFICATION=...` line into the pulled command log. It
also writes a JSON report under `.p25_browser_audio_live_reports/` on the Pi.

## Diagnostic run

The normal raw audio test remains:

```bash
./tools/msys2_run_pi_browser_audio_live_test.sh --seconds 600
```

To try to expose OP25 D-Error, BER, or frequency tracking diagnostics, run:

```bash
./tools/msys2_run_pi_browser_audio_live_test.sh --seconds 600 --op25-verbosity 10
```

If OP25 does not print explicit BER or D-Error metrics, the classifier still
uses proxy evidence such as audio packet counts, flag packets, underruns,
encryption-related log lines, generic error/sync/CRC lines, and time since the
last audio packet.

Encrypted traffic remains skipped/muted only. The classifier must not attempt to
recover, bypass, or decode encrypted content.

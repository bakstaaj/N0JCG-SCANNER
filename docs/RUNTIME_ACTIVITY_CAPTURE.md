# V0.2I Live Activity Capture

V0.2I adds a repeatable live evidence capture for field testing the validated
backend/OP25 path.

The capture tool polls the backend `/api/status` endpoint while the scanner is
running and writes:

- raw status snapshots as JSONL,
- a human-readable summary report,
- a machine-readable summary JSON file, and
- a copy of the summary under `runtime/evidence/`.

The tool is intentionally observational. It does not change the validated OP25
command marker, decoder arguments, runtime config schema, or encryption policy.
If it starts the scanner through the backend API, it stops only the process it
started. If the scanner was already running before capture, it leaves it running.

## Self-test

```bash
./tools/pi5_p25_live_activity_capture.sh --self-test
```

## TOPAZ/TRWC live capture

```bash
./tools/p25_init_topaz_trwc_test_config.sh --apply --yes
./tools/pi5_p25_live_activity_capture.sh --seconds 180 --interval 3 --yes
```

A quiet RF window is not a failure. The tool reports a warning when no TGIDs are
observed so the capture remains useful for control-channel, start/stop, and
backend/service evidence.

Set `P25_SCANNER_CAPTURE_REPORT_DIR` to redirect reports during automated local
self-tests without leaving report files in the repository tree.

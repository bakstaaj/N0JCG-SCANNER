# Live Evidence Analysis

V0.2J adds an offline analyzer for evidence captured during bounded live RF
testing.

The analyzer reads JSON status snapshots from `runtime/evidence/` or
`.p25_live_activity_capture_reports/` and produces a Markdown plus JSON
summary under `.p25_live_evidence_analyze_reports/`.

It is intentionally observational only. It does not start OP25, stop OP25,
change receiver roles, tune frequencies, alter the validated command marker, or
attempt any encrypted-audio handling beyond reporting encrypted/muted metadata
already exposed by the backend.

Typical Pi workflow:

```bash
cd ~/scanner
./tools/p25_init_topaz_trwc_test_config.sh --apply --yes
./tools/pi5_p25_live_activity_capture.sh --seconds 180 --interval 3 --yes
./tools/pi5_p25_live_evidence_analyze.sh --latest
```

Strict mode is available when a run is expected to include talkgroup activity:

```bash
./tools/pi5_p25_live_evidence_analyze.sh --latest --strict
```

Default mode treats a quiet RF window as a warning, not a failure.

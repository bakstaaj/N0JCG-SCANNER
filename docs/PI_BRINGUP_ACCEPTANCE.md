# Pi Bring-Up Acceptance Bundle

`tools/pi5_p25_bringup_acceptance.sh` is the single Pi-side bring-up check for the current non-live decoder milestones.

It is intentionally non-invasive:

- it does not install packages,
- it does not clone or build OP25,
- it does not start live OP25 decoding,
- it does not transmit,
- it treats missing RF traffic as a warning when the application and probes are otherwise healthy.

## Run on the Raspberry Pi

```bash
cd ~/sdrdev/PI-P25-SCANNER
./tools/pi5_p25_bringup_acceptance.sh
```

The bundle runs the repo validator, config validator, config API smoke validator, Pi preflight, runtime probe, OP25 install/capability probe, and RTL role probe when those tools are present.

Reports are written under:

```text
.p25_pi_bringup_acceptance_reports/
```

A successful current-stage bring-up ends with:

```text
FINAL: PASS
```

Warnings are acceptable for optional decoder tooling or quiet RF conditions until the project reaches a milestone that explicitly requires live P25 decode.

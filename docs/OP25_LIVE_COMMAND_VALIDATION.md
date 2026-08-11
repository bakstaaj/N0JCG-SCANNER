# OP25 Live Command Validation

V0.1K adds a bounded Pi-side probe for validating the exact OP25 foreground command before scanner enables backend live launch.

## Purpose

The guarded source install proves that OP25 source and installed components are present. This step proves that the generated PI-P25 runtime config, selected RTL serial, and OP25 command-line options can start together on the Pi.

## Guarded workflow

Run from the Raspberry Pi repository root:

```bash
cd ~/scanner
./tools/pi5_p25_op25_live_command_probe.sh --dry-run
./tools/pi5_p25_op25_live_command_probe.sh --rx-smoke --seconds 20 --yes
```

The dry run prints and records the candidate `rx.py` command without starting OP25. The smoke run uses `timeout`, writes logs under `.p25_op25_live_command_probe_reports/`, and treats a timeout exit as success when the process starts and remains alive for the requested bounded window.

## What this does not do

- It does not install OP25.
- It does not create or change systemd services.
- It does not enable `/api/scanner/start` live OP25 launch.
- It does not attempt encrypted audio decoding or key handling.

## Acceptance rule

A successful smoke run may be used as evidence for updating `docs/OP25_INSTALL_DECISION.md` with the validated command template. Backend launch remains disabled until a later patch explicitly wires the validated template into the backend status/start path.

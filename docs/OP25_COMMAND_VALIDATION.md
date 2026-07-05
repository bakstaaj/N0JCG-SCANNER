# OP25 Command Validation

This document records the guarded post-install command validation path for PI-P25-SCANNER.

## Purpose

After OP25 is installed from the guarded source path, the project still does not enable backend live decoder launch automatically. The next step is to verify which installed OP25 app entrypoint is usable on the Pi and capture the exact command evidence.

## Probe

Run on the Raspberry Pi from the repository root:

```bash
./tools/pi5_p25_op25_postinstall_probe.sh
```

The probe is bounded and non-live. It does not tune the radio and does not start a persistent decoder. It checks:

- active PI-P25 runtime config,
- OP25 source marker,
- generated OP25 runtime files,
- OP25 source app paths,
- installed command discovery,
- Python imports commonly needed after OP25 install,
- `rx.py --help` and `multi_rx.py --help` output using timeouts,
- candidate JSON availability.

## Evidence output

The probe writes reports under:

```text
.p25_op25_postinstall_probe_reports/
runtime/settings/op25_postinstall_probe.json
```

## Backend launch rule

`/api/scanner/start` must remain guarded until this document records a validated command template and an operator has run a bounded live control-channel test on the Pi.

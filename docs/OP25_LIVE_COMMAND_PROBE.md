# OP25 Live Command Probe

This project keeps OP25 live launch disabled until the exact Pi command is validated.

`tools/pi5_p25_op25_live_command_probe.sh` is the bounded validation tool for that command. It generates the OP25 runtime config, builds a candidate `rx.py` command from the active local scanner config, and can run a foreground smoke test under `timeout`.

## Safe default

```bash
./tools/pi5_p25_op25_live_command_probe.sh --dry-run
```

Dry-run mode prints candidate commands only. It does not start OP25.

## Bounded smoke test

```bash
./tools/pi5_p25_op25_live_command_probe.sh --rx-smoke --seconds 20 --yes
```

Smoke mode starts OP25 only in the foreground and only under a bounded timeout. It does not change services, does not enable backend live launch, and does not install packages.

## Device selection

The probe first tries the stable RTL serial from the `p25_control` receiver role. If OP25 exits early and the probe can map that serial to a current RTL runtime index, it also tries the runtime index candidate. This keeps stable serials as the preferred project config while recognizing that OP25/osmosdr builds may vary in serial support.

## Failure evidence

When OP25 exits early, the probe classifies the smoke log and prints the log tail into the report. Common classifications include import errors, option errors, SDR open errors, config-file errors, and Python runtime errors.

A successful smoke test writes:

```text
runtime/settings/op25_validated_rx_command.env
```

This file is evidence only. A later patch must explicitly consume it before `/api/scanner/start` live launch is enabled.

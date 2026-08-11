# Guarded OP25 Source Path

V0.1I adds a guarded OP25 source path for Raspberry Pi 5 validation. It does not enable live scanner launch and does not run OP25 by default.

## Upstream candidate

The current source candidate is the `boatbod/op25` repository. This path is selected for validation because it provides command-line OP25 tooling and includes both `rx.py` and `multi_rx.py` application paths.

## Guarded workflow

Run these on the Raspberry Pi from the scanner repository root.

Dry-run the planned source path first:

```bash
./tools/pi5_p25_op25_source_install.sh --dry-run
```

Clone the upstream source only, without installing or building packages:

```bash
./tools/pi5_p25_op25_source_install.sh --clone-only --yes
```

After the source tree exists, collect a non-invasive command-candidate report:

```bash
./tools/pi5_p25_op25_command_candidate.sh
```

A full upstream install/build is intentionally gated behind explicit flags:

```bash
./tools/pi5_p25_op25_source_install.sh --run-upstream-install --yes
```

Do not run the full upstream install until the dry run and clone-only evidence are reviewed.

## What this milestone does not do

- It does not enable backend live OP25 launch.
- It does not start long-running decoder processes.
- It does not add encryption decryption or key handling.
- It does not make runtime RTL indexes persistent config.

## Evidence locations

- Source install reports: `.p25_op25_source_install_reports/`
- Command candidate reports: `.p25_op25_command_candidate_reports/`
- Source path marker: `runtime/settings/op25_source_path.env`
- Command candidate JSON: `runtime/settings/op25_command_candidate.json`

# OP25 Install and Capability Decision

This document records the supported decoder-install decision for the PI P25 Scanner.

## Current decision state

V0.1C does not choose or install OP25 yet. It adds repeatable evidence collection so the supported Pi 5 / Trixie OP25 path can be selected from observed runtime facts instead of assumptions.

## Goals

The supported OP25 path must prove:

- command-line OP25 tools are discoverable from the Pi runtime,
- generated `runtime/op25/trunk.tsv` and talkgroup files match the selected invocation style,
- the decoder can open the intended RTL-SDR receiver by stable serial or by a documented runtime mapping,
- the control channel can be tuned without launching persistent service behavior,
- Phase I support is clearly reported,
- Phase II support is clearly reported as supported, unsupported, or unknown,
- live backend launch remains disabled until the exact command template is validated.

## Evidence script

Run on the Raspberry Pi 5 from the repository root:

```bash
./tools/pi5_p25_op25_install_probe.sh
```

The probe is intentionally non-invasive. It does not install packages, clone external repositories, build OP25, or start a long-running decoder. It checks the local environment and writes a report under `.p25_op25_install_probe_reports/`.

## Decision fields to record

When a path is selected, update this file with:

- OP25 source or package path,
- install command or build script,
- executable path,
- verified command template,
- supported P25 phases,
- RTL-SDR selection method,
- validation report path,
- date and Pi hardware evidence.

## Guardrails

Do not enable `/api/scanner/start` live OP25 launch by default until this document contains the validated command template.


## V0.1I guarded source candidate

The current guarded source candidate is `https://github.com/boatbod/op25.git` on branch `master`, cloned by default to `~/op25` only when `tools/pi5_p25_op25_source_install.sh --clone-only --yes` is run on the Pi.

This is not yet a live-launch decision. The repository may be cloned and inspected, and command-candidate evidence may be generated, but `/api/scanner/start` live OP25 launch remains disabled until this document records the exact validated Pi command template.

Evidence commands:

```bash
./tools/pi5_p25_op25_source_install.sh --dry-run
./tools/pi5_p25_op25_source_install.sh --clone-only --yes
./tools/pi5_p25_op25_command_candidate.sh
```
## Post-install validation state

V0.1J adds post-install command evidence collection. This still does not enable backend live OP25 launch. The project must record the exact validated command template and manual control-channel test evidence before `/api/scanner/start` is allowed to start OP25.

Post-install probe:

```bash
./tools/pi5_p25_op25_postinstall_probe.sh
```

## V0.1K command-validation evidence

`tools/pi5_p25_op25_live_command_probe.sh` records the dry-run and optional bounded `rx.py` smoke command. A `FINAL: PASS` smoke report is required before the backend command template is considered validated. This milestone still does not enable live backend launch by default.
## V0.2A backend launch decision

The supported backend live-launch path is the validated marker produced by `tools/pi5_p25_op25_live_command_probe.sh --rx-smoke --seconds 20 --yes`. The backend must consume that marker instead of guessing command arguments.

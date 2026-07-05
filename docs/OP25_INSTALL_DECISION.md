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

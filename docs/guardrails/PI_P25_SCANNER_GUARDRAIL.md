# PI P25 Scanner Guardrail Index

This file indexes the active project guardrails. The source of truth is `DEV_GUARDRAILS.md` in the repository root.

## Active baseline guardrails

- MSYS2 UCRT64 is the supported staging environment.
- Raspberry Pi 5 / Debian Trixie full is the target runtime.
- Prefer single `.sh` script handoffs.
- Use explicit PASS/FAIL validation.
- Keep validators from aborting accidentally under `set -e`.
- Commit Pi-runnable scripts with executable mode `100755`.
- Track P25 receiver roles by stable RTL EEPROM serials after live enumeration.
- Use SDRTrunk as a reference only unless license compatibility is explicitly documented.
- Do not attempt to decrypt encrypted P25 traffic.
- Keep the web UI minimal until the decoder path is proven.

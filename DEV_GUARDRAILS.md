# PI P25 Scanner Development Guardrails

This is the living guardrail file for `bakstaaj/PI-P25-SCANNER`. Keep it updated whenever workflow corrections, script fixes, deployment rules, runtime discoveries, hardware mapping evidence, or decoder/legal constraints change.

## Supported Development Environment

- Use Windows MSYS2 UCRT64 as the supported development environment for repo staging and script handoff.
- Keep commands and scripts compatible with MSYS2 UCRT64 paths such as `~/sdrdev` and `/c/Users/jim/Downloads`.
- Target repository: `bakstaaj/PI-P25-SCANNER`.
- Target runtime: Raspberry Pi 5 running Raspberry Pi OS / Debian Trixie full.
- Do not assume an Ubuntu development server for this project unless explicitly requested.
- Do not pivot to a native Windows application, PowerShell/cmd packaging, Windows services, Visual Studio, or installers.

## Script Handoff Rules

- Prefer a single directly downloadable `.sh` script for non-trivial command sequences.
- Do not provide zip bundles as the primary handoff unless there is a strong reason.
- Scripts should be runnable from the repository root unless clearly stated otherwise.
- Staging scripts that create the repository may run from any MSYS2 directory, but must clearly state that in the header and response instructions.
- Scripts should validate that they are being run from the expected repo root before making repo-local changes when practical.
- Scripts should be idempotent where practical.
- Scripts should create backups before modifying existing project files when overwriting user-edited files is possible.
- Scripts should print clear PASS/FAIL status messages.
- Every downloadable script handoff must include exact MSYS2 run instructions.

## Validation Style

- Use actual PASS/FAIL checks, not visual inspection.
- Recommended validations:
  - `bash -n` for shell scripts.
  - `python3 -m py_compile` for Python files.
  - `python3 -m json.tool <file>` for JSON files one file at a time.
  - `node --check web/app.js` when Node is installed.
  - `git diff --check` for whitespace validation.
- Do not rely on `grep`/`diff` output that the operator must visually inspect as the only validation.
- Scripts that create local report or backup folders must ignore those folders before repo cleanliness checks.

## No Blocking Output In Scripts

- Do not put `git diff`, `git log`, `git show`, or similar commands in scripts unless their output is used for a PASS/FAIL test.
- Do not invoke commands that may open a pager and stop the script at a `:` prompt.
- If repository state needs to be summarized, use non-paging checks such as `git status --short --branch`.
- If a Git command could page, force no pager with `GIT_PAGER=cat` or `--no-pager`.

## `set -e` / Diagnostic Script Guardrails

- `set -Eeuo pipefail` is allowed, but optional probes must not abort the script accidentally.
- Warning helpers must return success.
- Optional checks must use explicit `if command; then pass ...; else warn ...; fi` blocks.
- Required checks should use explicit PASS/FAIL accounting and still reach a final summary when practical.
- Only true precondition failures should exit immediately.
- Commands expected to return nonzero during probing, such as `curl`, `grep`, `ssh`, or HTTP checks, must be wrapped with `if`, `case`, or `|| true`.
- For pipelines under `pipefail`, handle expected no-match cases explicitly.

## Patch Commit and Push Guardrails

- Patch scripts may push only after all local validations and commit creation pass.
- Never push when validation, whitespace checks, executable-mode checks, or commit creation fail.
- Patch scripts must stage explicit intended paths only.
- Patch scripts must warn about unrelated untracked local artifacts and leave them untouched.
- If a patch intentionally leaves changes uncommitted or unpushed, it must say so explicitly in the final output.

## Executable Script Tracking Guardrails

- Runnable Pi/Linux shell scripts under `tools/` must be committed with Git mode `100755`.
- On Windows/MSYS2, do not rely on `chmod +x` alone; use `git update-index --chmod=+x <path>` for tracked executable scripts.
- Validate executable mode with `git ls-files -s -- <path>` and treat any mode other than `100755` as a FAIL before commit.
- A Raspberry Pi `Permission denied` result can be worked around immediately with `bash ./tools/name.sh`, but the repo must still be fixed.

## P25 Scanner Scope Guardrails

- The project is a minimal P25 trunk-following scanner, not a full SDRTrunk clone.
- Use P25 Phase I / Phase II terminology, not Type I / Type II for the user-facing scanner mode.
- Accept control-channel frequencies and talkgroup whitelist entries from local config/UI.
- Follow and play only allowed clear talkgroups.
- Detect encrypted calls when decoder metadata supports it, mute audio, and show encrypted/skipped status.
- Do not attempt encryption bypass, key recovery, key loading, or decryption of protected traffic.
- Keep the first UI minimal: config, start/stop, control-channel status, active voice frequency, TGID, phase, encrypted/clear, signal/decoder health, and log tail.

## SDRTrunk Reference Guardrail

- SDRTrunk may be studied as a protocol and scanner-behavior reference.
- Do not copy SDRTrunk source code into this repository until a license compatibility decision is explicitly documented.
- If SDRTrunk code is copied or adapted, the repository license and notices must be updated before or alongside the patch.
- Prefer an external decoder wrapper for the first Pi milestone.

## Decoder Engine Guardrails

- V0.1 decoder target is OP25 or another Pi-native command-line decoder controlled by the Python backend.
- Keep decoder engine process ownership explicit and visible in `/api/status`.
- Do not treat stale generated files as proof that the decoder process is alive.
- Validators must separate backend/API failures from RF-environment warnings.
- Phase II support depends on the installed decoder path and must be reported honestly in status/preflight.

## RTL-SDR Hardware Guardrails

- Use stable RTL EEPROM serials for persistent receiver roles after live enumeration confirms them.
- Do not hard-code runtime RTL device indexes in backend code.
- Prefer two-SDR architecture when available: one control-channel receiver and one voice-follow receiver.
- One-SDR mode is acceptable for early testing but may miss grants while retuning.
- Hardware mapping commits must include the evidence source and validator path.
- P25 receiver role naming should be `p25_control` and `p25_voice`.

## Pi Runtime Dependency Guardrails

- A no-hardware preflight with missing `rtl_*`, SoX, or decoder tools is not automatically a backend failure until the Pi dependency bootstrap has been run.
- Runtime dependency validation must check commands directly, not rely on visual inspection of apt output.
- Missing optional decoder tools should be warnings in discovery scripts and hard failures only in acceptance scripts that require live decode.

## API and UI Contract Guardrails

- `/api/status` must expose scanner state, decoder engine state, receiver role mapping, active control frequency, active voice frequency, active TGID, phase, encryption status, and recent log/status messages.
- Automatic UI refresh may poll status but must not issue scanner stop/start POSTs unless tied to a recent operator action.
- Service/API validators must wait for a stable, parseable `/api/status` before counting endpoint failures after startup.

## Acceptance Bundle Guardrail

- When a milestone has several validators, maintain a single acceptance bundle that emits one top-level `FINAL: PASS` or `FINAL: FAIL` summary.
- Required runtime paths must remain hard failures; RF conditions with no live traffic may be WARN when the scanner is otherwise healthy.

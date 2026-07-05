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
- Live OP25 process launch must not use a guessed command. It is enabled only after the Pi-specific command template is validated and documented.
- Generated OP25 runtime files belong under ignored `runtime/op25/`; source JSON stays in `config/`.

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
- `/api/status` must expose generated OP25 config state and decoder process state separately.
- Automatic UI refresh may poll status but must not issue scanner stop/start POSTs unless tied to a recent operator action.
- Service/API validators must wait for a stable, parseable `/api/status` before counting endpoint failures after startup.

## ChatGPT/Sandbox Reliability Guardrail

- When the assistant sandbox is slow or timing out, prefer small bounded scripts that run in the user's MSYS2/Pi environment instead of long sandbox operations.
- Keep generated patch scripts single-file, repo-root runnable, and self-validating.
- Avoid promising background work or delayed results; deliver the next executable handoff in the current response.

## Acceptance Bundle Guardrail

- When a milestone has several validators, maintain a single acceptance bundle that emits one top-level `FINAL: PASS` or `FINAL: FAIL` summary.
- Required runtime paths must remain hard failures; RF conditions with no live traffic may be WARN when the scanner is otherwise healthy.

## Patch Recovery and Staging Order Guardrails

- Patch scripts that create new runnable scripts must `git add <path>` before `git update-index --chmod=+x <path>`; `git update-index` must never be used as the first index operation for a new file.
- Executable-bit requests must be wrapped in explicit PASS/FAIL checks. A failed `git update-index` must not be followed by a misleading PASS line.
- Patch scripts must normalize touched text files before staging and then validate both unstaged and staged whitespace with `git diff --check` and `git diff --cached --check`.
- Recovery scripts may operate on a partially applied patch, but they must stage only explicit intended paths, preserve unrelated local artifacts, and commit/push only after all validation passes.

## OP25 Install Decision Guardrail

- OP25 install/build work must start with non-invasive capability evidence from `tools/pi5_p25_op25_install_probe.sh`.
- Do not install, clone, build, or enable OP25 from a probe script.
- Do not enable backend live OP25 launch until `docs/OP25_INSTALL_DECISION.md` records the validated executable path, command template, RTL-SDR selection method, and phase support.
- Missing OP25 remains a warning during discovery/probe milestones and becomes a hard failure only in a milestone that explicitly requires live decoding.
- Runtime OP25 files remain under ignored `runtime/op25/`; source config remains under `config/`.

## Local Config Guardrails

- Checked-in P25 JSON files under `config/` are templates; user/site runtime settings belong under ignored `runtime/settings/p25_systems.json`.
- Backend config resolution must prefer explicit `P25_SCANNER_CONFIG`, then ignored runtime config, then checked-in example config.
- Patch scripts that add new runnable tools must `git add` the file before `git update-index --chmod=+x`; do not set executable mode on an untracked path.
- Patch scripts must normalize touched text files before staging and must run staged whitespace validation with `git diff --cached --check` before commit.
- Config validators must check JSON syntax and project schema/model requirements, not only that the file exists.

## No-Pager Git Command Guardrails

- Patch and validation scripts must not run raw `git diff`, `git log`, or `git show` commands that can open a pager and stop at a `:` prompt.
- Any Git inspection command that could page must use `git --no-pager ...` or set `GIT_PAGER=cat` in that command's environment.
- `git diff` is allowed only when used as a PASS/FAIL validation such as `git --no-pager diff --check`, `git --no-pager diff --quiet`, or `git --no-pager diff --name-only` captured into a variable for explicit validation.
- Patch scripts must include a no-pager/static script validation step before commit when they add or modify runnable shell scripts.
- If a script hangs at a pager prompt, stop using that script and replace it with a no-pager recovery script instead of asking the operator to visually inspect diff output.

## No-Pager Validator Recovery Guardrail

- No-blocking Git validators must not flag their own comments, examples, or regex/pattern text as executable Git commands.
- When validating pager-prone commands, scan only executable-looking shell lines such as command-position `git diff`, `git log`, or `git show`.
- Allow `git --no-pager diff --check` and `git --no-pager diff --name-only --exit-code` only when used as PASS/FAIL validation.
- Patch scripts must stage new executable scripts before running `git update-index --chmod=+x`; do not set executable mode on paths Git has not added yet.
- Patch and recovery scripts must normalize touched text files to LF before both working-tree and staged whitespace checks.


## Staged Index / CRLF Recovery Guardrail

- If `git diff --cached --check` reports nearly every added line as trailing whitespace, suspect stale staged CRLF content rather than bad source logic.
- Recovery scripts must unstage intended paths first, normalize the working-tree copies to LF/no trailing spaces, then restage the normalized files before running staged whitespace validation.
- New executable scripts must be `git add` staged before `git update-index --chmod=+x`; do not set executable mode on untracked paths.
- Staged whitespace validation should write detailed output to a report file and print a concise PASS/FAIL summary so the terminal does not flood or hang.
- No-blocking Git validators must not flag pager-safe PASS/FAIL checks such as `git --no-pager diff --check` or `git --no-pager diff --cached --check`.

## Config UI/API Guardrails

- Web UI config edits must save only to the ignored runtime config path `runtime/settings/p25_systems.json`; checked-in templates under `config/` are not edited by the running app.
- Config save APIs must validate the full project config model before writing runtime config files.
- Existing runtime config files must be backed up under ignored runtime backup paths before overwrite.
- Backend status must expose active config metadata separately from decoder process state.
- Config/API validators that start the backend must bind only to loopback on a high test port, use bounded readiness waits, capture logs to ignored report folders, and always clean up the backend process.
- Patch scripts that normalize line endings must unstage intended files first, normalize the working tree, then restage so the Git index cannot retain stale CRLF content.


## RTL Role Mapping Patch Guardrails

- Receiver ownership must be serial-first. Runtime RTL indexes are evidence only and must not be persisted as the source of truth.
- Role mapping tools may write detected evidence under ignored `runtime/settings/` and report folders, but must not commit live hardware evidence unless explicitly requested.
- Local role update tools must modify only the ignored runtime config by default and must back up that file before overwrite.
- Patch scripts must unstage intended paths before normalization, normalize LF/no trailing whitespace, restage after normalization, then run staged whitespace validation to avoid stale CRLF index failures.

## Repository LF Line-Ending Policy Guardrails

- This repository must track `.gitattributes` to force LF line endings for text, scripts, source, docs, config, and web files.
- Patch and recovery scripts must set repo-local `core.autocrlf=false` and `core.eol=lf` before writing or staging generated text.
- When staged whitespace validation reports nearly every added line as trailing whitespace, treat it as a CRLF-in-index failure and repair the index with LF policy plus `git add --renormalize .`.
- After adding or changing `.gitattributes`, run `git add --renormalize .` before staged whitespace validation.
- Patch scripts should include a single reusable LF normalization step instead of ad hoc per-file CRLF repairs.
- Staged whitespace details should go to a report file; terminal output should remain concise PASS/FAIL.


## Pi Bring-Up Acceptance Guardrails

- Pi bring-up acceptance bundles must be non-invasive unless the milestone explicitly says otherwise: no package install, no source clone/build, no live decoder launch, and no transmit behavior.
- Acceptance bundles should orchestrate existing validators/probes and store full step logs under ignored report folders while printing only concise PASS/WARN/FAIL summaries.
- Optional decoder tooling gaps and quiet RF conditions remain WARN until the project reaches a milestone that explicitly requires live P25 control-channel lock or voice decode.
- Bring-up acceptance must keep backend/API, config/schema, RTL hardware evidence, and decoder capability evidence as separate result categories.
- Patch scripts must preserve the repository LF policy before staging: set repo-local `core.autocrlf=false`, `core.eol=lf`, normalize touched text, unstage stale entries, restage, then run staged whitespace validation.

## Config API Smoke Test Guardrails

- Config API smoke validators must preserve and restore the operator's ignored runtime config when exercising save/init endpoints.
- Config API smoke validators should seed a known-good temporary runtime config before backend startup so Pi bring-up failures are not caused by stale local operator state.
- Loopback API smoke validators should select a dynamic high port by default, while still honoring an explicit test-port environment override.
- API smoke validators must capture both backend logs and client/request logs, and failure messages must point to both logs.
- Pi acceptance bundles should treat a runtime probe failure caused by repo validation as a repo/API validation failure first; inspect the step log before changing RF/decoder assumptions.


## OP25 Source Install Guardrails

- OP25 source acquisition must be dry-run-first on the Pi.
- Clone-only source acquisition is allowed with explicit `--clone-only --yes`; it must not install packages, build OP25, or launch a decoder.
- Full upstream OP25 install/build must require explicit `--run-upstream-install --yes` and must capture a report.
- OP25 source and build artifacts belong outside tracked project files, normally `~/op25`, with only ignored runtime evidence written under `runtime/settings/`.
- Command-candidate evidence is not sufficient to enable backend live OP25 launch; `docs/OP25_INSTALL_DECISION.md` must record the validated exact command template first.
- The PI-P25-SCANNER scope remains clear audio only; encrypted traffic is mute/log only.
## OP25 Post-Install Command Validation Guardrails

- Post-install OP25 probes may run `rx.py --help` and `multi_rx.py --help` with short timeouts, but must not tune SDR hardware or start persistent decode processes.
- Any generated command evidence under `runtime/settings/` is local runtime evidence and must not by itself enable backend live launch.
- Backend live OP25 launch requires a separately documented, bounded Pi control-channel validation command and an explicit command template update.
- OP25 install/build completion is not equivalent to scanner acceptance; command-line behavior and runtime imports must be validated after reboot when practical.

## OP25 Live Command Validation Guardrails

- Live OP25 command validation must be bounded with `timeout` and write logs to ignored report folders.
- Dry-run command generation must be the default; any RF/decoder smoke run requires an explicit mode and `--yes`.
- A timeout exit from a bounded smoke run is acceptable evidence that OP25 started and stayed alive for the validation window, as long as the log has no immediate import/source/config errors.
- Backend `/api/scanner/start` live launch remains disabled until a later patch records and wires the exact validated command template.
- OP25 validation tools must keep encrypted traffic behavior set to skip or mute; no key files or decryption workflows are in scope.


## Staged Whitespace Recovery Guardrails

- If working-tree whitespace passes but staged whitespace fails, treat the Git index as stale until proven otherwise.
- Recovery must unstage the intended paths, normalize the working-tree files to LF/no trailing whitespace, then restage the normalized files.
- Patch scripts must not continue to repo validation after a staged whitespace failure without first refreshing the index.
- `tools/normalize_text_policy.sh --check` must be a real check-only mode before patch scripts rely on it; otherwise patch scripts should use explicit `git --no-pager diff --check` and `git --no-pager diff --cached --check` PASS/FAIL checks.

## Force LF Index Recovery Guardrail

- If normal unstage/normalize/restage still leaves `git diff --cached --check` failing while working-tree whitespace passes, rebuild staged blobs directly from normalized LF worktree content.
- The direct-index recovery pattern is: `git reset` to clear stale staged blobs, normalize each intended file to LF/no trailing spaces, `git hash-object -w` the normalized file, and `git update-index --add --cacheinfo` with the intended file mode.
- Do not use `git add --renormalize .` as the only recovery step when a stale index has already survived normal restaging.
- Recovery scripts may unstage all paths with `git reset -q`; this must not delete working-tree changes and must restage only explicit intended patch paths.

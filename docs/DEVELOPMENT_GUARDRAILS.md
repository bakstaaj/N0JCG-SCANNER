# PI-P25-SCANNER Development Guardrails

These guardrails capture project-specific lessons learned while building and validating the Raspberry Pi P25 scanner workflow.

## Shell and patch generation

- Prefer single downloadable `.sh` scripts for MSYS2/Docker/Pi steps instead of long pasted command blocks.
- Patch scripts must be repo-root runnable and idempotent when practical.
- Back up files before overwrite or broad replacement.
- Treat script-generation failures as hard failures. Never allow a patch script to print `FINAL: PASS` after a failed heredoc, Python generator, syntax check, or validation step.
- Avoid embedded Python triple-quoted strings for generating shell scripts, JSONL fixtures, heredocs, or content containing quote-heavy text. Use quoted shell heredocs or complete file replacement templates instead.
- Validate JSONL analysis with a real temporary JSONL fixture before commit whenever capture/analyzer behavior changes.

## Line endings and whitespace

- Keep `core.autocrlf=false`, `core.eol=lf`, and LF-only text files.
- Normalize touched text files before staging.
- Strip trailing whitespace and extra blank lines at EOF before commit.
- Run both working-tree and staged whitespace checks.

## Git workflow

- Stage every new script explicitly before applying executable mode.
- Validate executable scripts are staged as `100755` when intended.
- Use no-pager Git commands in scripts and validators.
- Commit and push only after syntax, whitespace, repo, and feature-specific probes pass.

## Runtime safety

- Long live probes must perform a short fail-fast readiness preflight before long evidence collection; skip long collection on startup/readiness failure unless an explicit force flag is used.

- Live OP25 launch must stay gated by `runtime/settings/op25_validated_rx_command.env`.
- Backend service startup must not automatically start RF decoding.
- Encrypted calls are metadata only: detect, show, mute/skip, and count. Do not attempt decryption, key recovery, bypass, key loading, or protected audio reconstruction.

## Whitespace and generated-script validation

- Generated patch and recovery scripts must normalize tracked text files to LF endings before staging.
- Generated patch and recovery scripts must remove trailing spaces/tabs and collapse EOF whitespace to exactly one final newline.
- Treat `new blank line at EOF`, trailing whitespace, CRLF, heredoc/generator errors, syntax errors, and failed fixture tests as hard failures.
- Run `git --no-pager diff --check` before staging and `git --no-pager diff --cached --check` after staging; both must pass before commit.
- Never print `FINAL: PASS` after any failed heredoc, generator, syntax check, whitespace check, fixture test, commit, or push step.
- Before generating future scripts, include a pre-commit whitespace normalizer/checker in the script design rather than relying on manual cleanup afterward.
- Avoid embedded Python triple-quoted strings for generating shell scripts, JSONL fixtures, heredocs, or quote-heavy content.
- Validate JSONL analysis with a real temporary JSONL fixture before commit whenever capture/analyzer behavior changes.
- Patch scripts must verify the working tree is clean before changing files; if dirty, preserve diffs/backups before any reset or overwrite.

## OP25 metadata discovery guardrails

- Interface/metadata discovery patches must avoid guessing OP25 parser behavior when live evidence shows no active TGID or voice-frequency lines.
- Before adding a new active-call parser, first capture source/interface evidence or a real OP25 log sample containing the field being parsed.
- Discovery probes should warn, not fail, when an optional OP25 HTTP/terminal/status interface is not present.
## OP25 interface discovery guardrail

- Interface/status discovery tools must save the initial `/api/status` snapshot before starting or stopping the scanner.
- A temporary loss of post-start status polling is WARN-level diagnostic evidence when the backend was initially reachable; do not fail the interface-discovery milestone solely from that condition.
- Live decoder metadata discovery must not guess active TGID or voice-frequency parsers from whitelist/config lines; use captured evidence or explicit OP25 status/interface data.

## Live interface probe guardrail

- Long-running interface discovery probes must derive endpoint ports from the actual decoder command before falling back to static defaults.
- When OP25 is launched with `-l http:host:port`, that port must appear in the probe report before concluding that no OP25 HTTP/status interface is active.

- Long-running interface probes must extract runtime ports and command metadata from all available sources, including initial status, start response, sampled status, and validated marker files, before deciding what to probe.
- Fail-fast reports must include enough start/readiness diagnostics to explain why long collection was skipped.
- Live fail-fast probes must count successful start API responses as status evidence before skipping long collection.

## Runtime HTTP interface diagnostics

- Short runtime diagnostics must distinguish backend start-response success from sustained backend pollability.
- If OP25 is launched with an HTTP terminal/listener argument such as `-l http:127.0.0.1:18091`, probes must verify both the command argument and the actual TCP listener.
- Do not assume an OP25 HTTP endpoint exists just because a command line contains an HTTP listener argument.

## Upload-ready command output logs

- Future patch, recovery, and live probe scripts must write a complete upload-ready stdout/stderr transcript to a local timestamped text file.
- The transcript path must be printed near the beginning of the script and again in the final summary.
- Do not rely on terminal scrollback as the only evidence. Reports and summaries are useful, but a raw command transcript is required for troubleshooting failed runs.
- Prefer task-specific report directories such as `.p25_<task>_reports/`. For ad-hoc Pi validation commands, use `tools/pi5_p25_run_with_log.sh` or an equivalent built-in tee capture.
- When a command can run for more than a few seconds, preserve both the structured report files and the raw transcript path so the user can upload one local file instead of pasting a long console dump.

## Upload-ready command transcripts

- Long-running patch, recovery, validation, and live probe commands must preserve stdout/stderr in a local upload-ready transcript file instead of relying only on terminal scrollback.
- Transcript helpers must print the absolute log directory and absolute log file path, verify that the file exists, and include the command exit status.
- When a command runs on the Raspberry Pi, the handoff must include a supported MSYS2 pull path or helper so the Pi-side transcript can be copied into a Windows-local upload folder.
- Relative report paths may be printed for convenience, but they must not be the only way to locate an upload artifact.

## HTTP JSON probe handling

- Probe helpers that parse backend JSON must read the full HTTP response before calling `json.loads`; only report samples and terminal display snippets may be truncated.
- Long or repetitive HTTP probe result arrays should be summarized in terminal Markdown, with complete details preserved in the generated JSON artifact.
- Pi-side command logs must include an absolute Pi path, and MSYS2-side instructions must provide a one-step pull command or helper that places the log in Windows Downloads for upload.

## GUARDRAIL: MSYS2 Pi log pulls must use sshpass and Jim/Pi paths

For Pi-hosted probes, do not ask the user to manually copy files from the Pi. The Pi user is `pi`, the Windows/MSYS2 user is `jim`, the Pi repo path is `/home/pi/PI-P25-SCANNER`, and local upload logs must be copied to `/c/Users/jim/Downloads/pi-p25-command-logs` before asking the user to upload them. MSYS2-side helper commands must use `sshpass` plus `scp -O`/`ssh`, not generic `ssh`/`scp` instructions that assume agent auth. Prefer `tools/msys2_run_pi_http_runtime_probe_and_pull_log.sh` or `tools/msys2_pull_latest_p25_log.sh` so the final output prints `UPLOAD_FILE_MSYS` and `UPLOAD_FILE_WINDOWS`.

## Backend readiness after service restart

Probe scripts that restart or immediately follow a restart of `pi-p25-scanner.service` must wait for `/api/status` to become reachable before making scanner start/no-start decisions. A transient connection-refused response during backend startup must not be treated as "already running," "no-start," or a final runtime state.

For Pi probes run from Windows/MSYS2, instructions must use the repo helper that runs the Pi command and pulls the latest upload-ready log back to `/c/Users/jim/Downloads/pi-p25-command-logs` using `sshpass` and `scp -O`.

## MSYS2-to-Pi credential and log-transfer guardrail

MSYS2 helper scripts that connect to the Pi must use Jim's standard defaults unless explicitly overridden:

- Windows/MSYS2 user path: `/c/Users/jim/Downloads/pi-p25-command-logs`
- Pi user: `pi`
- Pi repo path: `/home/pi/PI-P25-SCANNER`
- Copy method: `sshpass` plus `scp -O`

Helpers must source `tools/msys2_env_common.sh`, load local `.env` values, and create `.env` with mode `600` when prompting for `PI_PASSWORD`. The `.env` file must remain ignored by git, must never be staged, and scripts must never print the password value. Tracked examples belong in `.env.example` only.


## Source patching guardrail: no loose partial text markers

Generated patch and recovery scripts must not modify source files by searching loose snippets and expecting a single match. Do not use fragile paths such as "expected one marker" checks, count-based source substitutions, or broad string matches against Python, JavaScript, HTML, CSS, or shell source.

Allowed source-update approaches:

- full-file rewrites for small owned files such as web UI files, helper scripts, and docs;
- structured parsers or format-aware tools when changing Python, JSON, HTML, or JavaScript;
- exact whole-function or whole-class replacement only when the full boundary can be identified safely;
- fail before touching files when a change cannot be applied deterministically.

Documentation files may receive an append-only note, but source code must not be patched through loose partial-text marker matching.

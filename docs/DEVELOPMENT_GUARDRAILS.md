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

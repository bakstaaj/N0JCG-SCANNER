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

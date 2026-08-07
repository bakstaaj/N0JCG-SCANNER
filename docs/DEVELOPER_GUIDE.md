# N0JCG Scanner developer guide

| Metadata | Value |
|---|---|
| Product | N0JCG Scanner |
| Slug | scanner-developer-guide |
| Type | Developer guide |
| Version | 3.0.0 |
| Status | Preview |
| Last updated | 2026-08-07 |
| Audience | Contributors and maintainers |
| Prerequisites | Python, JavaScript, Git, and basic SDR concepts |
| Estimated time | 15 minutes |
| Related | [Architecture](ARCHITECTURE.md), [Contributing](../CONTRIBUTING.md) |
| Owner | N0JCG |

## Local environment

Windows development uses MSYS2 UCRT64 with LF line endings. Install dependencies
in a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements-dev.txt
```

## Code boundaries

- `src/pi_p25_scanner/` contains the radio API, OP25 integration, audio
  arbitration, configuration, and VHF/UHF workers.
- `web/` contains application-host assets. It communicates through relative API
  and audio paths so deployments do not hardcode station addresses.
- `deploy/`, `systemd/`, and role-specific tools define deployment ownership.
- `runtime/` is local state and is never committed.

Preserve serial-first receiver ownership. Keep browser clients independent of
scanner service lifecycle: attaching a listener must not restart the radios.

## Validation

```bash
PYTHONPATH=src python3 -m pytest -q
node --check web/app.js
node --check web/mobile.js
./tools/validate_repo.sh
git diff --check
```

Test UI changes at desktop size and at an approximately 800x420 short viewport.
Radio/DSP changes require synthetic tests first and live hardware evidence only
when the operator authorizes a test.

## Documentation and release workflow

Update maintained guides and `CHANGELOG.md` in the same pull request as the
behavior change. If the user manual changes, rebuild and visually verify the
DOCX/PDF editions with `tools/build_branded_user_manual.py`.

Release candidates must pass the full test suite, repository validator, shell
syntax checks, JavaScript syntax checks, and whitespace checks. Tag only a
commit already merged to `main`; publish checksums for downloadable artifacts.

## Recovery

If a change fails validation, revert only the scoped change or redeploy the last
known-good tag. Preserve unrelated working-tree changes and local runtime data.
Escalate RF uncertainty with captured, redacted evidence instead of guessing at
threshold or gain changes.

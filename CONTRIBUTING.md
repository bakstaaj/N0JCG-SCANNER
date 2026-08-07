# Contributing to N0JCG Scanner

Thank you for helping improve N0JCG Scanner. Changes should preserve the
receive-only safety boundary, stable RTL-SDR serial ownership, split-host
deployment model, and existing operator workflows.

## Before you begin

1. Search existing issues and open one for significant behavior changes.
2. Read `DEV_GUARDRAILS.md` and the relevant architecture or hardware guide.
3. Work on a focused branch and keep unrelated local changes out of the commit.
4. Do not commit credentials, private network addresses, station identifiers,
   receiver serial maps, recordings, or runtime logs.

## Development checks

Use MSYS2 UCRT64 on Windows and preserve LF line endings.

```bash
python3 -m pip install -r requirements-dev.txt
PYTHONPATH=src python3 -m pytest -q
node --check web/app.js
node --check web/mobile.js
./tools/validate_repo.sh
```

Changes to generated public documents must commit the Markdown source and the
generated DOCX/PDF editions together. Update `CHANGELOG.md` when behavior,
configuration, deployment, or public documentation changes.

## Pull requests

Describe the operator-visible outcome, affected deployment role, validation
performed, rollback path, and any hardware evidence. Keep pull requests small
enough to review safely. Never claim live RF or hardware validation unless it
was actually performed and the evidence is reproducible.

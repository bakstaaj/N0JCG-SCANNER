## Outcome

Describe the operator-visible or maintenance outcome.

## Scope

- Affected deployment role(s):
- Configuration or hardware impact:
- Documentation updated:

## Validation

- [ ] `PYTHONPATH=src python3 -m pytest -q`
- [ ] `node --check web/app.js` and `web/mobile.js`
- [ ] `./tools/validate_repo.sh`
- [ ] No credentials, private addresses, station identifiers, receiver serial
      maps, runtime logs, or recordings are included
- [ ] Live-hardware claims include reproducible evidence, or are marked untested

## Recovery

Describe the rollback or recovery path.

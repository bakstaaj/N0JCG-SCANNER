# Security policy

## Supported versions

Security fixes are applied to the current preview release and the `main`
branch. Older development snapshots are retained for historical reference and
are not supported.

## Reporting a vulnerability

Do not publish credentials, private station details, recordings, or an
exploitable vulnerability in a public issue. Use GitHub's private vulnerability
reporting for this repository when available. If private reporting is not
available, open a minimal issue requesting a private contact channel without
including sensitive details.

For ordinary defects that contain no sensitive information, use
[GitHub Issues](https://github.com/bakstaaj/N0JCG-SCANNER/issues).

## Security boundaries

- N0JCG Scanner is receive-only and does not support encrypted-audio recovery.
- Deployment credentials belong only in the ignored `.env` file or an
  equivalent secret store.
- Runtime profiles, receiver serial assignments, private addresses, and logs
  belong under ignored local storage, not in commits.
- The browser application should reach radio services through the configured
  application-host proxy; public clients should not receive radio-host secrets.

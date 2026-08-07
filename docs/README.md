# N0JCG Scanner documentation

| Metadata | Value |
|---|---|
| Product | N0JCG Scanner |
| Slug | scanner-documentation-index |
| Type | Documentation index |
| Version | 3.0.0 |
| Status | Preview |
| Last updated | 2026-08-07 |
| Audience | Operators, administrators, developers, integrators |
| Prerequisites | None |
| Estimated time | 2 minutes |
| Related | [Product page](https://www.n0jcg.com/products/scanner/) |
| Owner | N0JCG |

Use the smallest guide that covers the work:

- [User Guide](USER_MANUAL.md): daily operation, profiles, imports, and recovery
- [Administrator Guide](ADMINISTRATOR_GUIDE.md): installation, configuration,
  deployment, monitoring, backup, and rollback
- [Developer Guide](DEVELOPER_GUIDE.md): local setup, code layout, tests, and
  release workflow
- [API Reference](API_REFERENCE.md): HTTP and streaming interfaces
- [Hardware Guide](HARDWARE_GUIDE.md): receiver selection, serial ownership,
  antennas, USB power, and safe commissioning
- [Architecture Guide](ARCHITECTURE.md): system boundaries and data flow
- [Release archive](releases/): detailed version notes
- [Changelog](../CHANGELOG.md): concise version history

Engineering milestone notes remain in this directory for traceability. They are
not substitutes for the maintained guides above.

Public documentation intentionally uses placeholders for station-specific host
names, addresses, credentials, and RTL-SDR serials. Store those values in the
ignored `.env` and `runtime/` paths.

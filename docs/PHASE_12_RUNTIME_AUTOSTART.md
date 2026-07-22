# Phase 12 Runtime Monitoring Autostart

Marker: `PHASE12_RUNTIME_AUTOSTART_V0_6J`

The combined scanner now resumes monitoring after boot without relying on
browser JavaScript.

The enabled `pi-scanner-runtime-autostart.service` waits for the backend and
browser-audio bridge, then applies `runtime/settings/startup_policy.json`.

Default policy:

- P25 autostart enabled
- Analog 2 m autostart enabled
- Analog 70 cm autostart enabled

The orchestrator starts components through the existing local APIs and writes
`runtime/status/startup_orchestrator.json`.

The analog worker units remain individually disabled. This keeps boot behavior
under one policy owner while preserving all existing manual Start and Stop
controls.

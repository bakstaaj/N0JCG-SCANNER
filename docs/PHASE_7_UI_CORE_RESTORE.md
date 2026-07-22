# Phase 7 UI Core Restore

Marker: `PHASE7_UI_CORE_RESTORE_V0_6F2`

The analog feature phases retained calls to the original P25 dashboard helpers,
but the helper block itself was absent from `web/app.js`.

This hotfix restores:

- DOM helpers (`field`, `setText`, `setBadge`)
- API helpers (`fetchJson`, `postJson`)
- Dashboard rendering and `/api/status` polling
- Navigation helpers and category defaults
- Receiver inventory rendering and polling

It also removes the obsolete `V0_5K_AUTO_START_RTL_POOL` page-load auto-start
block. Normal browser loads now observe scanner state without attempting to
start the scanner. The desktop launcher or the trusted Start Scanner + Audio
button remains responsible for intentional scanner starts.

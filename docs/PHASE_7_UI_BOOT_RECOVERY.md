# Phase 7 UI Boot Recovery

Marker: `PHASE7_UI_BOOT_RECOVERY_V0_6F1`

This hotfix prevents a supplemental API or UI panel failure from leaving the
dashboard at the static `Loading scanner state...` placeholder.

Changes:

- `/api/status` refresh is launched before supplemental setup requests.
- Every boot task is isolated and reports failures in the visible dashboard.
- Global JavaScript and unhandled-promise errors are shown in the dashboard.
- HTML, JavaScript, and CSS are served with no-cache headers.
- Asset query versions are bumped to force a fresh browser load.

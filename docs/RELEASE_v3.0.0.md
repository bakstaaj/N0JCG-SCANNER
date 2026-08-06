# PI Scanner v3.0.0 — split-host runtime

This major architecture release separates the browser application from the
radio runtime.

- The existing N0JCG ROC at `192.168.68.114:8095` serves PI Scanner at
  `/pi-scanner/` and retains the main ROC dashboard at `/`.
- Radio Pi `192.168.68.137` retains all RTL-SDR, OP25, VHF/UHF, and audio
  functions.
- Browser API and PCM traffic use the existing `/pi-scanner/api/*` and
  `/pi-scanner/audio-api/*` proxy routes.
- Explicit deployment manifests prevent application changes from being copied
  to the radio host and radio changes from being copied to the ROC.
- Role-specific deployment scripts provide dry-run, backup, SHA-256
  verification, and controlled restart behavior.
- The frontend is base-path aware and the same source works through the ROC
  mount or through direct radio-node maintenance access.
- Desktop and mobile clients attach independently to the existing scanner
  session; opening another browser no longer restarts or disables scanning.
- The PCM path uses 20 ms proxy frames with TCP no-delay and a bounded,
  clock-recovered browser ring buffer instead of accumulating short-lived
  playback nodes.
- The radio-side audio arbitrator allows a bounded late-frame recovery window
  and reports packet jitter, recovered late frames, and active-source gaps.
- The ROC header includes a relative back link to the dashboard, with no
  browser-visible host address hardcoded into navigation or API requests.

See `RELEASE_NOTES_v3.0.0.md` in the repository root for deployment,
validation, and compatibility details.

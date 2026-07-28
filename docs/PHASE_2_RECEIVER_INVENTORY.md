# Phase 2 Receiver Inventory Foundation

Marker: `PHASE2_MULTI_RECEIVER_INVENTORY_V0_6A`

This phase adds a persistent, profile-independent RTL role registry and a
read-only hardware inventory API/UI.

## Assigned roles

| Role | Serial | Phase 2 state |
|---|---:|---|
| P25 control | 00000251 | Enabled |
| P25 voice | 00000252 | Enabled/reserved |
| NOAA / airband | 00000162 | Reserved |
| ADS-B 1090 | 00001090 | Reserved |
| UAT 978 | 00000978 | Reserved |
| Analog 2 m | 00000144 | Disabled; Phase 3 target |
| Analog 70 cm | 00000440 | Disabled; Phase 3 target |

## API

`GET /api/receivers/inventory`

The endpoint joins `runtime/settings/receiver_roles.json` to Linux sysfs RTL
devices and visible process command lines. It reports missing, duplicate, active,
reserved, and unassigned receivers.

No analog receiver process is launched in this phase.

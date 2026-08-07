# N0JCG Scanner architecture guide

| Metadata | Value |
|---|---|
| Product | N0JCG Scanner |
| Slug | scanner-architecture-guide |
| Type | Architecture guide |
| Version | 3.0.0 |
| Status | Preview |
| Last updated | 2026-08-07 |
| Audience | Developers, integrators, and administrators |
| Prerequisites | Familiarity with HTTP proxies, SDR receivers, and systemd |
| Estimated time | 15 minutes |
| Related | [Administrator Guide](ADMINISTRATOR_GUIDE.md), [API Reference](API_REFERENCE.md) |
| Owner | N0JCG |

## Production topology

```text
Desktop / phone browsers
          |
          | http://<ROC_HOST>:8095/pi-scanner/
          v
Existing N0JCG ROC application — <ROC_HOST>:8095
  /pi-scanner/                 -> static PI-SCANNER web assets
  /pi-scanner/api/*            -> radio Pi :8070/api/*
  /pi-scanner/audio-api/*      -> radio Pi :8072/*
          v
Radio Pi — <RADIO_HOST>
  backend.py / OP25 / radio API
  P25 audio pool and audio arbitrator
  VHF FFT scanner and UHF FFT scanner
          v
Dedicated RTL-SDR receivers selected by EEPROM serial
```

## Ownership boundary

The existing `N0JCG-ROC` application owns the browser-facing server and
navigation dashboard. This repository supplies only the scanner web bundle
installed under its `web/pi-scanner/` directory. It does not install a second
ROC web service.

The radio Pi owns every hardware or real-time function:

- P25 control and voice receivers;
- VHF and UHF FFT scanners;
- OP25 launch, monitoring, and runtime status parsing;
- analog worker lifecycle;
- audio pooling, arbitration, and fanout;
- runtime radio configuration and EEPROM-serial role mapping.

The radio Pi remains authoritative for scanner state and configuration. The
ROC proxy forwards requests; it does not duplicate radio state.

## Browser contract

The frontend detects whether it is mounted below `/pi-scanner/`:

- local/direct maintenance `/api/*` remains `/api/*`;
- ROC-mounted `/api/*` becomes `/pi-scanner/api/*`;
- local/direct `/radio/*` remains `/radio/*`;
- ROC-mounted `/radio/*` becomes `/pi-scanner/audio-api/*`.

Stylesheets, scripts, phone navigation, and the desktop override use relative
paths so both `/` and `/pi-scanner/` are supported by the same source files.

## Repository/deployment boundary

| Repository area | Owner | Deployment destination |
|---|---|---|
| `web/` | ROC `.114` | `N0JCG-ROC/web/pi-scanner/` |
| `config/`, `src/`, `systemd/`, `tools/` | radio Pi `.137` | `/home/pi/PI-P25-SCANNER/` |
| `docs/`, `tests/`, `deploy/` | development/GitHub | not copied to runtime |

An API contract change must remain compatible with the existing ROC proxy.
Deploy radio-side support first when needed, followed by the ROC web bundle.

## Radio model

P25 uses dedicated control and voice receivers. VHF and UHF use their own
FFT-directed NFM scanners. Persistent ownership is always resolved from RTL
EEPROM serials, never Linux device indexes. Encrypted P25 traffic remains
mute/skip only.

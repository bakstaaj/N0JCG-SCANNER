# N0JCG Scanner architecture guide

| Metadata | Value |
|---|---|
| Product | N0JCG Scanner |
| Slug | scanner-architecture-guide |
| Type | Architecture guide |
| Version | 4.1.0 |
| Status | Preview |
| Last updated | 2026-08-07 |
| Audience | Developers, integrators, and administrators |
| Prerequisites | Familiarity with HTTP proxies, SDR receivers, and systemd |
| Estimated time | 15 minutes |
| Related | [Administrator Guide](ADMINISTRATOR_GUIDE.md), [API Reference](API_REFERENCE.md) |
| Owner | N0JCG |

## Production topology

```text
ROC dashboard — <ROC_HOST>:8095
  Open Scanner -> http://<RADIO_HOST>:8070/
          |
          v
Desktop / phone browsers
          |
          | http://<RADIO_HOST>:8070/
          v
Radio Pi — <RADIO_HOST>
  backend.py serves the complete web application on :8070
  backend.py / OP25 / radio API
  P25 audio pool and audio arbitrator
  VHF FFT scanner and UHF FFT scanner
          v
Dedicated RTL-SDR receivers selected by EEPROM serial

Radio Pi backend -- HTTPS --> www.n0jcg.com licensing service
  product-neutral validation / installation binding / signed lease
```

## Ownership boundary

The `N0JCG-ROC` application owns only the platform dashboard. Its Scanner card
is a direct link to the radio Pi; it does not host, proxy, or duplicate scanner
web assets.

The radio Pi owns every hardware or real-time function:

- P25 control and voice receivers;
- VHF and UHF FFT scanners;
- OP25 launch, monitoring, and runtime status parsing;
- analog worker lifecycle;
- audio pooling, arbitration, and fanout;
- runtime radio configuration and EEPROM-serial role mapping.

The radio Pi is authoritative for scanner state, configuration, browser assets,
and audio. Browser API and audio requests stay same-origin on port `8070` (with
the audio bridge kept internal to the Pi).

The radio backend also owns licensing. It derives a stable, product-neutral
installation S/N, submits activation and refresh requests over HTTPS, and
accepts registration only after verifying the signed lease against its embedded
public key and expected product, installation, and email hash. The ROC/browser
sees display-safe status only. Scanner-specific five-minute trial enforcement
stays in `ScannerManager`; the shared licensing client contains no scanner or
radio-control behavior.

## Browser contract

The frontend is served directly from the Pi root. Relative assets and same-origin
`/api/*` and `/radio/*` requests are the production contract; the former ROC
subpath is no longer a supported scanner runtime.

## Repository/deployment boundary

| Repository area | Owner | Deployment destination |
|---|---|---|
| `web/`, `config/`, `src/`, `systemd/`, `tools/` | radio Pi `.137` | `/home/pi/n0jcg-scanner/` |
| ROC dashboard link | ROC `.114` | direct `http://<RADIO_HOST>:8070/` |
| `docs/`, `tests/`, `deploy/` | development/GitHub | not copied to runtime |

Deploy the complete Pi bundle together. ROC changes are limited to dashboard
link/settings changes and do not carry scanner runtime files.

## Radio model

P25 uses dedicated control and voice receivers. VHF and UHF use their own
FFT-directed NFM scanners. Persistent ownership is always resolved from RTL
EEPROM serials, never Linux device indexes. Encrypted P25 traffic remains
mute/skip only.

# N0JCG Scanner API reference

| Metadata | Value |
|---|---|
| Product | N0JCG Scanner |
| Slug | scanner-api-reference |
| Type | API reference |
| Version | 3.0.0 |
| Status | Preview |
| Last updated | 2026-08-07 |
| Audience | Developers, integrators, and administrators |
| Prerequisites | HTTP, JSON, and PCM audio familiarity |
| Estimated time | 10 minutes |
| Related | [Architecture](ARCHITECTURE.md), [Developer Guide](DEVELOPER_GUIDE.md) |
| Owner | N0JCG |

## Conventions

The browser uses relative routes beneath the deployed scanner base path. JSON
responses use `application/json`; mutating requests use `POST` with a JSON body.
This preview API is intended for the bundled UI and administrative tooling and
is not yet a stability-guaranteed third-party contract.

## Core scanner endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/status` | Combined P25 scanner and service state |
| POST | `/api/scanner/start` | Start P25, VHF, UHF, and audio services |
| POST | `/api/scanner/stop` | Stop all scanning/audio and reset call counters |
| GET | `/api/config` | Active scanner configuration |
| POST | `/api/config/save` | Validate and save active local configuration |
| POST | `/api/config/init-local` | Initialize ignored local configuration |
| GET | `/api/receivers/inventory` | Configured receiver-role inventory |

## Analog scanner endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/analog/status` | VHF/UHF state, lock, signal, and call counts |
| GET | `/api/analog/controls` | Current skip/block control state |
| POST | `/api/analog/control` | Apply `skip`, `block`, `clear_lock`, or clear action |
| GET | `/api/analog/channels` | Current imported analog channel collection |
| POST | `/api/analog/channels/import` | Parse/import a CHIRP-compatible CSV payload |
| POST | `/api/p25/csv/import` | Parse/import P25 system and talkgroup CSV data |

Analog control requests identify a role such as `analog_2m` or `analog_70cm`.
The server validates actions and owns the ten-minute skip expiry.

## Named profiles

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/config/named` | List saved profiles |
| POST | `/api/config/named/save` | Save a named profile |
| POST | `/api/config/named/load` | Load and optionally apply a profile |
| POST | `/api/config/named/delete` | Delete a profile |
| POST | `/api/config/named/export` | Export profile data as CSV |

Profiles live in ignored runtime storage. Names should describe a coverage area
or operating purpose and must not be treated as authorization to monitor it.

## Decoder and audio endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/decoder/capability` | OP25 discovery and launch readiness |
| GET | `/api/op25/generated-config` | Generated OP25 artifact manifest |
| POST | `/api/decoder/generate-config` | Regenerate OP25 runtime configuration |
| GET | `/api/audio/status` | Backend audio bridge status |
| GET | `/radio/api/audio/status` | Radio-host arbitrator/fanout status through proxy |
| GET | `/audio.pcm` | Continuous browser PCM stream through proxy |

`/audio.pcm` is a long-lived stream. Each browser attaches independently; a
new listener must not start, stop, or retune scanner services.

## Errors and verification

Invalid requests return an HTTP error and a JSON error message where practical.
Clients should treat absent fields as unknown, tolerate added fields, and avoid
inferring radio lock from HTTP success alone. Verify a start/stop operation by
polling `/api/status` and `/api/analog/status` until all roles converge.

Do not expose these endpoints directly to an untrusted network. Authentication,
TLS, and access control belong at the ROC reverse proxy or an upstream gateway.

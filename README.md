# PI Scanner

Raspberry Pi 5 P25, VHF, and UHF scanner for dedicated RTL-SDR receivers.

Current release: **v3.0.0**, introducing the split ROC/radio architecture and
continuous multi-browser PCM playback.

## User manual

See the [PI Scanner User Manual](docs/USER_MANUAL.md) for hardware setup,
RTL-SDR serial programming and role assignments, daily operation, radio
profiles, CSV imports, live channel controls, maintenance, and troubleshooting.

## Goal

This project is intended to provide a simple web-controlled scanner that:

- accepts one or more P25 control-channel frequencies,
- accepts a whitelist of talkgroup IDs,
- locks to the active P25 control channel,
- follows voice grants for allowed talkgroups,
- plays clear P25 audio,
- mutes encrypted calls, and
- shows only the minimal tuner/scanner status needed to operate the radio.

## Target runtime

The production runtime is split across two hosts:

- ROC application host `192.168.68.114`: the existing N0JCG ROC dashboard on
  port `8095`, with PI Scanner mounted at `/pi-scanner/`.
- Radio Pi `192.168.68.137`: RTL-SDR ownership, OP25, VHF/UHF workers, radio
  API on port `8070`, and audio fanout on port `8072`.
- Raspberry Pi OS / Debian Trixie full on the radio node.
- Dedicated RTL-SDR receivers selected by EEPROM serial, never USB index.

Operators open `http://192.168.68.114:8095/pi-scanner/`. The ROC forwards API
and audio traffic to the radio Pi; browsers do not connect directly to `.137`.

## Development environment

Repository staging and script handoff use Windows MSYS2 UCRT64, matching the existing Pi SDR development platform.

The preferred local path is:

```text
~/sdrdev/PI-P25-SCANNER
```

## Decoder strategy

V0.1 uses an external decoder-engine wrapper approach. The first implementation target is OP25 on the Pi, controlled by this project's Python backend. SDRTrunk may be used as a protocol and behavior reference, but SDRTrunk source code must not be copied into this repository unless the project license decision is made and documented first.

V0.1B adds OP25 config generation and guarded decoder discovery. Live OP25 start is intentionally disabled until the exact Pi OP25 install path and command template are validated.

## P25 scope

Initial scope:

- P25 Phase I trunked systems
- P25 Phase II trunked systems when supported by the installed decoder path
- clear audio only
- talkgroup whitelist filtering
- control-channel lock/status
- active voice frequency/TGID/status display

Out of scope:

- encrypted audio decoding
- key recovery or decryption
- broad SDRTrunk GUI cloning
- native Windows runtime
- scanner database subscription integration

## Repository layout

```text
config/                 Radio configuration templates
deploy/                 Explicit ROC-web and radio-Pi deployment manifests
docs/                   Architecture, milestones, guardrails, notes
src/pi_p25_scanner/     Radio API, decoder control, and SDR worker code
web/                    ROC-hosted desktop and mobile browser UI
tools/                  Role-specific deployment and validation scripts
runtime/                Ignored local runtime state and backups
```

Use `tools/deploy_application_to_roc.sh` for changes under `web/`; it installs
them into the existing N0JCG-ROC `web/pi-scanner/` mount. Use
`tools/deploy_radio_to_pi.sh` for changes under `config/`, `src/`, `systemd/`,
or radio tooling. Both commands default to a non-mutating dry run.
See [Split-host deployment](docs/SPLIT_HOST_DEPLOYMENT.md).

## Development validation

On the development machine from MSYS2 UCRT64:

```bash
cd ~/sdrdev/PI-P25-SCANNER
./tools/validate_repo.sh
```

Generate OP25 runtime config files from the example project config:

```bash
./tools/p25_generate_op25_config.sh
```

## Raspberry Pi validation

On the Raspberry Pi 5:

```bash
cd ~/sdrdev/PI-P25-SCANNER
./tools/pi5_p25_preflight.sh
./tools/pi5_p25_runtime_probe.sh
./tools/pi5_p25_op25_install_probe.sh
./tools/pi5_p25_bringup_acceptance.sh
```

The runtime probe is non-invasive. It checks repo health, generates OP25 runtime files, discovers OP25 candidates, and enumerates RTL-SDR tools/devices when present. Missing OP25 is reported as a warning until the OP25 install milestone.

## Local scanner configuration

The checked-in JSON files under `config/` are templates. Runtime scanner settings should live under the ignored path `runtime/settings/p25_systems.json`.

Initialize a local editable config:

```bash
./tools/p25_init_local_config.sh
```

Validate the active local config:

```bash
./tools/p25_validate_config.sh
./tools/p25_validate_config_api.sh
```

The backend reads `P25_SCANNER_CONFIG` when set. Otherwise it prefers `runtime/settings/p25_systems.json` and falls back to `config/p25_systems.example.json`. V0.1E adds the minimal web config editor and saves UI edits only to the ignored runtime config path.

The OP25 install decision is tracked in `docs/OP25_INSTALL_DECISION.md`. Live OP25 launch remains disabled until the Pi-specific command template is validated there.


## RTL receiver role mapping

Before live P25 decode work, map receivers by stable RTL EEPROM serial:

```bash
./tools/pi5_p25_rtl_role_probe.sh
./tools/p25_set_receiver_roles.sh <control_serial> [voice_serial]
```

The role setter updates only the ignored local runtime config at `runtime/settings/p25_systems.json`.


## Pi bring-up acceptance bundle

After the repo patches are applied and pulled on the Pi, run the current non-live acceptance bundle:

```bash
./tools/pi5_p25_bringup_acceptance.sh
```

The bundle runs the existing repo, config, API, Pi runtime, OP25 capability, and RTL role probes without installing packages or launching live OP25 decode.

## OP25 live command validation

After OP25 post-install validation passes, validate the foreground OP25 command on the Pi without enabling backend live launch:

```bash
cd ~/PI-P25-SCANNER
./tools/pi5_p25_op25_live_command_probe.sh --dry-run
./tools/pi5_p25_op25_live_command_probe.sh --rx-smoke --seconds 20 --yes
```

The probe is bounded with `timeout` and records report/log files under `.p25_op25_live_command_probe_reports/`.

## Backend dev run

```bash
PYTHONPATH=src python3 src/pi_p25_scanner/backend.py --host 0.0.0.0 --port 8070
```

Useful endpoints:

- `/api/status`
- `/api/config`
- `/api/decoder/capability`
- `/api/op25/generated-config`
- `POST /api/decoder/generate-config`
- `POST /api/config/init-local`
- `POST /api/config/save`
- `POST /api/scanner/start`
- `POST /api/scanner/stop`

## Legal and safety guardrails

This project is for lawful monitoring of unencrypted radio traffic only. It must not attempt to decrypt, bypass, defeat, or recover encryption keys for protected communications. Encrypted calls should be detected, muted, and logged as encrypted/skipped.


## Guarded OP25 source path

V0.1I adds a guarded OP25 source workflow for the Pi. The default helper mode is dry-run and does not install, build, or launch OP25:

```bash
./tools/pi5_p25_op25_source_install.sh --dry-run
./tools/pi5_p25_op25_source_install.sh --clone-only --yes
./tools/pi5_p25_op25_command_candidate.sh
```

Full upstream OP25 install/build remains gated behind `--run-upstream-install --yes` and live backend OP25 launch remains disabled until `docs/OP25_INSTALL_DECISION.md` records the validated command template.
## OP25 post-install command validation

After the guarded OP25 install/build completes on the Pi, run:

```bash
./tools/pi5_p25_op25_postinstall_probe.sh
./tools/pi5_p25_op25_command_candidate.sh
```

This captures installed OP25 command evidence and help output without starting live decode. Backend live launch remains disabled until the exact command template is validated on the Pi.

## V0.1M OP25 live command smoke diagnostics

V0.1M improves the bounded OP25 live-command probe. The probe now classifies early OP25 startup failures, prints smoke-log tails into the report, and tries a runtime RTL index fallback when serial-based SDR opening does not validate. Backend live launch remains disabled until a later patch consumes validated command evidence.
## V0.2A backend live launch

After the bounded OP25 live command probe passes on the Pi, the backend can consume `runtime/settings/op25_validated_rx_command.env` for a guarded `/api/scanner/start` launch. Validate it with:

```bash
./tools/pi5_p25_backend_live_launch_probe.sh
```

## V0.2D backend service

The standard backend/UI port is `8070`. For manual foreground operation on the Pi:

```bash
cd ~/PI-P25-SCANNER
PYTHONPATH=src python3 -m pi_p25_scanner.backend --host 0.0.0.0
```

For boot-time operation, install the guarded systemd service:

```bash
cd ~/PI-P25-SCANNER
./tools/pi5_p25_backend_service_install.sh --dry-run
./tools/pi5_p25_backend_service_install.sh --install --yes
./tools/pi5_p25_backend_service_probe.sh
```

Open the UI at `http://<pi-ip>:8070`.
## V0.2E runtime status parsing

V0.2E adds conservative OP25 log parsing so the backend can populate UI status fields such as active voice frequency, TGID, P25 phase, encrypted state, and muted state from OP25 stdout/stderr when recognizable log lines are present.

Validate without live RF traffic:

```bash
./tools/pi5_p25_runtime_status_parser_probe.sh
```

## V0.2F TOPAZ/TRWC Mesa test profile

V0.2F adds a guarded TOPAZ Regional Wireless Cooperative (TRWC) Mesa Simulcast test profile for later real-world validation. The profile is checked in at `config/topaz_trwc_mesa_test.json`; applying it writes only the ignored runtime config at `runtime/settings/p25_systems.json`.

```bash
./tools/pi5_p25_topaz_trwc_profile_probe.sh
./tools/p25_init_topaz_trwc_test_config.sh --dry-run
./tools/p25_init_topaz_trwc_test_config.sh --apply --yes
```

The profile includes Mesa Simulcast control-channel-capable frequencies plus a focused fire, EMS, law dispatch, and interop talkgroup starter list. Encrypted talkgroups remain mute/skip-only; the project must not attempt encrypted audio decoding or key handling.

## V0.2G TOPAZ/TRWC live RF probe

After applying the TOPAZ/TRWC test profile and refreshing the validated OP25 marker, run a bounded live RF observation from the Pi:

```bash
./tools/pi5_p25_topaz_trwc_live_rf_probe.sh --seconds 90 --yes
```

The probe starts decode through the backend API on port 8070, samples `/api/status`, writes a report under `.p25_topaz_trwc_live_rf_probe_reports/`, and stops decode unless `--leave-running` is supplied. Lack of observed TGID or voice-frequency activity is reported as a warning, not a failure.

## V0.2H runtime activity summary

V0.2H adds an in-memory runtime activity summary to `/api/status` and the web UI. It counts parsed OP25 status lines, control/voice updates, talkgroup observations, clear events, encrypted events, muted/skipped events, and recent parsed activity. Validate it with:

```bash
./tools/pi5_p25_runtime_activity_probe.sh
```

## V0.2I live activity capture

After V0.2H activity counters are available, archive a repeatable live test run
from the Pi with:

```bash
./tools/pi5_p25_live_activity_capture.sh --seconds 180 --interval 3 --yes
```

The capture writes JSONL snapshots and summary reports under
`.p25_live_activity_capture_reports/` and copies summary evidence under
`runtime/evidence/`. The tool is observational and does not change the validated
OP25 launch command or encryption behavior.
## V0.2J live evidence analysis

After a bounded live activity capture, summarize the captured status snapshots:

```bash
./tools/pi5_p25_live_evidence_analyze.sh --latest
```

The analyzer reads local evidence JSON, writes Markdown/JSON summaries under `.p25_live_evidence_analyze_reports/`, and reports control/voice frequency evidence, TGID evidence, clear voice events, encrypted-call metadata, muted/skipped events, warnings, and recent OP25 log-tail lines. It is observational only and does not change OP25 launch behavior.
## V0.2K OP25 voice-frame parser

V0.2K recognizes OP25 `IMBE (PLAINTEXT)` and `AMBE (PLAINTEXT)` log lines as clear voice-frame metadata so live evidence captures can report clear voice activity even when OP25 does not print TGID/frequency on the same line. Encrypted frame metadata is still detect/show/mute/skip only; no decryption behavior is added.
## V0.2L active TGID evidence guard

V0.2L separates OP25 whitelist/configuration talkgroup log lines from active call talkgroup evidence. Lines such as `added talkgroup ... from *_whitelist.tsv` are retained as parsed configuration notes but are not counted as active TGID activity. Active TGID evidence must come from live voice/grant/call-style runtime lines.
## V0.2M OP25 discovery trust

V0.2M classifies known OP25 source-tree app paths as trusted decoder candidates so the backend no longer warns about a generic `rx.py` when the path is under `op25/op25/gr-op25_repeater/apps`. Generic unrelated `rx.py` paths still warn. Validate with `./tools/pi5_p25_op25_discovery_trust_probe.sh`.

## V0.2N OP25 metadata discovery

V0.2N adds `tools/pi5_p25_op25_metadata_discovery_probe.sh`, a live evidence probe that classifies OP25 backend log-tail lines into active TGID candidates, voice-frequency candidates, configured whitelist/blacklist TGID lines, plaintext voice frames, encrypted metadata, and control-channel lines. This is used to discover the exact active-call metadata format emitted by the installed OP25 build before extending parser behavior.

## V0.2O OP25 interface discovery

V0.2O adds `tools/pi5_p25_op25_interface_discovery_probe.sh`, a Pi-side discovery probe that checks backend status snapshots, localhost listeners, possible OP25 HTTP/status endpoints, and OP25 source/app files for metadata interface clues. This is used after V0.2N when OP25 log text shows control lock and plaintext voice frames but no active TGID or voice-frequency metadata.

### V0.2T OP25 HTTP runtime probe

`tools/pi5_p25_op25_http_runtime_probe.sh` performs a short live diagnostic of the OP25 HTTP terminal/listener path. It checks the backend start response, polls `/api/status`, extracts runtime HTTP ports such as `18091`, checks TCP listeners, and probes localhost HTTP paths.

## Upload-ready command logs

For long-running Pi validation commands, capture the full terminal transcript to an upload-ready local file:

```bash
./tools/pi5_p25_run_with_log.sh --label op25_probe -- ./tools/pi5_p25_op25_http_runtime_probe.sh --seconds 30 --interval 1 --yes
```

The helper writes stdout and stderr to `.p25_command_logs/<label>_<timestamp>.txt` and prints the log path at the beginning and end of the run.

## V0.3A scanner control dashboard

V0.3A starts application-facing dashboard work. The web UI now highlights scanner state, decoder health, control frequency, latest warning/event, and OP25 HTTP UI reachability. The backend exposes `/api/op25/http-interface` and a same-host `/op25/` proxy so the OP25 UI can be opened from the scanner dashboard when the OP25 HTTP terminal is running.

# PI P25 Scanner

Minimal Raspberry Pi 5 P25 trunk-following scanner for NooElec NESDR Nano 2+ RTL-SDR receivers.

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

- Raspberry Pi 5
- Raspberry Pi OS / Debian Trixie full
- One or two NooElec NESDR Nano 2+ RTL-SDR receivers
- Browser-based UI served from the Pi

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
config/                 Example system configuration templates
docs/                   Architecture, milestones, guardrails, notes
src/pi_p25_scanner/     Python backend/service code
web/                    Minimal browser UI
tools/                  MSYS2/Pi validation and setup scripts
runtime/                Ignored local runtime state created on the Pi
```

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
PYTHONPATH=src python3 src/pi_p25_scanner/backend.py --host 0.0.0.0 --port 8090
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

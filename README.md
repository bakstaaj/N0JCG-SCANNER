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

## First validation

On the development machine from MSYS2 UCRT64:

```bash
cd ~/sdrdev/PI-P25-SCANNER
./tools/validate_repo.sh
```

On the Raspberry Pi 5:

```bash
cd ~/sdrdev/PI-P25-SCANNER
./tools/pi5_p25_preflight.sh
```

## Legal and safety guardrails

This project is for lawful monitoring of unencrypted radio traffic only. It must not attempt to decrypt, bypass, defeat, or recover encryption keys for protected communications. Encrypted calls should be detected, muted, and logged as encrypted/skipped.

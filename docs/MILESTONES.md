# PI P25 Scanner Milestones

## V0.1A - repository baseline and preflight

- Project README.
- Guardrail file.
- Architecture document.
- Example P25 system config.
- Minimal backend status stub.
- Minimal web UI shell.
- Repo validator.
- Pi preflight validator.

## V0.1B - decoder process wrapper scaffold

- Add OP25 runtime discovery.
- Generate decoder runtime config from project JSON.
- Expose decoder capability through `/api/status` and `/api/decoder/capability`.
- Add guarded backend start/stop wrapper.
- Keep live decoder launch disabled until the Pi-specific OP25 command template is validated.
- Add Pi runtime probe that separates missing decoder tooling warnings from hard repo/runtime failures.

## V0.1C - OP25 install/capability decision

- Add OP25 install/capability decision document.
- Add non-invasive Pi OP25 install probe.
- Keep package install/build and live launch out of this milestone.
- Choose and document the supported OP25 install path for Raspberry Pi 5 / Trixie.
- Validate OP25 command-line invocation with one attached NESDR Nano2+.
- Validate whether the installed path supports Phase II.
- Document exact decoder start command and required package/build dependencies.

## V0.1D - local scanner config workflow

- Add local runtime scanner config template.
- Add config init and validation scripts.
- Backend prefers ignored runtime config over checked-in example config.
- Keep UI editing for the next milestone after config persistence rules are validated.

## V0.1E - P25 config UI

- Add minimal web config editor.
- Add `/api/config/init-local` and `/api/config/save`.
- Add loopback config API smoke validator.
- Edit control channel list.
- Edit talkgroup whitelist.
- Select one-SDR or two-SDR mode.
- Configure RTL serial roles.
- Configure gain and PPM.

## V0.1E - live control-channel validation

- Confirm RTL serial mapping.
- Confirm control-channel lock.
- Confirm system/NAC/WACN/site metadata when available.
- Keep no-traffic RF conditions as warnings when lock and process health pass.

## V0.1F - talkgroup-following validation

- Follow allowed TGID voice grants.
- Mute disallowed TGIDs.
- Mute encrypted calls.
- Show active TGID/frequency/phase/status in UI.


## V0.1F - RTL receiver role mapping

- Add Pi-side RTL serial/index evidence probe.
- Add local config helper to assign `p25_control` and optional `p25_voice` serials.
- Keep backend ownership serial-first and avoid hard-coded runtime indexes.
- Keep live OP25 launch disabled until decoder install/capability is validated.

## V0.2 - service install and acceptance bundle

- Add systemd service installer.
- Add foreground/dev-mode validator.
- Add service smoke validator.
- Add single acceptance bundle with top-level `FINAL: PASS`/`FINAL: FAIL`.

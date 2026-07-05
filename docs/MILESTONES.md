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


## V0.1G - Pi bring-up acceptance bundle

- Add a single Pi-side acceptance bundle for current non-live milestones.
- Run repo, config, API, preflight, runtime, OP25 capability, and RTL role probes from one command.
- Keep the bundle non-invasive: no package install, no OP25 build, no live decoder launch.
- Preserve RF/no-traffic and optional decoder-tool gaps as warnings until a live decode milestone requires them.

## V0.1K - OP25 live command validation

- Add bounded Pi-side OP25 foreground command probe.
- Generate dry-run command evidence from runtime config, RTL serial roles, and OP25 source marker.
- Allow optional short `rx.py` smoke run behind explicit `--yes`.
- Keep backend live OP25 launch disabled until the validated command template is committed.

## V0.2 - service install and acceptance bundle

- Add systemd service installer.
- Add foreground/dev-mode validator.
- Add service smoke validator.
- Add single acceptance bundle with top-level `FINAL: PASS`/`FINAL: FAIL`.


## V0.1I - guarded OP25 source path

- Add dry-run-first OP25 source helper.
- Add clone-only guarded OP25 source acquisition.
- Add non-invasive OP25 command-candidate evidence script.
- Keep full upstream install/build behind explicit `--run-upstream-install --yes`.
- Keep backend live OP25 launch disabled until the exact command template is validated on the Pi.
## V0.1J - OP25 post-install command validation

- Add bounded post-install OP25 command/help probe.
- Verify OP25 source app paths after upstream install/build.
- Capture GNU Radio/import evidence when available.
- Keep live backend OP25 launch disabled until a bounded manual control-channel command is validated.

## V0.1M OP25 live command smoke diagnostics

- Improve the bounded rx.py foreground smoke validator.
- Keep dry-run as the default.
- Classify early OP25 failures from smoke logs.
- Try stable serial first and runtime RTL index second when available.
- Do not enable backend live launch.
## V0.2A - Guarded backend OP25 launch

- Consume the validated OP25 command marker from `runtime/settings/op25_validated_rx_command.env`.
- Add a bounded backend start/status/stop validation probe.
- Keep live launch disabled when the marker is missing or invalid.

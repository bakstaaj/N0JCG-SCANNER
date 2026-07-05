# PI P25 Scanner Milestones

## V0.1A — repository baseline and preflight

- Project README.
- Guardrail file.
- Architecture document.
- Example P25 system config.
- Minimal backend status stub.
- Minimal web UI shell.
- Repo validator.
- Pi preflight validator.

## V0.1B — decoder process wrapper

- Add OP25 runtime discovery.
- Generate decoder runtime config from project JSON.
- Start/stop decoder from backend.
- Capture logs and process state.
- Expose decoder health through `/api/status`.

## V0.1C — P25 config UI

- Edit control channel list.
- Edit talkgroup whitelist.
- Select one-SDR or two-SDR mode.
- Configure RTL serial roles.
- Configure gain and PPM.

## V0.1D — live control-channel validation

- Confirm RTL serial mapping.
- Confirm control-channel lock.
- Confirm system/NAC/WACN/site metadata when available.
- Keep no-traffic RF conditions as warnings when lock and process health pass.

## V0.1E — talkgroup-following validation

- Follow allowed TGID voice grants.
- Mute disallowed TGIDs.
- Mute encrypted calls.
- Show active TGID/frequency/phase/status in UI.

## V0.2 — service install and acceptance bundle

- Add systemd service installer.
- Add foreground/dev-mode validator.
- Add service smoke validator.
- Add single acceptance bundle with top-level `FINAL: PASS`/`FINAL: FAIL`.

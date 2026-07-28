# PI-SCANNER v1.0.19

## Current deployed Pi state

This release intentionally captures and commits the exact VHF implementation
currently deployed on the PI-SCANNER device.

No additional troubleshooting, runtime repair, tuning changes, or source
transformation was performed as part of this release.

### Captured VHF implementation

- Worker: `persistent_vhf_fft_scanner.py`
- Service override: `90-persistent-fft.conf`
- VHF receiver serial: `00000144`
- VHF audio UDP port: `23458`
- Separate `rtl_fm` audio handoff
- Generic candidate ranking by RF margin
- Three PCM frames required before confirming a lock
- No channel-specific priority behavior

### Runtime state at release capture

- VHF systemd service: `active`
- UHF systemd service: `active`
- VHF status: `state=stopped, search_mode=persistent_fft_rtl_tcp, voice_demodulator=separate_vhf_rtl_fm_dc_deemp, locks=0, frames=0`

The VHF status record reported `state=stopped` at capture time. This release
preserves that current state rather than claiming the VHF runtime is validated
or fully operational.

### Included snapshots

- Current analog receiver configuration
- Current VHF runtime status JSON
- Effective VHF systemd unit and overrides
- Effective VHF ExecStart

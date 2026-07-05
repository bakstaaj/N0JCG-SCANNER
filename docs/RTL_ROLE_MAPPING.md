# RTL Receiver Role Mapping

The P25 scanner must own RTL-SDR receivers by stable EEPROM serial, not by Linux runtime index.

## Roles

- `p25_control` - receiver parked on the P25 trunking control channel.
- `p25_voice` - optional second receiver used for voice-following traffic channels.

One-SDR mode is allowed for early tests by assigning only `p25_control`, but two-SDR mode is the preferred operating model.

## Pi-side probe

Run this on the Raspberry Pi 5 from the repository root:

```bash
./tools/pi5_p25_rtl_role_probe.sh
```

The probe is non-invasive. It records USB/RTL evidence and writes a local report under `.p25_rtl_role_probe_reports/`. When serials can be parsed, it also writes:

```text
runtime/settings/rtl_receiver_roles.detected.json
```

## Apply roles to local scanner config

After choosing stable EEPROM serials, initialize or update the ignored local scanner config:

```bash
./tools/p25_set_receiver_roles.sh <control_serial> [voice_serial]
```

Example:

```bash
./tools/p25_set_receiver_roles.sh 00001090 00000162
```

The script updates only `runtime/settings/p25_systems.json`; checked-in templates under `config/` remain unchanged.

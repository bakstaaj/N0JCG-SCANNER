# TOPAZ/TRWC Mesa test profile

V0.2F adds a guarded local test profile for the TOPAZ Regional Wireless Cooperative (TRWC) Mesa Simulcast site.

The checked-in profile is:

```text
config/topaz_trwc_mesa_test.json
```

The profile is intended for lawful monitoring of unencrypted traffic only. Encrypted talkgroups may be included for status parser and skip/mute validation, but encrypted audio must remain muted/skipped. The project must not attempt decryption, key loading, key recovery, or encryption bypass.

## Runtime initialization

Dry-run first:

```bash
./tools/p25_init_topaz_trwc_test_config.sh --dry-run
```

Apply to the ignored runtime config:

```bash
./tools/p25_init_topaz_trwc_test_config.sh --apply --yes
```

The initializer preserves existing RTL receiver roles from `runtime/settings/p25_systems.json` when possible, backs up the previous runtime config, validates the merged config, and regenerates OP25 runtime files.

## Pi validation

```bash
./tools/pi5_p25_topaz_trwc_profile_probe.sh
./tools/p25_validate_config.sh
./tools/p25_generate_op25_config.sh
```

After the TOPAZ/TRWC profile is active, rerun the OP25 command probe before live UI testing:

```bash
./tools/pi5_p25_op25_live_command_probe.sh --dry-run
./tools/pi5_p25_op25_live_command_probe.sh --rx-smoke --seconds 20 --yes
```

The backend Start button remains gated by `runtime/settings/op25_validated_rx_command.env`.

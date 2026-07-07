# Known-Good TOPAZ / TRWC Mesa Discovery Template

This file documents the temporary local template used while waiting for RadioReference API access.

## Template

`config/templates/topaz_trwc_mesa_discovery_2500_4500.json`

## Control channels

The template includes the Mesa Simulcast control channels used during project testing:

- 852.750000 MHz
- 852.825000 MHz
- 853.275000 MHz
- 853.350000 MHz

## Talkgroups

The template includes discovery TGIDs from `2500` through `4500`.

Known encrypted/problem TGIDs from prior project testing are included as disabled entries so they are not written to the OP25 whitelist:

- 2900
- 2901
- 2902
- 2903
- 2904
- 3107
- 3840

The remaining TGIDs are enabled with placeholder labels such as `Discovery TGID 2500`. Replace these with verified names when RadioReference import or field verification is available.

## Apply to Pi

From MSYS2 UCRT64:

```bash
cd ~/sdrdev/PI-P25-SCANNER
./tools/msys2_upload_pi_config_template.sh
```

Optional explicit path:

```bash
./tools/msys2_upload_pi_config_template.sh --template config/templates/topaz_trwc_mesa_discovery_2500_4500.json
```

The helper backs up the current runtime config, writes the template to `runtime/settings/p25_systems.json`, and regenerates OP25 runtime files under `runtime/op25`.

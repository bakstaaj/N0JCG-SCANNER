# Analog 2 m / 70 cm CSV Channel Import

This branch starts from the current `PI-P25-SCANNER/main` baseline. It adds the
CSV channel-list configuration boundary only; analog SDR workers are connected
in the next feature phase.

Required columns are `receiver` and `frequency_mhz`. Receiver values may be
`2m`, `70cm`, `analog_2m`, or `analog_70cm`.

Common optional columns are `name`, `mode`, `ctcss_hz`, `dcs_code`, `enabled`,
and `priority`. Advanced optional columns are `gain_db`, `squelch_rms`,
`hold_seconds`, `resume_delay_seconds`, `recording_enabled`, `tone_gate`, and
`dcs_gate`.

The normal import replaces only receiver roles present in the file. A 2 m-only
upload therefore preserves the current 70 cm list.

Fixed serial bindings:

- `analog_2m`: `00000440`
- `analog_70cm`: `00000144`

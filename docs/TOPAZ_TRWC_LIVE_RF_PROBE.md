# TOPAZ/TRWC live RF probe

V0.2G adds a bounded Pi-side live RF probe for the TOPAZ/TRWC test profile.

The probe uses the existing backend API on port 8070 and the validated OP25 command marker at `runtime/settings/op25_validated_rx_command.env`. It does not install packages, change systemd service state, change the validated OP25 command, or attempt any encrypted audio handling.

## Intended sequence

```bash
cd ~/scanner
./tools/p25_init_topaz_trwc_test_config.sh --apply --yes
./tools/pi5_p25_op25_live_command_probe.sh --rx-smoke --seconds 20 --yes
./tools/pi5_p25_topaz_trwc_live_rf_probe.sh --seconds 90 --yes
```

The probe starts scanner decode through `/api/scanner/start`, samples `/api/status`, and stops decode at the end unless `--leave-running` is supplied.

## PASS/WARN behavior

`FINAL: PASS` means the backend accepted the validated marker, OP25 stayed running throughout the bounded window, status samples were captured, and the decoder was stopped cleanly.

A warning is emitted when no TGID or voice-frequency activity is observed. That can happen when the system is quiet, the selected site is not decodable from the antenna location, gain/PPM needs adjustment, or the control channel is not locked.

## Encryption policy

Encrypted calls remain metadata only. The scanner may show encrypted/muted/skipped state, but the project does not decrypt, bypass, recover, or load keys for protected communications.

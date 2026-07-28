# PI-SCANNER v2.0.0 release record

PI-SCANNER v2.0.0 is the first major release with the rebuilt VHF scanner
validated end to end on the deployed Raspberry Pi.

The release removes the v1.0.19 patched worker and service override, corrects
the receiver assignments to VHF `00000144` and UHF `00000440`, and makes
`src/pi_p25_scanner/vhf_fft_scanner.py` the maintained implementation behind
the stable VHF service entry point.

See `RELEASE_NOTES_v2.0.0.md` in the repository root for the implementation,
live acceptance evidence, validation results, and upgrade procedure.

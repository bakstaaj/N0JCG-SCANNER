# Phase 13 C4FM and Tenderfoot II Control-Channel Correction

Marker: `PHASE13_C4FM_TENDERFOOT_CONTROL_FIX_V0_6K`

This phase makes OP25 demodulation explicit through the optional marker field:

`P25_VALIDATED_RX_DEMOD_TYPE`

Accepted values are `cqpsk` and `fsk4`.

Tenderfoot II is tested with C4FM (`fsk4`) and these current
control-capable frequencies:

- 852.2250 MHz
- 853.3000 MHz
- 853.5375 MHz
- 853.7500 MHz

The deployment captures a CQPSK baseline and a C4FM candidate, downloads both
browser-audio WAV files, and automatically restores the original configuration
if the candidate decoder is unstable or objectively worse.

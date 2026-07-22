# Phase 9 DCS / DPL Decoding

Marker: `PHASE9_DCS_TONE_GATE_V0_6H`

Phase 9 activates the DCS metadata field introduced with the analog channel
editor.

## Decoder

- Demodulated 8 kHz PCM input
- 250 Hz single-pole low-pass filter
- 134.4 bit/s NRZ integration
- Eight parallel bit-timing hypotheses
- Golay(23,12) systematic codeword generation
- All 23 cyclic rotations
- Normal and inverted polarity
- Hamming-distance threshold of two bits
- Sustained matching windows required for lock
- Sustained misses required for release

## Configuration

- `023` accepts normal or inverted polarity.
- `023N` accepts normal polarity only.
- `023I` accepts inverted polarity only.
- **DCS Gate** is off by default.
- CTCSS Gate and DCS Gate cannot both be enabled on the same channel.

Existing channels migrate to schema 4 with `dcs_gate: false`, preserving
carrier-only operation.

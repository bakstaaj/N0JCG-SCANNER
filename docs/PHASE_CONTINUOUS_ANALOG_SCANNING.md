# Continuous Analog Scanning

Both analog receivers now scan continuously using the validated RF acquisition
profile while retaining the existing 8 kHz browser audio bridges.

- RF input: 240 kHz
- Browser PCM: 8 kHz signed 16-bit mono
- Gain: 49.6 dB
- PPM: 0
- Offset tuning and DC block enabled
- FM deemphasis enabled
- Serial-bound VHF and UHF receivers
- Adaptive RMS threshold per channel
- Three-of-five frame activity confirmation
- Prebuffered audio on lock
- Per-channel hold and release timing
- Automatic return to scanning after silence
- Status reports channel tunes, cycles, locks, thresholds, and last lock

No live traffic is required for deployment acceptance. Successful channel
cycling and healthy services prove the scanner is operating; lock events will
appear naturally when transmissions occur.

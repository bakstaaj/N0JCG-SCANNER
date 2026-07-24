# Analog AM/FM Tuning Checkpoint

This checkpoint ports the validated Air Traffic RTL-SDR tuning profile into
bounded PI-SCANNER diagnostics while leaving the working 8 kHz live browser
streams unchanged.

Diagnostic profile:

- RF input rate: 240,000 samples/second
- PCM WAV rate: 24,000 Hz
- PCM format: signed 16-bit mono
- Gain: 49.6 dB
- PPM: 0
- Offset tuning: enabled/requested
- DC block: enabled
- FM/NFM deemphasis: enabled
- AM deemphasis: disabled
- Receiver selection: EEPROM serial
- Two captures per receiver
- Human listening required before production promotion

The USBFS memory oneshot service is installed before both analog workers.

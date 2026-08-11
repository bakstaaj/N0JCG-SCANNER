# scanner v1.0.0

scanner v1.0.0 is the first major stable release of the dedicated
Raspberry Pi P25 trunked-radio scanner. It establishes a validated
multi-receiver OP25 architecture with independent control and voice
demodulation, reliable browser audio, receiver-role protection, and truthful
runtime status.

## Validated production architecture

- Raspberry Pi 5 running the P25 scanner backend and web interface.
- Dedicated control receiver:
  - RTL serial `00000251`
  - CQPSK demodulation
  - `LNA:40`
- Dedicated voice receiver:
  - RTL serial `00000252`
  - FSK4 demodulation
  - `LNA:49`
- OP25 `multi_rx.py` runtime with one protected control receiver and scalable
  dedicated voice receivers.
- Backend and web interface on TCP port `8070`.
- Raw browser-audio endpoint on TCP port `8072`.
- OP25 per-receiver UDP audio pool on ports `23500` through `23509`.
- Selected audio forwarded to the existing browser bridge on UDP port `23456`.

## P25 control-channel behavior

- Sticky control-channel operation tolerates consecutive framing timeouts before
  hunting fallback channels.
- Tenderfoot II effective control-channel list:
  - `853.300000 MHz`
  - `853.537500 MHz`
  - `853.750000 MHz`
- `852.225000 MHz` is excluded from control-channel scanning but remains
  available for dynamically assigned voice traffic.
- The dedicated control receiver uses an impossible TGID whitelist entry so it
  cannot be consumed by a voice grant.

## Voice and browser-audio behavior

- Independent control and voice demodulator settings.
- FSK4 voice decoding produces active, intelligible Phase I PCM where CQPSK
  voice produced effectively silent PCM.
- Audio-pool minimum RMS threshold: `25`.
- Per-burst acquisition warm-up: eight 20 ms frames, approximately `160 ms`.
- OP25 `DRAIN` and `DROP` UDP flags are decoded as explicit audio-burst
  boundaries.
- The selected voice source remains held through quiet speech while PCM packets
  continue arriving.
- Lost-packet safety release occurs after `2.5 seconds` without PCM.
- Audio sources are selected without mixing.

## Runtime and UI improvements

- Receiver inventory and persistent receiver-role registry.
- Scalable multi-receiver state and configuration telemetry.
- Truthful control and voice frequency reporting.
- Voice-assignment and audio-pool activity counters.
- Balanced UI polling to avoid excessive backend load.
- Rotating OP25 runtime log:
  - 8 MiB active log
  - five retained backups
- Control-frequency parsing no longer misclassifies unqualified voice tuning
  messages as control-channel changes.

## Hardware role registry

The validated seven-receiver inventory preserves these assignments:

| Role | RTL serial |
|---|---:|
| P25 control | `00000251` |
| P25 voice | `00000252` |
| NOAA / airband | `00000162` |
| ADS-B 1090 | `00001090` |
| UAT 978 | `00000978` |
| Analog 2 m | `00000440` |
| Analog 70 cm | `00000144` |

Only the P25 services are part of this release runtime. Other assigned receivers
remain reserved for their associated applications or later phases.

## Final hardware validation

The v1.0.0 release state was validated on July 23, 2026 against Colorado DTRS
Tenderfoot II, RFSS 6, Site 017.

Final flag-aware audio validation observed:

- three actual decoded audio bursts;
- 216 PCM frames;
- 64 active PCM frames;
- 24 acquisition frames suppressed, exactly eight per audio burst;
- 122 stabilized PCM frames forwarded;
- 15 OP25 DRAIN flags;
- three selected-stream boundary events;
- zero timeout-driven source releases;
- zero audio output errors.

The user confirmed that audio quality and continuity were substantially improved
after the flag-aware call-boundary and source-hold changes.

## Important limitations

- Encrypted P25 traffic cannot be decoded.
- The first approximately 160 ms of each selected voice burst is intentionally
  suppressed to remove unstable acquisition audio.
- v1.0.0 was hardware-validated with one dedicated voice receiver. The
  architecture supports adding more matching P25 voice receivers.
- This source release does not contain site credentials, RadioReference
  credentials, runtime logs, generated local settings, or other device-specific
  secrets.

## Release lineage

- Recovery/main checkpoint:
  `a83da5307636ebf32deece49b2ad9a2639837c44`
- Validated Phase 32 audio-boundary commit:
  `1de841591071fb693f20aa40929cd707cb6d0d07`
- Release branch:
  `feature/scalable-p25-multi-rx-v2`

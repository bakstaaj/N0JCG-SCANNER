# PI-SCANNER v2.0.0

This major release replaces the retired v1.0.19 patched VHF path with a
single-owner FFT-directed NFM scanner validated on the deployed Raspberry Pi.

## Major changes

- Binds the VHF scanner to RTL-SDR serial `00000144` and the UHF scanner to
  serial `00000440`; the VHF worker fails closed if its serial is reversed.
- Replaces `persistent_vhf_fft_scanner.py` and the
  `90-persistent-fft.conf` service override with the maintained
  `vhf_fft_scanner.py` worker behind the stable `analog_vhf_worker` entry point.
- Surveys only enabled uploaded VHF FM/NFM frequencies across grouped FFT
  spans, validates candidates away from the tuner DC spike, demodulates NFM
  in-process, and forwards 8 kHz mono PCM to the unified audio arbitrator on
  UDP port `23458`.
- Actively drains and discards queued `rtl_tcp` IQ after sample-rate and tuner
  changes so FFT and narrowband validation use samples from the requested RF
  center.
- Rejects weak, off-frequency, silent, and noise-only candidates using
  multi-slice carrier and audio evidence while preserving complete calls
  through modulation-related metric dips.
- Adds learned artifact baselines, signal-rise ranking, cooldown override,
  priority-channel short-circuiting, and separate strong-call carrier hang.
- Preserves per-frequency validation history, the last successful lock, and a
  bounded `vhf_last_call.wav` diagnostic containing the exact PCM sent to the
  browser.
- Makes the analog channel API honor `PI_SCANNER_ANALOG_ROOT`, keeping channel
  uploads and worker runtime configuration on the same checkout.
- Updates deployment, service installation, role mapping, hardware smoke
  tests, tuning capture tools, and documentation for the corrected serial
  assignments and new VHF state machine.

## Live acceptance

- VHF receiver: serial `00000144`
- UHF receiver: serial `00000440`
- N0JCG simplex test: `146.600 MHz`
- Measured VHF carrier: approximately `50 dB` narrowband SNR
- Measured frequency error: approximately `27 Hz`
- Result: continuous NFM audio heard in the web application through key-down
- Additional result: a complete live VHF fire-dispatch transmission was heard
- Idle noise check: 44 FFT sweeps, 45 candidates rejected, zero false locks,
  and zero unwanted frames before final strong-call tuning
- Unified audio arbitrator: VHF packets accepted and forwarded with zero
  rejected packets during acceptance testing

## Validation

- 17 focused VHF unit and regression tests pass.
- Python compilation, JSON/schema checks, shell syntax, whitespace checks,
  isolated RTL hardware PCM smoke, UDP-to-HTTP bridge smoke, serial ownership,
  FFT sweep, service state, and web channel API checks pass.
- The deployment workflow creates a recoverable Pi backup before replacing
  files. The final acceptance deployment backup is under
  `/home/pi/n0jcg-scanner/runtime/patch_backups/`.

## Upgrade note

Run `tools/msys2_deploy_vhf_fft_scanner.sh` from MSYS2 UCRT64. The deployment
backs up the current Pi files, removes the exact retired v1.0.19 systemd
override, installs the maintained worker, verifies serial ownership, runs the
isolated hardware/audio smoke tests, restarts the VHF and backend services, and
checks the live channel API.

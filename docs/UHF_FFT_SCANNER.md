# UHF FFT-Directed NFM Scanner

The UHF worker is a single-owner scanner bound to RTL-SDR serial `00000440`.
VHF remains bound to serial `00000144`. The worker refuses to start when the
UHF runtime configuration contains a different serial or audio port.

## Runtime state machine

1. Load the enabled UHF FM/NFM channels from
   `runtime/settings/analog_receivers.json`.
2. Group those exact channel frequencies into FFT capture segments.
3. Keep one `rtl_tcp` process attached to serial `00000440` and survey each
   segment. It starts directly inside the first configured UHF window because
   this receiver cannot PLL-lock at `rtl_tcp`'s 100 MHz default. After every
   retune, actively drain queued old-frequency IQ before
   taking the FFT, then discard a bounded post-retune sample interval sized for
   the observed rtl_tcp queue depth; energy between configured channels is
   ignored.
4. Rank configured channels whose local carrier SNR passes the FFT threshold.
5. Retune each candidate with a 50 kHz tuner offset so the RTL DC spike is not
   mistaken for a carrier.
6. Validate centered carrier SNR, carrier frequency error, and demodulated NFM
   audio activity. A very strong carrier stable across at least three slices
   may enter a short probationary lock even if the initial audio classification
   is uncertain; the normal audio-hang release then removes silence or noise.
7. Validate only the strongest candidate from each sweep. Weak/off-frequency
   hits are rejected after a two-slice precheck; accepted calls require carrier
   confirmation in at least two slices, and valid-looking candidates get the
   full five-slice carrier/audio check. Reject and
   temporarily cool down silent, static-only, off-frequency, or weak
   candidates so false hits cannot delay the next wideband survey. Cooled
   channels remain in every FFT; a signal rising 6 dB above the rejected
   baseline overrides cooldown immediately, so a real call is never made
   invisible for the cooldown period. Candidate ordering uses rise above that
   learned baseline instead of absolute FFT strength, preventing a persistent
   tuner artifact from outranking a newly active channel on every sweep.
   Operator-priority channels bypass learned-noise cooldown filtering and rank
   on current SNR, preventing an earlier late/rejected transmission from
   suppressing the next call on that channel. A priority hit also short-circuits
   the remaining FFT spans and uses a three-slice validation window to reduce
   key-up-to-audio latency. Priority short-circuiting requires 25 dB survey SNR,
   above the measured 13--24 dB idle tuner artifacts.
8. For a valid call, send 8 kHz, mono, signed 16-bit PCM frames to the unified
   audio arbitrator at `127.0.0.1:23459`.
   The default NFM output gain is `105000`, 50% above the original analog level,
   to better match P25 playback volume without changing P25 audio.
9. Release when the carrier ends, useful audio has been absent for the audio
   hang interval, the operator skips/blocks the channel, or the maximum call
   duration is reached. Then return directly to FFT survey mode.
   A strongly centered carrier with demodulated energy maintains the audio
   hold even when spectral speech classification is uncertain; an unmodulated
   carrier still releases on the audio timer. Once a call is strongly confirmed,
   the normal 6 dB carrier-release threshold maintains this relaxed audio hold
   through modulation-induced per-slice SNR variation.
   Strongly confirmed calls use a 1.5-second carrier hang to bridge brief
   measurement dips without fragmenting one transmission into repeated locks;
   ordinary weak candidates retain the 0.45-second hang.
   After at least one voice-like chunk is confirmed, a strong live carrier
   retains the lock through normal speech pauses. Silence and non-voice chunks
   remain excluded from playback, so this does not reopen the closing-static
   path or turn an unmodulated carrier into continuous audio.

The receive path keeps one 100 ms chunk pending. Before voice is confirmed,
only voice-like chunks enter playback. After confirmation, all chunks with a
live carrier are forwarded so ordinary speech is not clipped by a per-chunk
classifier. When the carrier ends, the pending chunk receives a 20 ms fade and
the first carrier-ended/static chunk is discarded. Noise-only carriers never
enter the confirmed state, and tail cleanup does not shorten the carrier lock.
The UHF voice classifier permits spectral flatness through `0.65`, matching the
observed UHF NFM voice path while remaining below broadband closing static.

## Live channel controls

- **Skip 10 Min** suppresses the currently locked analog frequency for exactly
  600 seconds, releases the lock, and then makes the frequency eligible again.
- **Block Channel** suppresses the currently locked frequency with no expiry and
  releases the lock. **Clear Blocks** restores all skipped and blocked analog
  frequencies across VHF and UHF.
- **Clear Lock** releases the current analog carrier immediately without adding
  a skip or block, allowing the scanner to resume its normal search.

Skip and block require an identified active lock; a stale last-lock status is
never used as the target. The backend and both analog workers share the atomic
`runtime/settings/analog_controls.json` control file.

The runtime channel file is checked between sweeps, so a successful channel
upload becomes active without restarting the worker.

The P25 web backend runs from a separate checkout on the deployed Pi. Its
`PI_SCANNER_ANALOG_ROOT=/home/pi/n0jcg-scanner` service environment is honored by
the channel-import module, ensuring `/api/analog/channels` and CSV uploads use
the same runtime configuration as this UHF worker. The runtime-unit installer
updates that small backend module along with the analog services.

## Compatibility

The worker writes `runtime/status/analog_70cm.json` atomically and preserves the
existing dashboard fields, including `rtl_serial`, `channel_count`,
`scan_cycles`, `lock_count`, `frames_forwarded`, `current_channel`, `rms`, and
`threshold_rms`. Additional FFT, carrier, frequency-error, audio-quality, and
candidate-rejection fields make live diagnosis possible.
The most recent accepted UHF call is also retained as
`runtime/status/uhf_last_call.wav`, bounded to 30 seconds, so the exact PCM
sent to the arbitrator can be inspected during live acceptance testing.

The stable systemd entry point remains:

```text
python3 -m pi_p25_scanner.analog_uhf_worker
```

The previous linear UHF worker is retained in the repository only as reference;
the stable UHF service entry point now selects this FFT-directed worker.

## Validation

Host-side deterministic validation:

```bash
export PYTHONPATH=src
python3 -m pi_p25_scanner.analog_uhf_worker --self-test
python3 -m unittest discover -s tests -p 'test_uhf_fft_scanner.py' -v
```

Pi-side bounded hardware validation:

```bash
./tools/pi5_uhf_phase_smoke.sh
```

The Pi smoke test requires `python3`, NumPy, `rtl_tcp`, and `timeout`. It checks
the serial binding, uploaded channel count, FFT-directed mode, and at least one
completed hardware sweep without forwarding test audio.

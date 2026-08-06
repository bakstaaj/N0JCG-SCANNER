# PI-SCANNER v3.0.0

This major release separates the browser application from the radio runtime
and hardens continuous audio delivery for multiple simultaneous listeners.

## Major changes

- Hosts the browser application under the existing N0JCG ROC `/pi-scanner/`
  mount while retaining all RTL-SDR, OP25, FFT scanning, demodulation, and
  audio arbitration on the radio Pi.
- Adds explicit ROC and radio deployment manifests with role-specific dry-run,
  backup, checksum, deployment, restart, and live-validation commands.
- Uses relative browser navigation and base-path-aware API/audio requests, so
  the application does not depend on hardcoded browser-facing host addresses.
- Restores the ROC-dashboard back control adjacent to the menu button.
- Lets each desktop or mobile browser attach to an already-running scanner
  without restarting the P25, VHF, or UHF services.
- Replaces per-frame browser playback nodes with a bounded continuous PCM ring
  buffer. The player resamples 8 kHz scanner audio to the browser device rate,
  corrects slow clock drift, and reports underrun and dropped-sample metrics.
- Retains AudioWorklet support for secure deployments and supplies an
  HTTP-compatible continuous-node implementation for the current LAN ROC.
- Adds a bounded radio-side jitter recovery window so slightly late decoder
  frames are recovered instead of immediately being replaced by silence.
- Adds audio packet-gap, late-frame recovery, and active-gap counters to the
  arbitrator status contract.

## Runtime ownership

- ROC application host: static PI Scanner assets and reverse-proxy routes.
- Radio Pi: P25 decoder, VHF and UHF FFT workers, RTL-SDR ownership, radio API,
  and unified PCM fanout.
- VHF RTL-SDR serial: `00000144`.
- UHF RTL-SDR serial: `00000440`.

## Audio validation

- ROC PCM proxy cadence: 500 consecutive 20 ms frames with zero gaps over
  100 ms and a measured maximum gap near 30 ms.
- Chromium continuous-player validation: native 48 kHz output, clock-recovered
  queue settling near 110 ms, and no new underruns or dropped samples during
  the extended validation interval.
- Controlled UHF input test: 750 frames received during a 15-second
  transmission with 749 forwarded by the unified arbitrator.
- Operator acceptance after the bounded backend jitter update: longer messages
  reported improved.

## Validation

- `PYTHONPATH=src python3 -m pytest -q`: 169 tests passed.
- JavaScript syntax checks pass for desktop, mobile, AudioWorklet, and
  HTTP-compatible ring-buffer players.
- Split-host runtime validation passes ROC health, proxied radio status,
  proxied audio status, direct radio API, direct audio fanout, and served web
  asset checks.
- Live deployments created recoverable, application-specific backups on both
  the ROC and radio Pi.

## Upgrade

From MSYS2 UCRT64, configure the non-secret host and repository fields in
`.env`, then run:

```bash
./tools/deploy_application_to_roc.sh --dry-run
./tools/deploy_radio_to_pi.sh --dry-run
./tools/deploy_application_to_roc.sh --deploy --yes
./tools/deploy_radio_to_pi.sh --deploy --yes --restart
./tools/validate_split_runtime.sh
```

Deployment credentials remain local in the ignored `.env` file and are never
included in release artifacts.

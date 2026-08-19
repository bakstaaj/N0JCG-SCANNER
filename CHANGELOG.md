# Changelog

All notable changes to N0JCG Scanner are recorded here. Detailed historical
notes are available in [`docs/releases/`](docs/releases/).

## Unreleased

## 4.2.7 - 2026-08-19

- Kept the OP25 decoder alive across a backend service restart and added
  persistent event-log tailing so scanner state and control-channel events
  reconnect without launching duplicate decoders.
- Hardened external decoder detection against shell/process-name false
  positives and documented the recovery and maintenance workflow.
- Refreshed the branded operator guide and release package for this runtime.

- Made the P25 control-channel demodulator an explicit profile field named
  `control_demod_type`; Colorado DTRS profiles use `fsk4` for C4FM control
  decoding, and CSV templates/imports now carry the setting.
- Returned scanner runtime ownership to the Pi: the Pi now serves the complete
  web application, API, radio workers, and audio while the ROC provides a
  direct link to `http://<RADIO_HOST>:8070/`.

## 4.2.0 - 2026-08-11

- Made the P25 control/voice receiver split persistent: `00000251` remains the
  control receiver and `00000252` is always launched as the dedicated voice
  receiver through the scalable `multi_rx` path.
- Added fail-closed launch protection so a missing voice receiver cannot
  silently revert the scanner to single-radio mode.
- Added live antenna-alignment scoring and refreshed the branded user guide.

## 4.1.0 - 2026-08-10

- Published the recombined Pi-only scanner runtime and deployment package.
- Corrected licensing activation, browser navigation, registration-badge
  visibility, and production asset cache invalidation.
- Refreshed the release documentation and verification evidence.

## 4.0.0 - 2026-08-07

- Added reusable phone-home license activation using a stable installation S/N,
  email binding, signed offline leases, visible desktop/mobile registration
  state, and a backend-enforced five-minute scan limit for unregistered
  installations.
- Rebranded the desktop and mobile interfaces to the N0JCG visual system.
- Added a hooked return control to the ROC dashboard using relative navigation.
- Published a branded user manual in Markdown, DOCX, and PDF formats.
- Added the N0JCG-required administrator, developer, API, hardware,
  architecture, release, and changelog documentation surfaces.
- Added contributor/security guidance and GitHub CI/issue/PR templates.
- Consolidated historical release notes under `docs/releases/`.

- Promoted the N0JCG branding, licensing, deployment, and operator UI work to
  the v4.0.0 major release.
- Added the canonical radio runtime root `/home/pi/n0jcg-scanner` for P25,
  VHF, and UHF services, with migration support from legacy paths.
- Renamed the ROC application route to `/n0jcg-scanner/` and aligned the
  split-role deployment tooling and documentation with that route.
- Added dark-theme contrast fixes for navigation controls, start scanning,
  and last-heard talkgroup status text.

## 3.0.0 - 2026-08-06

- Split browser application hosting from radio and DSP services.
- Added continuous multi-client PCM fanout and desktop listen-only attachment.
- Improved P25 audio buffering for long transmissions.
- Added explicit application-host and radio-host deployment workflows.

## 2.0.0

- Rebuilt VHF scanning around FFT-directed carrier detection and NFM audio.
- Added scalable FFT-directed UHF scanning.
- Added skip, block, clear-lock, and audio-arbitration controls.
- Added named radio profiles and CSV import/export workflows.

## 1.x

- Established the P25/OP25 receiver, browser dashboard, configuration model,
  service controls, and guarded Raspberry Pi deployment workflow.

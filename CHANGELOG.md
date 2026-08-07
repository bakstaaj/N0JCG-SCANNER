# Changelog

All notable changes to N0JCG Scanner are recorded here. Detailed historical
notes are available in [`docs/releases/`](docs/releases/).

## Unreleased

- Rebranded the desktop and mobile interfaces to the N0JCG visual system.
- Added a hooked return control to the ROC dashboard using relative navigation.
- Published a branded user manual in Markdown, DOCX, and PDF formats.
- Added the N0JCG-required administrator, developer, API, hardware,
  architecture, release, and changelog documentation surfaces.
- Added contributor/security guidance and GitHub CI/issue/PR templates.
- Consolidated historical release notes under `docs/releases/`.

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

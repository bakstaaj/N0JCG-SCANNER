# N0JCG Scanner v4.0.0

Release date: 2026-08-07

N0JCG Scanner v4.0.0 is the major release that unifies the licensed scanner
application, N0JCG branding, and split ROC/Pi deployment model.

## Highlights

- Reusable phone-home licensing with stable installation serial number binding,
  email registration, signed offline leases, and a five-minute trial limit.
- Dark N0JCG operator theme across desktop and mobile views.
- Canonical radio runtime and deployment root: `/home/pi/n0jcg-scanner`.
- ROC-hosted application URL: `http://192.168.68.114:8095/n0jcg-scanner/`.
- P25, VHF, and UHF scanners controlled together by the Start/Stop workflow.
- FFT-directed analog scanning, NFM audio arbitration, multi-client PCM fanout,
  and named CSV radio profiles.
- Improved contrast and usability for navigation, start scanning, and last-heard
  talkgroup status controls.

## Deployment

Use the split-role deployment script with the repository `.env` file. Deploy the
ROC web role to the application host and the radio role to the Pi. The radio
deployment migrates legacy runtime directories when present and installs the
current systemd units under the canonical path.

## Validation

- Main branch contains the release commit and is pushed to GitHub.
- JavaScript syntax checks and repository whitespace checks pass.
- ROC route smoke test passes for `/n0jcg-scanner/`.
- Live ROC and Pi service checks were completed before packaging.

# N0JCG Scanner v4.1.0

Release date: 2026-08-10

N0JCG Scanner v4.1.0 updates the v4 licensed scanner for the recombined
Raspberry Pi deployment model. The Pi is the complete scanner host; the ROC
provides navigation to the Pi service.

## Highlights

- Recombined the web application, API, audio fanout, P25 decoder, VHF FFT
  scanner, and UHF FFT scanner on the Pi runtime at `/home/pi/n0jcg-scanner`.
- Updated deployment and operator documentation to reflect Pi-only runtime
  ownership and the direct scanner URL on port 8070.
- Corrected production licensing activation payload and Cloudflare-safe client
  identification, including the exact `scanner` product binding.
- Fixed dynamic return navigation and refreshed the N0JCG branded desktop and
  mobile assets.
- Fixed registered-badge visibility and cache invalidation so registered
  installations do not show the registration pill on the main UI.

## Validation

- Full Python suite: 189 tests passed.
- Browser asset and deployment checks passed.
- Release archive and SHA-256 checksum generated from the current `main`
  commit.

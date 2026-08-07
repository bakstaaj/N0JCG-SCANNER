# Split-host deployment

## Hosts and public URL

| Role | Address | Runtime responsibility |
|---|---|---|
| Existing N0JCG ROC | `<ROC_HOST>:8095` | ROC dashboard, PI Scanner web mount, API/audio proxy |
| Radio Pi | `<RADIO_HOST>` | RTL radios, OP25, FFT scanners, radio API, audio fanout |

Open PI Scanner at:

```text
http://<ROC_HOST>:8095/n0jcg-scanner/
```

The ROC root dashboard remains at `http://<ROC_HOST>:8095/`.

## Existing ROC routes

The `N0JCG-ROC` server already provides the required boundary:

- `/n0jcg-scanner/` serves `N0JCG-ROC/web/pi-scanner/`;
- `/n0jcg-scanner/api/*` proxies to `<RADIO_HOST>:8070`;
- `/n0jcg-scanner/audio-api/*` proxies to `<RADIO_HOST>:8072`.

PI-SCANNER does not install or operate a second web service on the ROC.

## Local `.env`

```text
ROC_USER=n0jcg
ROC_HOST=roc.example.internal
ROC_REPO=/home/n0jcg/sdrdev/N0JCG-ROC
ROC_PASSWORD=...

RADIO_USER=pi
RADIO_HOST=radio.example.internal
RADIO_REPO=/home/pi/n0jcg-scanner
RADIO_PASSWORD=...
```

Keep `.env` ignored and never commit it.

## Dry-run first

```bash
./tools/deploy_application_to_roc.sh --dry-run
./tools/deploy_radio_to_pi.sh --dry-run
```

## Deploy scanner web assets to the ROC

```bash
./tools/deploy_application_to_roc.sh --deploy --yes
```

This maps this repository's `web/` directory into
`N0JCG-ROC/web/pi-scanner/`, backs up the previous scanner bundle under the ROC
runtime backup directory, and verifies all files by SHA-256. Static updates do
not require a ROC service restart. Use `--restart` only when explicitly needed.

## Deploy radio code to the Pi

```bash
./tools/deploy_radio_to_pi.sh --deploy --yes
```

This updates radio-owned files without interrupting reception. When a change
requires processes to reload:

```bash
./tools/deploy_radio_to_pi.sh --deploy --yes --restart
```

The restart option restarts P25 and audio services and uses `try-restart` for
VHF/UHF, so scanners that were stopped remain stopped.

## Acceptance check

```bash
./tools/validate_split_runtime.sh
```

The validator checks the ROC dashboard, the mounted PI Scanner web bundle,
ROC-proxied scanner and audio status, and both direct radio Pi endpoints. Its
final line is `FINAL=PASS` only when the complete boundary is healthy.

## Deployment rule

- Browser/UI change: deploy `web/` to the ROC scanner mount only.
- RTL, OP25, FFT, audio, scanner service, or radio configuration change:
  deploy the radio manifest to `.137` only.
- API contract change: update and test both sides in a compatible order.
- ROC dashboard/server change: make it in the separate `N0JCG-ROC` repository.
- Documentation/test-only change: no runtime deployment.

# Pi-host scanner deployment

## Hosts and public URL

| Role | Address | Runtime responsibility |
|---|---|---|
| Existing N0JCG ROC | `<ROC_HOST>:8095` | ROC dashboard and direct Scanner link |
| Radio Pi | `<RADIO_HOST>:8070` | Complete scanner UI, RTL radios, OP25, FFT scanners, API, audio |

Open PI Scanner at:

```text
http://<RADIO_HOST>:8070/
```

The ROC root dashboard remains at `http://<ROC_HOST>:8095/` and its **Open
Scanner** action links directly to the Pi.

## Runtime ownership

The Pi backend serves `web/` from `/home/pi/n0jcg-scanner` on port `8070`.
The browser uses same-origin API and audio paths. The ROC does not mirror or
proxy scanner files and may remain online independently of scanner operation.

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

## Deploy the complete scanner to the Pi

```bash
./tools/deploy_radio_to_pi.sh --deploy --yes
```

This updates web, API, configuration, workers, and systemd files under
`/home/pi/n0jcg-scanner`, preserving a timestamped remote backup. When a change
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

The validator checks the ROC dashboard link, the direct Pi web application,
radio API, and audio fanout. Its final line is `FINAL=PASS` only when the
complete boundary is healthy.

## Deployment rule

- Any scanner UI, API, RTL, OP25, FFT, audio, scanner service, or radio
  configuration change: deploy the complete radio manifest to `.137`.
- ROC dashboard/server changes remain in the separate `N0JCG-ROC` repository.
- Documentation/test-only change: no runtime deployment.

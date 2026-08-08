# N0JCG Scanner

![N0JCG Scanner](web/assets/brand/N0JCG_Header_Dark_Approved.png)

**Preview release v3.0.0**

N0JCG Scanner is a receive-only software-defined radio application for clear
P25 trunked traffic and FFT-directed VHF/UHF analog channels. It combines
dedicated RTL-SDR receivers, a radio-services host, a browser application host,
and a single audio arbitrator that safely fans audio out to multiple clients.

- Product page: [n0jcg.com/products/scanner](https://www.n0jcg.com/products/scanner/)
- Report a problem: [GitHub Issues](https://github.com/bakstaaj/N0JCG-SCANNER/issues)
- Current release notes: [v3.0.0](docs/releases/RELEASE_NOTES_v3.0.0.md)

## Capabilities

- P25 Phase I/II following through an installed OP25 receiver path
- Clear-audio talkgroup filtering with encrypted calls muted
- FFT-directed VHF and UHF NFM scanning from imported channel lists
- Serial-number ownership of RTL-SDR receivers instead of unstable USB indexes
- One audio arbitrator with continuous multi-browser PCM fanout
- Separate desktop and phone-friendly browser interfaces
- Named radio profiles with CHIRP-compatible analog and P25 CSV import/export
- Split deployment: browser application on the ROC and radio/DSP services on
  the receiver host
- Server-enforced five-minute scan sessions on unregistered installations,
  with phone-home activation and a signed seven-day offline grace lease

## Documentation

| Audience | Guide |
|---|---|
| Operators | [User Guide](docs/USER_MANUAL.md) · [Branded PDF](web/docs/N0JCG_Scanner_User_Manual.pdf) |
| Administrators | [Administrator Guide](docs/ADMINISTRATOR_GUIDE.md) |
| Developers | [Developer Guide](docs/DEVELOPER_GUIDE.md) |
| Integrators | [API Reference](docs/API_REFERENCE.md) |
| Installers | [Hardware Guide](docs/HARDWARE_GUIDE.md) |
| Maintainers | [Architecture Guide](docs/ARCHITECTURE.md) |
| Release users | [Changelog](CHANGELOG.md) · [Release archive](docs/releases/) |

The [documentation index](docs/README.md) describes the support boundary and
which guide to use for each task.

## Quick start for developers

The supported Windows development shell is MSYS2 UCRT64. Clone the repository,
create a virtual environment, and install the development dependencies:

```bash
git clone https://github.com/bakstaaj/N0JCG-SCANNER.git
cd N0JCG-SCANNER
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements-dev.txt
PYTHONPATH=src python3 -m pytest -q
./tools/validate_repo.sh
```

Copy `.env.example` to `.env` and supply deployment-specific hostnames,
credentials, repository paths, and API URLs. `.env` and `runtime/` are ignored;
never commit credentials, private station addresses, or a station's receiver
serial map.

## Repository layout

```text
config/                 Versioned radio configuration examples
deploy/                 Explicit application-host and radio-host manifests
docs/                   Public guides, engineering notes, and release history
src/pi_p25_scanner/     Radio API, decoder control, and SDR workers
systemd/                Radio-host service definitions
tests/                  Automated application and regression tests
tools/                  Validation, deployment, and maintenance tools
web/                    Desktop/mobile UI and public downloadable documents
runtime/                Ignored local settings, state, logs, and backups
```

## Deployment model

The radio Pi at `<RADIO_HOST>` owns the complete scanner application:
desktop/mobile web UI, radio API, RTL-SDR devices, OP25, analog scanner
workers, and PCM fanout. The ROC at `<ROC_HOST>:8095` remains the platform
dashboard and provides a direct link to the Pi application at port `8070`.

Deployment commands default to a non-mutating dry run:

```bash
./tools/deploy_radio_to_pi.sh
```

See the [Administrator Guide](docs/ADMINISTRATOR_GUIDE.md) and
[Pi-host deployment reference](docs/SPLIT_HOST_DEPLOYMENT.md) before using a
mutating deployment option.

## Project status and support boundary

N0JCG Scanner is currently a preview product. Supported behavior is the
versioned repository configuration and the workflows documented here. OP25 is
an external dependency and encrypted traffic is intentionally unsupported.
Hardware compatibility claims apply only to configurations that have been
verified and recorded in the release documentation.

This project is for lawful reception of traffic that may be monitored in the
operator's jurisdiction. It does not decrypt, bypass, defeat, or recover keys
for protected communications.

Before contributing, read [CONTRIBUTING.md](CONTRIBUTING.md),
[SECURITY.md](SECURITY.md), and [DEV_GUARDRAILS.md](DEV_GUARDRAILS.md).

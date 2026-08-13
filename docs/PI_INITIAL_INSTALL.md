# New Raspberry Pi installation

Run the installer from the repository root in MSYS/Git Bash:

```bash
./tools/install_n0jcg_scanner_pi.sh
```

The wizard asks for the Pi address, SSH user (default `pi`), and password. It
saves reusable `PI_*` and `RADIO_*` values in the local `.env` file. The file
contains a password and must not be committed or shared.

The installer then installs Raspberry Pi OS packages, deploys the application
to `/home/<user>/n0jcg-scanner`, installs path-correct systemd units, and keeps
all scanner services stopped. It prompts for each RTL-SDR separately; attach
only the requested receiver before assigning its serial number:

| Role | Suggested serial |
|---|---|
| P25 control | `00000251` |
| P25 voice | `00000252` |
| VHF / 2 m | `00000144` |
| UHF / 70 cm | `00000440` |

Validation checks Python modules, the receiver-role JSON, RTL tooling, and
service files. After validation, open the Pi dashboard and import or create a
profile in **Radio setup**. A new installation with no named profile opens on
that screen automatically. Start systemd services only after the role map and
profile have been reviewed.

# N0JCG Scanner administrator guide

| Metadata | Value |
|---|---|
| Product | N0JCG Scanner |
| Slug | scanner-administrator-guide |
| Type | Administrator guide |
| Version | 3.0.0 |
| Status | Preview |
| Last updated | 2026-08-07 |
| Audience | System administrators and station maintainers |
| Prerequisites | Linux, systemd, SSH, reverse-proxy, and RTL-SDR familiarity |
| Estimated time | 30 minutes |
| Related | [Hardware Guide](HARDWARE_GUIDE.md), [Architecture](ARCHITECTURE.md) |
| Owner | N0JCG |

## Goal

Install and operate N0JCG Scanner without exposing private station data or
allowing more than one process to own an RTL-SDR receiver.

## Prerequisites

- One application host that serves the ROC product route
- One Linux radio host with supported RTL-SDR receivers and OP25
- Stable DNS names or deployment-specific addresses for both roles
- SSH access and systemd administration rights
- A completed receiver role map stored outside version control

## Configure

1. Copy `.env.example` to `.env`.
2. Set `ROC_HOST`, `RADIO_HOST`, repository paths, user names, and radio service
   URLs. Put passwords only in `.env` or a secret store.
3. Copy the checked-in configuration example to the ignored runtime location:

   ```bash
   ./tools/p25_init_local_config.sh
   ```

4. Assign each receiver by EEPROM serial with
   `tools/p25_set_receiver_roles.sh`. Never rely on Linux device indexes.
5. Validate configuration before deployment:

   ```bash
   ./tools/p25_validate_config.sh
   ./tools/p25_validate_config_api.sh
   ```

## Registration and trial mode

`GET /api/status` reports the radio host's stable installation S/N in
`registration.serial_number`. An unregistered installation may start normally,
but the radio backend stops P25, VHF, and UHF after five minutes. Stopping and
starting begins a new trial session.

Open **Menu → Registration**, enter the N0JCG license S/N and purchaser email,
then select **Activate license**. The radio backend contacts the N0JCG licensing
service over HTTPS. First activation binds one license to the email and
installation. A signed lease is cached in ignored `runtime/settings/` storage.

The backend revalidates at startup and every 24 hours. A previously verified
installation continues through a seven-day offline grace period if the public
service or Internet connection is temporarily unavailable. A definitive
revocation, product mismatch, email mismatch, or expired grace period returns
the appliance to trial mode. License credentials and leases must never be
committed, packaged, or copied to another installation.

## Deploy

Run both deployment tools without mutation first and review their manifests:

```bash
./tools/deploy_application_to_roc.sh
./tools/deploy_radio_to_pi.sh
```

Use each tool's documented confirmation flag only after the dry run names the
correct host and files. Application files go to the ROC host; DSP, API, config,
and systemd files go to the radio host.

## Verify

1. Confirm the radio API status endpoint returns JSON.
2. Confirm each systemd service is active and owns only its assigned serial.
3. Open the ROC product route and press **Start Scanning + Audio**.
4. Confirm P25, VHF, and UHF statuses enter scanning state.
5. Attach a second browser with **Listen** and verify both clients receive clean
   audio without restarting scanner services.
6. Run `./tools/validate_repo.sh` on the deployed source tree.

## Backup and recovery

Back up ignored runtime profiles, `.env`, and systemd overrides separately from
the repository. Do not publish the backup. Before an upgrade, record the
deployed commit and service state. To roll back, redeploy the previous tagged
commit to the affected role, restore its runtime settings, restart only the
affected services, and repeat the verification steps.

## Escalation

Capture the application version, affected role, service status, and redacted
logs. Remove credentials, private addresses, serials, and received audio before
opening a GitHub issue. Hardware ownership failures should be resolved before
changing DSP thresholds.

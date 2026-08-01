# PI Scanner User Manual

This manual covers the PI Scanner v2.0.0 production layout: P25 trunked radio,
FFT-directed VHF and UHF analog scanning, unified browser audio, radio profiles,
and four dedicated RTL-SDR receiver assignments. It is written for both initial
installation and normal daily operation.

## Contents

1. [What PI Scanner does](#1-what-pi-scanner-does)
2. [Safety and legal use](#2-safety-and-legal-use)
3. [Hardware and network requirements](#3-hardware-and-network-requirements)
4. [RTL-SDR role and serial-number plan](#4-rtl-sdr-role-and-serial-number-plan)
5. [Prepare the Raspberry Pi](#5-prepare-the-raspberry-pi)
6. [Program RTL-SDR serial numbers](#6-program-rtl-sdr-serial-numbers)
7. [Apply and verify receiver assignments](#7-apply-and-verify-receiver-assignments)
8. [Application files and services](#8-application-files-and-services)
9. [Start PI Scanner and open the web application](#9-start-pi-scanner-and-open-the-web-application)
10. [Use the Dashboard](#10-use-the-dashboard)
11. [Use Skip, Block, Clear Lock, and Clear Blocks](#11-use-skip-block-clear-lock-and-clear-blocks)
12. [Manage radio profiles](#12-manage-radio-profiles)
13. [Import and export analog CHIRP CSV files](#13-import-and-export-analog-chirp-csv-files)
14. [Import and export P25 CSV files](#14-import-and-export-p25-csv-files)
15. [Understand the audio arbitrator](#15-understand-the-audio-arbitrator)
16. [Routine maintenance and backups](#16-routine-maintenance-and-backups)
17. [Troubleshooting](#17-troubleshooting)
18. [Technical reference](#18-technical-reference)
19. [Acceptance checklist](#19-acceptance-checklist)

## 1. What PI Scanner does

PI Scanner combines three receivers in one touch-friendly web application:

- **P25:** follows permitted, clear P25 talkgroups using dedicated control and
  voice RTL-SDR receivers.
- **VHF:** surveys only uploaded VHF channels with an FFT, validates an active
  carrier, demodulates NFM audio, and returns to scanning when the call ends.
- **UHF:** uses the same FFT-directed workflow for uploaded UHF channels.
- **Audio arbitrator:** accepts P25, VHF, and UHF audio and sends one active
  source to the browser without mixing calls together.

Encrypted P25 audio is not decoded. Encrypted or blocked traffic is detected
and muted or skipped.

## 2. Safety and legal use

- Monitor only traffic that may lawfully be received in your location.
- Do not attempt to decrypt encrypted traffic or recover encryption keys.
- PI Scanner is receive-only. Never connect a transmitter directly to an
  RTL-SDR input.
- A nearby transmitter can overload or damage an SDR. Use suitable antenna
  separation, filtering, and attenuation during transmitter tests.
- Stop the service that owns a receiver before running direct `rtl_test`,
  `rtl_eeprom`, `rtl_tcp`, or `rtl_fm` commands against it.

## 3. Hardware and network requirements

The validated production system uses:

- Raspberry Pi 5 running 64-bit Debian 13 / Raspberry Pi OS Trixie.
- Reliable Pi 5 power supply.
- Four uniquely serialized RTL-SDR receivers dedicated to PI Scanner.
- A powered USB hub suitable for the combined current draw of the receivers.
- Appropriate antennas or a properly engineered receive-only distribution
  system.
- Wired Ethernet or reliable Wi-Fi on the same network as the operator device.
- A modern browser with Web Audio support.

For best RF performance, label every dongle physically after assigning its
serial. Keep antenna leads and USB cables identifiable. VHF, UHF, and 700/800
MHz P25 benefit from band-appropriate antennas and filters.

## 4. RTL-SDR role and serial-number plan

Linux device indexes such as `device 0` change after reboots and USB
reconnection. PI Scanner therefore owns receivers by the stable eight-digit
serial stored in each RTL-SDR EEPROM.

| Role | Required serial | Operational use |
|---|---:|---|
| P25 control | `00000251` | Remains on the trunked-system control channel |
| P25 voice | `00000252` | Follows P25 voice-channel grants |
| VHF / analog 2 m | `00000144` | FFT-directed VHF NFM scanner |
| UHF / analog 70 cm | `00000440` | FFT-directed UHF NFM scanner |

The two analog assignments are mandatory: VHF is `00000144` and UHF is
`00000440`. Do not swap them. The VHF and UHF workers fail closed if their
runtime serial or audio port is wrong.

## 5. Prepare the Raspberry Pi

### 5.1 Install baseline packages

On the Pi:

```bash
sudo apt update
sudo apt install -y git rtl-sdr sox netcat-openbsd usbutils python3 python3-numpy
```

The P25 receiver also requires the validated OP25 installation. The deployed
system uses:

```text
/home/pi/op25/op25/gr-op25_repeater/apps/rx.py
```

Use the guarded project probes and installer workflow rather than guessing an
OP25 command:

```bash
cd /home/pi/PI-P25-SCANNER
./tools/pi5_p25_op25_install_probe.sh
./tools/pi5_p25_op25_postinstall_probe.sh
./tools/pi5_p25_op25_live_command_probe.sh --dry-run
```

### 5.2 Prevent the television driver from claiming RTL-SDR devices

The DVB kernel modules must be blacklisted for SDR use:

```bash
printf '%s\n' \
  'blacklist dvb_usb_rtl28xxu' \
  'blacklist rtl2832' \
  'blacklist rtl2830' \
  | sudo tee /etc/modprobe.d/rtl-sdr-blacklist.conf
sudo reboot
```

After reboot, this command should normally print no matching modules:

```bash
lsmod | grep -E 'dvb_usb_rtl28xxu|rtl2832|rtl2830'
```

## 6. Program RTL-SDR serial numbers

Serial programming is safest with one receiver connected at a time.

### 6.1 Stop every service that may own an SDR

```bash
sudo systemctl stop pi-p25-scanner.service
sudo systemctl stop pi-scanner-vhf-worker.service
sudo systemctl stop pi-scanner-uhf-worker.service
```

Also stop any other application that owns an SDR connected to the Pi. Confirm
that no unrelated receiver process still owns the device before changing its
EEPROM.

### 6.2 Disconnect all RTL-SDR receivers

Unplug every RTL-SDR. Connect only the receiver that will receive the first
serial. Confirm that exactly one receiver is visible:

```bash
rtl_test -t
```

### 6.3 Write the serial

For example, to prepare the VHF receiver:

```bash
sudo rtl_eeprom -d 0 -s 00000144
```

Read the warning, confirm the write, then unplug and reconnect the receiver.
Verify it:

```bash
rtl_eeprom -d 0
```

Repeat the one-at-a-time procedure for each role in the table above. Write the
role and serial on a physical label before moving to the next receiver.

Important rules:

- Use exactly eight numeric digits, including leading zeroes.
- Never give two connected receivers the same serial.
- The `-d 0` index is safe here only because exactly one receiver is connected.
- Never store runtime USB indexes as application role assignments.
- Power-cycle or unplug/reconnect a receiver after changing its EEPROM.

### 6.4 Verify all receivers together

Reconnect all four PI Scanner receivers while the scanner services remain
stopped:

```bash
rtl_test -t
```

The first lines should list every expected serial exactly once. To inspect each
EEPROM individually, use the temporary indexes shown by `rtl_test`:

```bash
rtl_eeprom -d 0
rtl_eeprom -d 1
```

Continue through the last displayed index. The index order may change later;
only the printed serial is persistent.

## 7. Apply and verify receiver assignments

### 7.1 Apply the canonical four-receiver map

From the P25 application directory, preview the role map:

```bash
cd /home/pi/PI-P25-SCANNER
./tools/pi5_apply_receiver_serial_map.sh --dry-run
```

Apply it after verifying the table:

```bash
./tools/pi5_apply_receiver_serial_map.sh --apply --yes
```

The tool validates uniqueness, backs up an existing registry, and writes:

```text
/home/pi/PI-P25-SCANNER/runtime/settings/receiver_roles.json
```

### 7.2 Verify the application inventory

Start the backend, then query the inventory:

```bash
sudo systemctl restart pi-p25-scanner.service
curl -fsS http://127.0.0.1:8070/api/receivers/inventory \
  | python3 -m json.tool
```

Confirm that the four PI Scanner roles have the serials shown above and that no
scanner receiver is missing or duplicated. A shared Pi may list receivers owned
by other applications; those devices are outside the scope of this manual.

### 7.3 Verify the active analog worker map

The analog runtime configuration is separate from the inventory registry:

```text
/home/pi/PI-SCANNER/runtime/settings/analog_receivers.json
```

Check it through the application API:

```bash
curl -fsS http://127.0.0.1:8070/api/analog/status \
  | python3 -m json.tool
```

Confirm:

- `analog_2m.rtl_serial` is `00000144`.
- `analog_70cm.rtl_serial` is `00000440`.
- Both roles report `fft_scanning`, `locked`, or another healthy running state.

## 8. Application files and services

The validated Pi layout uses two application roots:

| Path | Purpose |
|---|---|
| `/home/pi/PI-P25-SCANNER` | Web UI, backend, P25 configuration, profiles, receiver registry, and audio arbitrator |
| `/home/pi/PI-SCANNER` | VHF/UHF workers, analog configuration, controls, status, and captured last-call diagnostics |

Runtime settings are intentionally not committed to Git. Back them up before
replacing an SD card or performing a major upgrade.

Main services:

| Service | Purpose |
|---|---|
| `pi-p25-scanner.service` | Web UI/API on port 8070 and OP25 process control |
| `pi-p25-raw-audio-bridge.service` | Three-source audio arbitrator and browser stream on port 8072 |
| `pi-p25-audio-pool.service` | Collects audio from P25 voice receivers |
| `pi-scanner-vhf-worker.service` | VHF FFT scanner using serial `00000144` |
| `pi-scanner-uhf-worker.service` | UHF FFT scanner using serial `00000440` |

Install or refresh the analog/audio runtime units from the analog root:

```bash
cd /home/pi/PI-SCANNER
sudo ./tools/install_audio_runtime_units.sh
```

## 9. Start PI Scanner and open the web application

Enable the core services at boot:

```bash
sudo systemctl enable --now pi-p25-scanner.service
sudo systemctl enable --now pi-p25-raw-audio-bridge.service
sudo systemctl enable --now pi-p25-audio-pool.service
sudo systemctl disable --now pi-scanner-vhf-worker.service
sudo systemctl disable --now pi-scanner-uhf-worker.service
```

Find the Pi address:

```bash
hostname -I
```

Open either of these from a device on the same network:

```text
http://PI-SDR.local:8070
http://<pi-ip-address>:8070
```

The current installation is normally available at
`http://192.168.68.137:8070`, but DHCP may change that address. A DHCP
reservation is recommended.

After boot, P25, VHF, and UHF scanning are all stopped. Press **Start Scanning +
Audio** once to start all three scanners together and connect that browser tab
to the audio stream. Browsers require a real tap or click before audio can
begin.

## 10. Use the Dashboard

### Top status indicators

- **Scanning:** the system is ready and looking for activity.
- **P25/VHF/UHF ON AIR:** the audio arbitrator is currently forwarding that
  source.
- **Online:** the browser can reach the backend.
- **Offline:** the backend is unreachable; check the Pi network and service.

### Main controls

- **Start Scanning + Audio:** starts P25, VHF, and UHF scanning together and
  connects low-latency browser audio.
- **Mute / Unmute:** mutes only this browser tab. It does not stop the scanners
  or mute another browser.
- **Volume:** controls only this browser tab. Moving it while muted also
  unmutes the tab.
- **Stop:** stops P25, VHF, and UHF scanning together and disconnects audio in
  this browser.

### Activity information

- **Active / Last Talkgroup:** current or most recently heard P25 talkgroup.
- **Voice:** current or last P25 voice frequency.
- **Control:** active P25 control-channel frequency.
- **Phase:** detected P25 phase when known.
- **Audio Arbitrator:** browser connection and active-source status.
- **Active Source:** P25, VHF, UHF, or None.
- **Voice Calls:** persistent count of distinct P25 voice transmissions.
- **Unique TGIDs:** number of unique P25 talkgroups observed in the current
  backend session.
- **VHF Locks / UHF Locks:** accepted analog carrier locks; these service
  counters restart when their worker restarts.
- **Muted:** encrypted or otherwise muted P25 events detected by the parser.

Open **Menu → Logs / Details** for recent activity, backend/OP25 log tail,
active configuration, validated command, and launch-readiness information.

## 11. Use Skip, Block, Clear Lock, and Clear Blocks

These buttons become available only while VHF or UHF has a current identified
lock.

- **Skip 10 Min:** releases the current analog channel and makes it ineligible
  for 600 seconds.
- **Block Channel:** releases the current analog channel and blocks it until
  blocks are cleared.
- **Clear Lock:** releases the current carrier immediately without creating a
  skip or block.
- **Clear Blocks:** removes all temporary skips and persistent analog blocks
  across both VHF and UHF.

Use **Skip 10 Min** for intermittent noise and **Block Channel** for a channel
that repeatedly opens on interference. Use **Clear Lock** when a carrier is
stuck but should remain eligible for future calls.

Control state is stored atomically at:

```text
/home/pi/PI-SCANNER/runtime/settings/analog_controls.json
```

## 12. Manage radio profiles

Open **Menu → Radio Setup**.

A named profile contains P25 systems/talkgroups and a snapshot of the analog
VHF/UHF lists. Receiver serial assignments are hardware settings and are not
changed when a profile is loaded.

### Save the current setup

1. Enter a name in **New Profile Name**.
2. Press **Save Current**.
3. Press **Refresh Profiles** if the new name does not appear immediately.

### Load a profile

1. Select it under **Saved Profile**.
2. Press **Load Selected Profile**.
3. For a P25 system change, press **Stop**, load the profile, then press
   **Start Scanning + Audio** so OP25 regenerates and uses the selected system.

Analog workers re-read their channel file between FFT sweeps, so updated analog
lists normally become active without restarting the workers.

### Delete a profile

Select it and press **Delete Selected**. Deleting a saved profile does not by
itself erase the currently active runtime configuration.

### Name a profile during upload

Enter **New Profile Name** before choosing or uploading the CSV. If the field
is blank, PI Scanner converts the CSV filename into the profile name.

## 13. Import and export analog CHIRP CSV files

Use the **Analog VHF / UHF** card on the Radio Setup screen.

1. Press **Download Template**.
2. Edit the file in CHIRP, Excel, LibreOffice, or a text editor without changing
   the header names.
3. Enter the desired **New Profile Name**.
4. Choose the CSV file.
5. Press **Upload & Save Profile**.
6. Confirm the displayed VHF/UHF channel counts.

Required CHIRP columns are `Location`, `Name`, `Frequency`, and `Mode`. The
downloaded template includes the full standard CHIRP column set.

Import behavior:

- 136–174 MHz channels are assigned to VHF / `analog_2m` / serial `00000144`.
- 400–520 MHz channels are assigned to UHF / `analog_70cm` / serial `00000440`.
- `FM` and `NFM` are accepted and normalized for the NFM scanner path.
- `Skip` values `S` or `L` import the row as disabled.
- CHIRP `Tone` by itself is transmit-only and does not enable receive gating.
- `TSQL`, `TSQL-R`, or a compatible cross mode uses `cToneFreq` as a receive
  CTCSS gate.
- A file containing only one band replaces only that band and preserves the
  other active band.

To back up a named analog profile, select it and press **Export Selected**.

## 14. Import and export P25 CSV files

Use the **P25 Systems & Talkgroups** card on the Radio Setup screen.

1. Press **Download Template**.
2. Enter one row per control frequency, optional voice frequency, or talkgroup.
3. Enter a **New Profile Name**.
4. Choose the CSV and press **Upload & Save Profile**.
5. Stop and restart P25 scanning after applying a different system profile.

Important columns:

| Column | Use |
|---|---|
| `RecordType` | `control`, `voice`, or `talkgroup` |
| `System` | System name; required on every populated row |
| `Site` | Site name shown in configuration |
| `FrequencyMHz` | Required for control and voice rows |
| `TGID` | Required for talkgroup rows |
| `Name` | Talkgroup label |
| `Enabled` | `true` or `false` |
| `Priority` | Optional value from 0 through 100 |
| `NAC` | Optional P25 network access code |
| `Modulation` | Usually `CQPSK` for the validated system |

Every imported system must have at least one enabled control-channel row.
Talkgroup IDs must be unique within the system and range from 0 through 65535.
PI Scanner does not decode encrypted traffic even if an encrypted talkgroup is
present in a CSV.

To download a selected named P25 profile, press **Export Selected** in the P25
card.

## 15. Understand the audio arbitrator

Audio arrives on three loopback UDP ports:

| Source | UDP port |
|---|---:|
| P25 | 23456 |
| VHF | 23458 |
| UHF | 23459 |

The first source that produces enough valid frames becomes active. Other
sources are rejected while that call owns the output. After approximately 1.5
seconds without frames, the arbitrator releases the source and can accept the
next P25, VHF, or UHF call.

The browser receives 8 kHz, mono, signed 16-bit PCM from port 8072. The browser
keeps only a small queue to limit delay. Mute and volume operate in the browser,
not in the radio workers.

Check audio status directly:

```bash
curl -fsS http://127.0.0.1:8072/api/audio/status \
  | python3 -m json.tool
```

## 16. Routine maintenance and backups

### Check service health

```bash
systemctl --no-pager --full status pi-p25-scanner.service
systemctl --no-pager --full status pi-p25-raw-audio-bridge.service
systemctl --no-pager --full status pi-p25-audio-pool.service
systemctl --no-pager --full status pi-scanner-vhf-worker.service
systemctl --no-pager --full status pi-scanner-uhf-worker.service
```

### Back up operator data

At minimum, preserve:

```text
/home/pi/PI-P25-SCANNER/runtime/settings/
/home/pi/PI-SCANNER/runtime/settings/
```

Example:

```bash
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "/home/pi/scanner-backups/$stamp"
cp -a /home/pi/PI-P25-SCANNER/runtime/settings \
  "/home/pi/scanner-backups/$stamp/p25-settings"
cp -a /home/pi/PI-SCANNER/runtime/settings \
  "/home/pi/scanner-backups/$stamp/analog-settings"
```

Named profiles are under the P25 runtime settings directory. Analog channel
lists, skips, and blocks are under the analog runtime settings directory.

### Restart after maintenance

```bash
sudo systemctl restart pi-p25-raw-audio-bridge.service
sudo systemctl restart pi-p25-audio-pool.service
sudo systemctl restart pi-scanner-vhf-worker.service
sudo systemctl restart pi-scanner-uhf-worker.service
sudo systemctl restart pi-p25-scanner.service
```

Worker and backend counters may restart, except the persistent P25 **Voice
Calls** total.

## 17. Troubleshooting

### `usb_claim_interface error -6`

Another process already owns that receiver. This is expected if a scanner
service is running. Do not reinstall drivers first.

1. Identify ownership:

   ```bash
   ps -ef | grep -E 'rtl_tcp|rtl_fm|rx.py|multi_rx|readsb|dump978'
   ```

2. Stop the service for that receiver.
3. Run the bounded hardware test again.
4. Restart the service when finished.

### A PI Scanner receiver is missing

- Check the powered hub and Pi power supply.
- Reseat one receiver at a time.
- Run `lsusb` and `rtl_test -t` with receiver-owning services stopped.
- Check for duplicate or blank serials with `rtl_eeprom -d <temporary-index>`.
- Do not solve a missing device by changing persistent roles to current indexes.

### The wrong receiver is scanning VHF or UHF

Check both the inventory registry and analog runtime status. VHF must be
`00000144`; UHF must be `00000440`. Reapply the canonical role map and correct
`analog_receivers.json` before restarting the workers.

### Dashboard says Offline

```bash
systemctl is-active pi-p25-scanner.service
curl -fsS http://127.0.0.1:8070/api/status | python3 -m json.tool
journalctl -u pi-p25-scanner.service -n 100 --no-pager
```

### No browser audio

- Tap **Start Scanning + Audio**; browser autoplay requires a user gesture.
- Confirm the button says **Mute**, not **Unmute**, and raise the volume.
- Check whether **Active Source** shows P25, VHF, or UHF.
- Confirm port 8072 is reachable and the arbitrator service is active.
- Reload the page after restarting the audio service.

### VHF or UHF never locks

- Confirm the frequency is enabled in the active analog profile.
- Confirm the correct band assignment and serial.
- Check antenna, feed line, filters, and receiver gain.
- Inspect status and recent worker logs:

  ```bash
  curl -fsS http://127.0.0.1:8070/api/analog/status | python3 -m json.tool
  journalctl -u pi-scanner-vhf-worker.service -n 100 --no-pager
  journalctl -u pi-scanner-uhf-worker.service -n 100 --no-pager
  ```

### A noisy analog channel repeatedly opens

Use **Skip 10 Min** while evaluating it. Use **Block Channel** if it should
remain excluded. **Clear Blocks** restores every skipped and blocked analog
frequency.

### P25 does not lock or follow calls

- Confirm P25 control serial `00000251` and voice serial `00000252` are present.
- Confirm the selected system contains the correct current control channels.
- Check **Logs / Details** for launch readiness and OP25 messages.
- Inspect `runtime/settings/op25_validated_rx_command.env` and the generated
  files under `runtime/op25/`.
- Run the bounded project probes before changing OP25 command arguments.

### CSV upload fails

- Start from the downloadable template.
- Preserve exact header spelling.
- Save as UTF-8 CSV.
- Remove formulas, merged cells, and extra title rows.
- Confirm frequencies are in MHz, not Hz.
- For P25, include one enabled control row per system.

## 18. Technical reference

### Network ports

| Port | Protocol | Purpose |
|---:|---|---|
| 8070 | TCP/HTTP | PI Scanner web UI and backend API |
| 8072 | TCP/HTTP | Audio status and browser PCM/WAV stream |
| 23456 | UDP loopback | P25 audio into the arbitrator |
| 23458 | UDP loopback | VHF audio into the arbitrator |
| 23459 | UDP loopback | UHF audio into the arbitrator |
| 23500–23509 | UDP loopback | P25 multi-receiver audio pool inputs |

### Important runtime files

| File | Purpose |
|---|---|
| `/home/pi/PI-P25-SCANNER/runtime/settings/p25_systems.json` | Active P25 system and talkgroups |
| `/home/pi/PI-P25-SCANNER/runtime/settings/receiver_roles.json` | Stable PI Scanner receiver registry |
| `/home/pi/PI-P25-SCANNER/runtime/settings/configs/` | Named radio profiles |
| `/home/pi/PI-P25-SCANNER/runtime/settings/runtime_activity.json` | Persistent P25 Voice Calls total |
| `/home/pi/PI-SCANNER/runtime/settings/analog_receivers.json` | Active VHF/UHF channels and worker settings |
| `/home/pi/PI-SCANNER/runtime/settings/analog_controls.json` | Analog skips, blocks, and clear-lock requests |
| `/home/pi/PI-SCANNER/runtime/status/analog_2m.json` | Current VHF worker status |
| `/home/pi/PI-SCANNER/runtime/status/analog_70cm.json` | Current UHF worker status |
| `/home/pi/PI-SCANNER/runtime/status/vhf_last_call.wav` | Most recent accepted VHF browser-audio capture |
| `/home/pi/PI-SCANNER/runtime/status/uhf_last_call.wav` | Most recent accepted UHF browser-audio capture |

### Useful API endpoints

```text
GET  /api/status
GET  /api/activity
GET  /api/analog/status
GET  /api/analog/channels
GET  /api/analog/controls
GET  /api/receivers/inventory
GET  /api/config
GET  /api/config/named
POST /api/scanner/start
POST /api/scanner/stop
```

## 19. Acceptance checklist

Use this checklist after initial setup, a power cycle, or a major update:

- [ ] Pi boots without undervoltage or USB power warnings.
- [ ] Four unique PI Scanner RTL-SDR serials are visible.
- [ ] P25 control is `00000251`; P25 voice is `00000252`.
- [ ] VHF is `00000144`; UHF is `00000440`.
- [ ] Receiver inventory reports no missing, duplicate, or unassigned serials.
- [ ] Backend, audio bridge, audio pool, VHF, and UHF services are active.
- [ ] Port 8070 loads the dashboard and shows **Online**.
- [ ] Port 8072 reports `"ok": true`.
- [ ] VHF and UHF report healthy FFT-scanning states and nonzero channel counts.
- [ ] **Start Scanning + Audio** starts P25, VHF, and UHF and connects browser audio.
- [ ] A known P25 call updates talkgroup information and Voice Calls.
- [ ] A known VHF transmission produces a VHF lock and complete audio.
- [ ] A known UHF transmission produces a UHF lock and complete audio.
- [ ] Skip, Block, Clear Lock, and Clear Blocks behave as expected.
- [ ] A named profile can be saved, exported, loaded, and restored.

When every applicable item passes, the scanner is ready for normal operation.

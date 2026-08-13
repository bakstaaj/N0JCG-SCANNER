# N0JCG Scanner user manual

| Metadata | Value |
|---|---|
| Product | N0JCG Scanner |
| Slug | scanner-user-guide |
| Type | User guide |
| Version | 4.2.0 |
| Status | Current |
| Last updated | 2026-08-11 |
| Audience | Scanner operators and installers |
| Prerequisites | N0JCG Scanner hardware and network access |
| Estimated time | 45 minutes for setup; 5 minutes for daily operation |
| Related | [Product page](https://www.n0jcg.com/products/scanner/), [Hardware Guide](HARDWARE_GUIDE.md) |
| Owner | N0JCG |

This manual covers the N0JCG Scanner v4.2.0 production layout: P25 trunked radio,
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

PI Scanner combines three scanning paths, using four dedicated RTL-SDR
receivers, in one touch-friendly web application:

- **P25:** follows permitted, clear P25 talkgroups using dedicated control and
  voice RTL-SDR receivers. The production P25 assignment is configured through
  the role template; the launch path fails closed rather than silently
  reverting to a single-radio decoder.
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
| P25 control | `<P25_CONTROL_SERIAL>` | Remains on the trunked-system control channel |
| P25 voice | `<P25_VOICE_SERIAL>` | Follows P25 voice-channel grants |
| VHF / analog 2 m | `<VHF_SERIAL>` | FFT-directed VHF NFM scanner |
| UHF / analog 70 cm | `<UHF_SERIAL>` | FFT-directed UHF NFM scanner |

Keep the two analog assignments distinct and do not swap them. The VHF and UHF
workers fail closed if their
runtime serial or audio port is wrong.

The P25 path supports a fixed-center wideband voice receiver. When enabled,
OP25 selects configured voice channels digitally within the sampled bandwidth
without a slow RTL hardware retune for every grant. Keep system frequencies,
sample rates, demodulator types, and gain values in the ignored station runtime
configuration.

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
cd /home/pi/n0jcg-scanner
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
sudo rtl_eeprom -d 0 -s <VHF_SERIAL>
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
cd /home/pi/n0jcg-scanner
./tools/pi5_apply_receiver_serial_map.sh --dry-run
```

Apply it after verifying the table:

```bash
./tools/pi5_apply_receiver_serial_map.sh --apply --yes
```

The tool validates uniqueness, backs up an existing registry, and writes:

```text
/home/pi/n0jcg-scanner/runtime/settings/receiver_roles.json
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

### 7.3 Verify the analog worker map

The analog runtime configuration is separate from the inventory registry:

```text
/home/pi/n0jcg-scanner/runtime/settings/analog_receivers.json
```

Check it through the application API:

```bash
curl -fsS http://127.0.0.1:8070/api/analog/status \
  | python3 -m json.tool
```

Confirm:

- `analog_2m.rtl_serial` matches `<VHF_SERIAL>`.
- `analog_70cm.rtl_serial` matches `<UHF_SERIAL>`.
- Before **Start Scanning + Audio** is pressed, both workers are expected to be
  stopped.
- After **Start Scanning + Audio** is pressed, both roles report
  `fft_scanning`, `locked`, or another healthy running state.

## 8. Application files and services

The validated Pi layout uses one application root:

| Path | Purpose |
|---|---|
| `/home/pi/n0jcg-scanner` | Complete web UI, backend, P25 and analog configuration, profiles, receiver registry, workers, audio arbitrator, controls, and runtime diagnostics |

Runtime settings are intentionally not committed to Git. Back them up before
replacing an SD card or performing a major upgrade.

Main services:

| Service | Purpose |
|---|---|
| `pi-p25-scanner.service` | Boot-enabled web UI/API on port 8070 and coordinated scanner control |
| `pi-p25-raw-audio-bridge.service` | Boot-enabled three-source audio arbitrator and browser stream on port 8072 |
| `pi-p25-audio-pool.service` | Boot-enabled collector for P25 voice-receiver audio |
| `pi-scanner-vhf-worker.service` | On-demand VHF FFT scanner using `<VHF_SERIAL>`; started and stopped by the dashboard |
| `pi-scanner-uhf-worker.service` | On-demand UHF FFT scanner using `<UHF_SERIAL>`; started and stopped by the dashboard |

Install or refresh the complete audio/runtime units from the application root:

```bash
cd /home/pi/n0jcg-scanner
sudo ./tools/install_audio_runtime_units.sh
```

## 9. Start PI Scanner and open the web application

The radio Pi at `<RADIO_HOST>` runs the complete scanner application: web UI,
backend, audio infrastructure, OP25, and analog workers. The N0JCG ROC at
`<ROC_HOST>:8095` remains a separate platform dashboard and provides a direct
link to the Pi scanner on port `8070`; it does not host or proxy scanner files.

On the radio Pi, enable the radio API and audio infrastructure at boot while
keeping the receiver workers out of the boot target:

```bash
sudo systemctl enable --now pi-p25-scanner.service
sudo systemctl enable --now pi-p25-raw-audio-bridge.service
sudo systemctl enable --now pi-p25-audio-pool.service
sudo systemctl disable --now pi-scanner-vhf-worker.service
sudo systemctl disable --now pi-scanner-uhf-worker.service
```

On the ROC, the existing `n0jcg-roc.service` owns only the dashboard. It does
not host or proxy scanner assets.

Open this address from a device on the same network:

```text
http://<RADIO_HOST>:8070/
```

The ROC and radio Pi should both have DHCP reservations. The direct Pi URL is
the normal scanner operator URL.

For the separate compact phone dashboard, open:

```text
http://<RADIO_HOST>:8070/mobile.html
```

Supported phone browsers are redirected to this page automatically when they
open the main dashboard address. The **Full UI** link bypasses that redirect
for the current navigation when radio setup or logs are needed.

The phone dashboard provides the coordinated Start and Stop controls, local
Mute and Volume, current P25 or analog activity, Voice/VHF/UHF counters, and
analog Skip, Block, Clear Lock, and Clear Blocks. Use **Full UI** when profile
editing, logs, or detailed radio setup is required.

After boot, the dashboard and audio infrastructure are available, but P25, VHF,
and UHF scanning are all stopped. Opening the web page or the desktop shortcut
does not start a receiver. Press **Start Scanning + Audio** once to start all
three scanners together and connect that browser tab to the audio stream.
Browsers require a real tap or click before audio can begin.

When another browser has already started the scanners, the new browser shows
an enabled **Listen** button. Pressing **Listen** attaches only that browser to
the PCM fanout and does not restart P25, VHF, or UHF.

If any one of the three scanners cannot start, the coordinated start is treated
as failed and the application returns the other scanners to the stopped state.

## 10. Use the Dashboard

### Top status indicators

- **Scanning:** the system is ready and looking for activity.
- **P25/VHF/UHF ON AIR:** the audio arbitrator is currently forwarding that
  source.
- **Connected:** the browser can reach the Pi scanner API and audio services.
- **Offline:** check the Pi scanner service and the network path to the radio
  Pi. The ROC dashboard is not required for direct Pi operation.

### Main controls

- **Start Scanning + Audio:** starts P25, VHF, and UHF scanning together and
  connects low-latency browser audio.
- **Listen:** appears when the scanners are already running but this browser is
  not attached; it connects this tab without restarting radio services.
- **Mute / Unmute:** mutes only this browser tab. It does not stop the scanners
  or mute another browser.
- **Volume:** controls only this browser tab. Moving it while muted also
  unmutes the tab.
- **Stop:** stops P25, VHF, and UHF scanning together, resets **Voice Calls**,
  **VHF Locks**, and **UHF Locks** to zero, and disconnects audio in this
  browser.

After pressing **Stop**, the dashboard and audio services remain online so a
later press of **Start Scanning + Audio** can resume reception without rebooting
the Pi.

### Activity information

- **Active / Last Talkgroup:** current or most recently heard P25 talkgroup.
- **Voice:** current or last P25 voice frequency.
- **Control:** active P25 control-channel frequency.
- **Phase:** detected P25 phase when known.
- **Audio Arbitrator:** browser connection and active-source status.
- **Active Source:** P25, VHF, UHF, or None.
- **Voice Calls:** count of distinct P25 voice transmissions since the last
  press of **Stop**.
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
/home/pi/n0jcg-scanner/runtime/settings/analog_controls.json
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

When scanning is running, the analog workers re-read their channel file between
FFT sweeps. When scanning is stopped, an updated analog list becomes active the
next time **Start Scanning + Audio** is pressed.

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

- 136–174 MHz channels are assigned to VHF / `analog_2m` / `<VHF_SERIAL>`.
- 400–520 MHz channels are assigned to UHF / `analog_70cm` / `<UHF_SERIAL>`.
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
5. Press **Stop**, apply the different system profile, and then press **Start
   Scanning + Audio**. The coordinated controls restart P25, VHF, and UHF
   together.

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
| `Modulation` | System modulation metadata; retain the value supplied by the system profile |
| `ControlDemod` | **Required.** OP25 control-channel demodulator. Use `fsk4` for Colorado DTRS C4FM control channels. |

Every imported system must have at least one enabled control-channel row.
Every P25 profile must define `control_demod_type`; this value controls the
control-channel demodulator used by OP25. For Colorado DTRS, set
`control_demod_type` to `fsk4`. Do not rely on the legacy runtime marker or an
environment override to select the control demodulator.
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

Each connected browser receives its own copy of the 8 kHz, mono, signed 16-bit
PCM stream from port 8072. Browsers do not compete for audio frames. Each tab
keeps only a small queue to limit delay; mute and volume operate in that browser,
not in the radio workers or other tabs.

The arbitrator maintains a continuous 20 ms browser stream even through short
source-packet gaps. A short three-frame server prebuffer and approximately 60 ms
browser jitter buffer reduce clipped call beginnings while absorbing normal
network and browser scheduling variation.

P25 audio is forwarded from the first valid decoded frame after each OP25 call
boundary. The audio pool does not discard opening frames; its RMS validation,
single-source ownership, and DRAIN/DROP boundary handling remain active.

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

Interpret the results according to the dashboard state:

- Before **Start Scanning + Audio**, the backend, audio bridge, and audio pool
  should be active; the VHF and UHF workers should be inactive.
- While scanning, all five services should be active and `/api/status` should
  report P25 `running`, VHF `active`, and UHF `active` under
  `coordinated_scanners`.
- After **Stop**, the VHF and UHF workers should again be inactive while the
  three core services remain active.

### Back up operator data

At minimum, preserve:

```text
/home/pi/n0jcg-scanner/runtime/settings/
```

Example:

```bash
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "/home/pi/n0jcg-scanner-backups/$stamp"
cp -a /home/pi/n0jcg-scanner/runtime/settings \
  "/home/pi/n0jcg-scanner-backups/$stamp/settings"
```

Named profiles, P25 settings, analog channel lists, skips, and blocks are all
under the application runtime settings directory.

### Restart after maintenance

First press **Stop** in the dashboard. If the dashboard is unavailable, stop
the analog workers directly before maintenance:

```bash
sudo systemctl stop pi-scanner-vhf-worker.service
sudo systemctl stop pi-scanner-uhf-worker.service
```

Restart only the boot-enabled application infrastructure:

```bash
sudo systemctl restart pi-p25-raw-audio-bridge.service
sudo systemctl restart pi-p25-audio-pool.service
sudo systemctl restart pi-p25-scanner.service
```

Leave VHF and UHF stopped. Open the dashboard and press **Start Scanning +
Audio** when reception should resume. Worker and backend counters may restart,
except the persistent P25 **Voice Calls** total.

## 17. Troubleshooting

### `usb_claim_interface error -6`

Another process already owns that receiver. This is expected if a scanner
service is running. Do not reinstall drivers first.

1. Identify ownership:

   ```bash
   ps -ef | grep -E 'rtl_tcp|rtl_fm|rx.py|multi_rx|readsb|dump978'
   ```

2. Press **Stop** so P25, VHF, and UHF release their receivers together. If the
   dashboard is unavailable, stop both analog worker services directly.
3. Run the bounded hardware test again.
4. Press **Start Scanning + Audio** when finished.

### A PI Scanner receiver is missing

- Check the powered hub and Pi power supply.
- Reseat one receiver at a time.
- Run `lsusb` and `rtl_test -t` with receiver-owning services stopped.
- Check for duplicate or blank serials with `rtl_eeprom -d <temporary-index>`.
- Do not solve a missing device by changing persistent roles to current indexes.

### The wrong receiver is scanning VHF or UHF

Check both the inventory registry and analog runtime status. VHF must be
the configured VHF serial; UHF must use its configured UHF serial. Reapply the
station role map and correct
`analog_receivers.json`, then use **Stop** followed by **Start Scanning +
Audio**. Do not enable either analog worker for boot.

### Dashboard says Offline

```bash
systemctl is-active pi-p25-scanner.service
curl -fsS http://127.0.0.1:8070/api/status | python3 -m json.tool
journalctl -u pi-p25-scanner.service -n 100 --no-pager
```

### No browser audio

- Tap **Start Scanning + Audio**; browser autoplay requires a user gesture.
- Confirm `/api/status` reports P25 `running`, VHF `active`, and UHF `active`
  under `coordinated_scanners`.
- Confirm the button says **Mute**, not **Unmute**, and raise the volume.
- Check whether **Active Source** shows P25, VHF, or UHF.
- Confirm port 8072 is reachable and the arbitrator service is active.
- Reload the page after restarting the audio service.

### VHF or UHF never locks

- Confirm **Start Scanning + Audio** has been pressed and the applicable worker
  service is active.
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

- Confirm the configured P25 control and voice serials are present.
- Confirm the selected system contains the correct current control channels.
- Check **Logs / Details** for launch readiness and OP25 messages.
- Inspect `runtime/settings/op25_validated_rx_command.env` and the generated
  files under `runtime/op25/`.
- Confirm the private station command markers match its validated values:

  ```text
  P25_CONTROL_GAIN=<VALIDATED_CONTROL_GAIN>
  P25_VOICE_GAIN=<VALIDATED_VOICE_GAIN>
  P25_CONTROL_DEMOD_TYPE=<VALIDATED_CONTROL_DEMOD>
  P25_VOICE_DEMOD_TYPE=<VALIDATED_VOICE_DEMOD>
  P25_VOICE_SAMPLE_RATE=<VALIDATED_SAMPLE_RATE>
  P25_VOICE_CENTER_HZ=<VALIDATED_CENTER_HZ>
  ```

- Use `tools/p25_terminal_diagnostic.py` to capture receiver frequency error
  and `tools/p25_terminal_plot_snapshot.py` for bounded spectrum and
  constellation evidence. Do not leave diagnostic plots enabled during normal
  operation.
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
| `/home/pi/n0jcg-scanner/runtime/settings/p25_systems.json` | Active P25 system and talkgroups |
| `/home/pi/n0jcg-scanner/runtime/settings/receiver_roles.json` | Stable PI Scanner receiver registry |
| `/home/pi/n0jcg-scanner/runtime/settings/configs/` | Named radio profiles |
| `/home/pi/n0jcg-scanner/runtime/settings/runtime_activity.json` | Persistent P25 Voice Calls total |
| `/home/pi/n0jcg-scanner/runtime/settings/analog_receivers.json` | Active VHF/UHF channels and worker settings |
| `/home/pi/n0jcg-scanner/runtime/settings/analog_controls.json` | Analog skips, blocks, and clear-lock requests |
| `/home/pi/n0jcg-scanner/runtime/status/analog_2m.json` | Current VHF worker status |
| `/home/pi/n0jcg-scanner/runtime/status/analog_70cm.json` | Current UHF worker status |
| `/home/pi/n0jcg-scanner/runtime/status/vhf_last_call.wav` | Most recent accepted VHF browser-audio capture |
| `/home/pi/n0jcg-scanner/runtime/status/uhf_last_call.wav` | Most recent accepted UHF browser-audio capture |

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

`POST /api/scanner/start` is the coordinated Start action for P25, VHF, and
UHF. `POST /api/scanner/stop` is the coordinated Stop action. The
`GET /api/status` response includes `coordinated_scanners`, which reports the
last known P25, VHF, and UHF state.

## Registration and five-minute trial

The status bar shows **Registered**, **Unregistered**, or a live **Trial**
countdown. An unregistered scanner operates for five minutes after scanning is
started, then automatically stops P25, VHF, and UHF. To register, open
**Menu → Registration**, enter the license S/N supplied by N0JCG and the
purchaser email, then select **Activate license**. The scanner validates over
HTTPS and keeps a signed offline lease. A registered scanner can remain offline
for up to seven days after its last successful validation.

## 19. Acceptance checklist

Use this checklist after initial setup, a power cycle, or a major update:

- [ ] Pi boots without undervoltage or USB power warnings.
- [ ] Four unique PI Scanner RTL-SDR serials are visible.
- [ ] P25 control and P25 voice match the private station role map.
- [ ] VHF and UHF match the private station role map.
- [ ] Receiver inventory reports no missing, duplicate, or unassigned serials.
- [ ] Immediately after boot, the backend, audio bridge, and audio pool are
      active, while P25, VHF, and UHF scanning are stopped.
- [ ] VHF and UHF worker services are not enabled for boot.
- [ ] ROC `<ROC_HOST>:8095/` shows an **Open Scanner** link to
      `http://<RADIO_HOST>:8070/`.
- [ ] ROC root `<ROC_HOST>:8095/` still loads the main dashboard.
- [ ] Radio Pi `<RADIO_HOST>:8070/api/status` returns the scanner state.
- [ ] Radio host `<RADIO_HOST>:8072/api/audio/status` reports `"ok": true`.
- [ ] `./tools/validate_split_runtime.sh` reports `FINAL=PASS` when the ROC
      dashboard is available; direct Pi operation remains valid without ROC.
- [ ] **Start Scanning + Audio** starts P25, VHF, and UHF and connects browser
      audio.
- [ ] While scanning, `/api/status` reports P25 `running`, VHF `active`, and UHF
      `active` under `coordinated_scanners`.
- [ ] VHF and UHF report healthy FFT-scanning states and nonzero channel counts.
- [ ] A known P25 call updates talkgroup information and Voice Calls.
- [ ] A known VHF transmission produces a VHF lock and complete audio.
- [ ] A known UHF transmission produces a UHF lock and complete audio.
- [ ] Skip, Block, Clear Lock, and Clear Blocks behave as expected.
- [ ] **Stop** stops P25, VHF, and UHF while leaving the dashboard online.
- [ ] A named profile can be saved, exported, loaded, and restored.

When every applicable item passes, the scanner is ready for normal operation.

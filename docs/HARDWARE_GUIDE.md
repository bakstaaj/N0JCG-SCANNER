# N0JCG Scanner hardware guide

| Metadata | Value |
|---|---|
| Product | N0JCG Scanner |
| Slug | scanner-hardware-guide |
| Type | Hardware guide |
| Version | 3.0.0 |
| Status | Preview |
| Last updated | 2026-08-07 |
| Audience | Installers and station maintainers |
| Prerequisites | Basic RF safety and Linux command-line skills |
| Estimated time | 20 minutes |
| Related | [User Guide](USER_MANUAL.md), [Administrator Guide](ADMINISTRATOR_GUIDE.md) |
| Owner | N0JCG |

## Supported topology

The validated design uses four individually serialized RTL-SDR receivers: P25
control, P25 voice, VHF analog, and UHF analog. A smaller installation may omit
a scanning path only when its configuration and services are disabled cleanly.

Use a powered USB hub sized for the receivers and the host. Label every dongle,
USB lead, and antenna connection. Choose band-appropriate antennas, filters,
and splitters; an unpowered passive split can reduce signal level substantially.

## Receiver identity

Linux indexes can change after reboot or reconnection. Program a unique EEPROM
serial into each receiver and store the station's role-to-serial map under the
ignored `runtime/` directory. Public examples use placeholders:

| Role | Configuration value |
|---|---|
| P25 control | `<P25_CONTROL_SERIAL>` |
| P25 voice | `<P25_VOICE_SERIAL>` |
| VHF analog | `<VHF_SERIAL>` |
| UHF analog | `<UHF_SERIAL>` |

Connect only one receiver while programming its EEPROM. Stop all scanner
services first, write the serial with `rtl_eeprom`, reconnect the device, and
confirm the new identity before proceeding to the next receiver.

## RF and electrical safety

- The receiver inputs are receive-only; never connect transmitter power.
- Nearby transmissions can overload or damage an SDR. Use separation,
  attenuation, and band filtering for test transmissions.
- Provide airflow around the host, hub, and receivers.
- Do not hot-plug marginal hubs during active service; stop services first.
- A USB ownership error is a service/process conflict until proven otherwise.

## Commissioning and verification

1. Confirm the DVB kernel driver is not claiming the receivers.
2. Enumerate all devices by EEPROM serial with the provided role probe.
3. Start one service at a time and verify it claims only its configured serial.
4. Validate P25 control lock, then a clear voice call.
5. Validate VHF and UHF with lawful, brief test transmissions on configured
   channels or with known local activity.
6. Run all scanners together and verify continuous browser audio.

If a receiver fails, stop its owning service, exchange only that labeled device,
program the replacement serial, and repeat commissioning. Do not compensate for
a hardware or antenna fault by blindly increasing software gain or squelch.

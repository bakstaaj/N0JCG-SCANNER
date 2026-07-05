# OP25 Backend Live Launch

V0.2A allows the backend `/api/scanner/start` endpoint to launch OP25 only
from the validated command marker produced by the bounded live command probe.

## Required evidence marker

The backend consumes:

```text
runtime/settings/op25_validated_rx_command.env
```

That marker is generated only after:

```bash
./tools/pi5_p25_op25_live_command_probe.sh --rx-smoke --seconds 20 --yes
```

passes on the Raspberry Pi.

## Backend validation

Run this on the Raspberry Pi from the repository root:

```bash
./tools/pi5_p25_backend_live_launch_probe.sh
```

The probe starts the backend on loopback, calls `/api/scanner/start`, confirms
the decoder process remains running briefly, calls `/api/scanner/stop`, and then
exits. It does not install packages or create a service.

## Guardrails

- The backend must not invent OP25 command lines.
- Missing marker means `/api/scanner/start` remains config-generation only.
- Invalid marker returns a controlled API error instead of launching.
- Encrypted calls remain mute/log/skip only; no key loading or decryption path is added.

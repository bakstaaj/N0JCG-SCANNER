# V0.3A Scanner Control Dashboard

V0.3A moves the project from bring-up diagnostics into application-facing scanner controls.

## Scope

- Keep the existing backend on port `8070`.
- Preserve guarded OP25 launch through `runtime/settings/op25_validated_rx_command.env`.
- Add a dashboard summary above the detailed status grid.
- Keep Start / Stop scanner controls visible and driven by backend status.
- Show scanner state, decoder process state, control frequency, command source, and latest warning/event.
- Detect the OP25 HTTP terminal port from the active command or validated marker.
- Provide a backend-proxied OP25 UI link at `/op25/` so a browser can open OP25 through the scanner backend host.

## Notes

The OP25 process currently listens on localhost on the Pi, so the application exposes a minimal same-host proxy rather than asking the browser to connect to `127.0.0.1` on the workstation. The proxy is for the OP25 UI only and does not change scanner start/stop semantics.

Encrypted calls remain out of scope for decoding. The application should only detect, mute, log, or skip encrypted traffic.

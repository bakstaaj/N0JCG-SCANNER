# OP25 HTTP Runtime Probe

`tools/pi5_p25_op25_http_runtime_probe.sh` is a short live diagnostic for the OP25 terminal/listener path.

It is intentionally separate from the longer interface-discovery probe. It runs for a short bounded window, starts the scanner through the backend when needed, extracts OP25 HTTP ports from the start response, status samples, and validated command marker, and checks whether those ports appear in TCP listener snapshots or answer localhost HTTP probes.

The normal live command is:

```bash
./tools/pi5_p25_op25_http_runtime_probe.sh --seconds 30 --interval 1 --yes
```

The expected OP25 terminal port from the current validated command is `18091` because the backend launches OP25 with `-l http:127.0.0.1:18091`.

If the report shows a running start response but port `18091` never appears as a TCP listener, the next fix should focus on the OP25 launch mode or terminal-server arguments rather than adding more backend parsers.

## Backend JSON response handling

The HTTP runtime probe reads full backend `/api/status`, `/api/scanner/start`, and `/api/scanner/stop` JSON bodies before parsing. OP25 endpoint probes may still store bounded body samples in the human-readable report, while the generated JSON artifact preserves the structured probe results.

## V0.2Y backend readiness wait

When the backend service has just been restarted, the runtime probe waits for `/api/status` to become reachable before deciding whether to start the scanner. This prevents a transient connection-refused response during service startup from being misclassified as `--no-start` or already-running behavior.

Useful options:

- `--backend-ready-seconds N` controls how long to wait for `/api/status` before the first start decision. The default is 15 seconds.
- `--backend-ready-interval N` controls the polling interval during that readiness wait. The default is 1 second.

# OP25 Interface Discovery

V0.2O adds a Pi-side discovery probe for OP25 metadata/interface paths.

The earlier V0.2N evidence showed that the current backend stdout/stderr stream exposes control lock and plaintext IMBE voice frames, but does not expose active TGID or voice-frequency metadata. This probe collects evidence about possible alternate metadata sources without guessing parser behavior.

The probe collects:

- backend `/api/status` snapshots while the validated scanner runs
- localhost TCP listeners from `ss` or `netstat`
- HTTP responses from common OP25/terminal/status ports
- OP25 app/source files found from the running command and common install paths
- source-token hits for `http`, `terminal`, `json`, `tgid`, `talkgroup`, `frequency`, `voice`, `grant`, `trunk`, `metadata`, `plot`, and `zmq`

Run:

```bash
./tools/pi5_p25_op25_interface_discovery_probe.sh --self-test
./tools/pi5_p25_op25_interface_discovery_probe.sh --seconds 240 --interval 2 --yes
```

Reports are written under `.p25_op25_interface_discovery_reports/`.
## Status capture behavior

The interface discovery probe must preserve the initial `/api/status` snapshot before it starts the scanner. If scanner startup temporarily prevents additional status polling, that condition is diagnostic evidence and should be reported as WARN, not as a hard failure, as long as the backend was initially reachable and the probe can still inspect OP25 source/interface candidates.

## Fail-fast preflight

The live interface discovery probe first performs a short readiness preflight before long evidence collection.
If the backend is reachable but the scanner does not reach a running state during preflight, the probe reports a hard failure and skips the long collection window unless `--force-collect` is supplied.

Useful options:

- `--preflight-seconds N`: readiness window before long collection, default 20.
- `--preflight-interval N`: status polling interval during preflight, default 1.
- `--force-collect`: collect the full window even when preflight does not observe a running decoder.

## V0.2Q dynamic OP25 HTTP port probing

- Interface discovery must parse the live backend decoder command for OP25 terminal HTTP arguments such as `http:127.0.0.1:18091`.
- Runtime-declared OP25 HTTP ports must be probed before the static default port list.
- A report that says no OP25 HTTP endpoint responded is not conclusive unless the runtime-declared `-l http:...` port appears in `http_ports_probed`.

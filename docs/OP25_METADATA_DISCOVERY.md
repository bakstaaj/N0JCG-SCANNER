# OP25 Metadata Discovery

V0.2N adds a live metadata discovery probe for identifying how the installed OP25 build exposes active call metadata.

The probe polls the backend status endpoint, captures log-tail snapshots, and separates candidate lines into these categories:

- active TGID candidate lines
- voice-frequency candidate lines
- configured whitelist/blacklist TGID lines
- plaintext voice-frame lines
- encrypted-call metadata lines
- control-channel lines

The tool is discovery-only. It does not decrypt, bypass, key-load, or reconstruct protected audio.

Run on the Pi:

```bash
./tools/pi5_p25_op25_metadata_discovery_probe.sh --self-test
./tools/pi5_p25_op25_metadata_discovery_probe.sh --seconds 240 --interval 2 --yes
```

Reports are written under `.p25_op25_metadata_discovery_reports/`.

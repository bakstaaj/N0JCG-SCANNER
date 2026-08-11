# OP25 Absolute Runtime Paths

The OP25 live-command probe launches `rx.py` from the upstream OP25 apps directory so OP25 relative Python imports such as `tdma` and `tx` resolve consistently.

Because OP25 parses `trunk.tsv` from that process working directory, all generated file references inside `trunk.tsv` must be absolute paths. This includes:

- `TGID Tags File`
- `Whitelist`
- `Blacklist`

The scanner generator writes these values as absolute paths under `runtime/op25`. This avoids failures where OP25 starts correctly, opens the RTL-SDR, then fails while loading a relative whitelist path from the wrong working directory.

Validate with:

```bash
./tools/p25_validate_op25_runtime_paths.sh
```

This does not start live OP25 decode.

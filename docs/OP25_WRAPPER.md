# OP25 Wrapper Notes

V0.1B adds a guarded external-decoder wrapper. The project does not yet assume a specific OP25 install path or command-line variant.

## Runtime files generated from JSON

The project JSON config is the source of truth. The helper below generates OP25-oriented runtime files under `runtime/op25/`:

```bash
./tools/p25_generate_op25_config.sh
```

Generated files include:

- `runtime/op25/trunk.tsv`
- `runtime/op25/<system>_talkgroups.tsv`
- `runtime/op25/<system>_whitelist.tsv`
- `runtime/op25/<system>_blacklist.tsv`
- `runtime/op25/manifest.json`

`runtime/` is intentionally ignored by Git.

## Decoder discovery

The backend and Pi runtime probe search for OP25 command candidates in `PATH` and common install locations. Discovery is advisory in V0.1B. Missing OP25 is a warning in discovery/probe scripts, not a repo validation failure.

## Live decoder launch guardrail

The backend does not launch a guessed OP25 command by default. Live launch is enabled only when `P25_SCANNER_OP25_COMMAND_TEMPLATE` is set. The template may use these placeholders:

- `{trunk_tsv}`
- `{output_dir}`
- `{control_frequency_hz}`
- `{control_frequency_mhz}`

Example placeholder only, not yet validated for this hardware:

```bash
export P25_SCANNER_OP25_COMMAND_TEMPLATE='python3 /path/to/op25/op25/gr-op25_repeater/apps/rx.py -T {trunk_tsv}'
```

A later Pi hardware milestone must replace this with the validated command for the installed OP25 variant and RTL receiver mapping.

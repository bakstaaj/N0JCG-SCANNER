# Runtime Status Parsing

V0.2E adds a conservative OP25 runtime log parser.

The backend already tails OP25 stdout/stderr while the validated OP25 command is running. The parser reads those log lines and updates the existing UI status fields when it sees recognizable operational status:

- control-channel frequency
- active voice frequency
- active TGID
- talkgroup label when present
- P25 Phase I / Phase II indicators
- encrypted and muted/skipped state

The parser is best-effort and intentionally does not control OP25. It does not decode, decrypt, bypass, or recover encrypted audio. Encrypted traffic is represented only as status metadata so the UI can show that it was encrypted/muted/skipped.

Validate parser behavior without RF traffic:

```bash
./tools/pi5_p25_runtime_status_parser_probe.sh
```

The parser can be refined as real OP25 log lines are collected from field use.

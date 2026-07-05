# OP25 Voice Frame Parsing

V0.2K adds conservative parsing for OP25 voice-frame log lines such as:

```text
IMBE (PLAINTEXT) ... errs 0
AMBE (PLAINTEXT) ... errs 0
IMBE (ENCRYPTED) ...
```

These lines are metadata from OP25 stdout/stderr. The project uses them only
to update UI activity counters and evidence summaries.

Behavior:

- `IMBE (PLAINTEXT)` and `AMBE (PLAINTEXT)` count as clear voice-frame evidence.
- `IMBE (ENCRYPTED)` and `AMBE (ENCRYPTED)` count as encrypted and muted/skipped metadata.
- No encrypted audio decoding, key handling, bypass, or decryption behavior is added.
- Existing launch behavior and TOPAZ/TRWC runtime config are unchanged.

The evidence analyzer now treats activity counters as evidence even when OP25
does not include a TGID or voice frequency in the same log-tail line.

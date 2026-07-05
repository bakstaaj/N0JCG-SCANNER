# Runtime activity summary

V0.2H adds in-memory counters for status lines already parsed from OP25 output.
The summary is exposed through `/api/status` as `activity_summary` and displayed
in the web UI.

The counters are intentionally lightweight:

- parsed status lines,
- control-frequency updates,
- voice-frequency updates,
- talkgroup updates,
- unique TGIDs observed,
- clear voice events,
- encrypted events, and
- muted/skipped events.

This feature does not change OP25 launch behavior. It does not persist a call
log, decrypt encrypted audio, or attempt to bypass encryption. Encrypted calls
remain metadata-only events that are counted and shown as muted/skipped when the
parsed OP25 output indicates that state.

Validate the parser and backend integration with:

```bash
./tools/pi5_p25_runtime_activity_probe.sh
```

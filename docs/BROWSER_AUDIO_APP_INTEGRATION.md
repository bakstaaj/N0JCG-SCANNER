# V0.3O Raw Browser Audio App Integration

V0.3O promotes the proven raw browser-audio path into the application runtime.

Normal audio path:

```text
OP25 UDP PCM (-w -W 127.0.0.1 -u 23456) -> raw browser audio bridge on port 8072 -> browser audio element
```

The raw bridge intentionally does not gate, drop, or modify OP25 audio frames. It counts OP25 2-byte flag packets for visibility only.

The filtered encrypted/log-gated test path remains diagnostic-only until a future TGID-aware gate can prove it does not suppress clear traffic.

Backend endpoints:

```text
GET  /api/audio/status
POST /api/audio/start
POST /api/audio/stop
```

Scanner start behavior:

```text
POST /api/scanner/start
```

starts the raw browser-audio bridge first, then appends OP25 UDP audio output flags to the validated OP25 command if needed.

UI behavior:

The Browser Audio Output panel can start/stop the raw bridge, play the bridge test tone, and play the live `/audio.wav` stream from the browser host.

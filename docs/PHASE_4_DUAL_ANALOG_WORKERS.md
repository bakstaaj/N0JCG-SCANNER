# Phase 4 Dual Analog Receiver Workers

Both analog receivers are independently controllable while OP25 continues on its dedicated P25 receiver.

| Worker | RTL serial | Initial channel | Audio UDP |
|---|---:|---:|---:|
| Analog 2 m | 00000440 | 146.520 MHz | 23458 |
| Analog 70 cm | 00000144 | 446.000 MHz | 23459 |

API endpoints:

- `GET /api/analog/status`
- `POST /api/analog/2m/start`
- `POST /api/analog/2m/stop`
- `POST /api/analog/70cm/start`
- `POST /api/analog/70cm/stop`

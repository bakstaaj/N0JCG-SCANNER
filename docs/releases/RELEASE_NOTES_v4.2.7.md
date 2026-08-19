# N0JCG Scanner v4.2.7

## Persistent P25 service recovery

The P25 decoder now remains owned by the radio service when the web backend is
restarted. The backend follows the persistent OP25 event log, reconnects to an
already-running decoder, and avoids starting a duplicate process. Decoder
process detection validates the actual command line so transient shell matches
cannot be mistaken for a healthy receiver.

## Operator documentation and packaging

- Documented backend-only restart behavior and the correct Stop workflow.
- Refreshed the branded user manual to v4.2.7.
- Published the reproducible N0JCG Scanner v4.2.7 release archive and checksum.

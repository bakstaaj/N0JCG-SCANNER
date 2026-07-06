# Browser audio baseline

V0.3M/V0.3N established the current browser-audio baseline for the PI-P25 scanner.

## Result

The raw browser-audio bypass path produced good two-way audio without the earlier
short metallic/choppy bursts:

```text
OP25 UDP PCM -> raw browser audio bridge -> browser host speakers
```

The encrypted-log and OP25 flag-gated path suppressed encrypted bursts, but it also
suppressed or starved clear audio during later long tests. Treat that gated path as
diagnostic-only until a more precise talkgroup-aware clear/encrypted gate is built.

## Normal clear-audio test

From MSYS2 UCRT64 on the development machine:

```bash
cd ~/sdrdev/PI-P25-SCANNER
./tools/msys2_run_pi_browser_audio_clear_test.sh --seconds 300 --op25-verbosity 0
```

For longer traffic observation:

```bash
./tools/msys2_run_pi_browser_audio_clear_test.sh --seconds 900 --op25-verbosity 10
```

## Diagnostic encrypted-burst test

Use only when intentionally testing filtering behavior:

```bash
./tools/msys2_run_pi_browser_audio_filtered_test.sh --seconds 120 --op25-verbosity 10 --flag-drop-hold-ms 2500 --encrypted-log-hold-ms 5000
```

## Operating rule

Do not make encrypted/flag gates the default audio path until clear-audio pass-through
is proven with active clear talkgroups. The application default should favor clear
audio pass-through and use OP25's own encrypted skip/mute behavior plus observation
logs, not project-side PCM suppression, as the baseline.

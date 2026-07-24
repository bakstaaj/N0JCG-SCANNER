# CSV-Constrained Fast Spectrum Scanning

- Derives each receiver sweep range from the minimum and maximum enabled CSV frequencies.
- Uses `rtl_power` with 12.5 kHz bins.
- Evaluates only CSV-listed channel centers.
- Ranks candidates by power above the local row median.
- Sends at most 12 candidates into the existing NFM validation and hold path.
- Falls back to linear scanning when `search_mode` is changed to `linear`.

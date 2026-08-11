# N0JCG Scanner v4.2.0

## P25 multi-radio reliability

The validated OP25 launch marker now selects the scalable multi-radio wrapper
explicitly. RTL-SDR `00000251` is assigned to control-channel decoding and
`00000252` to voice decoding. The wrapper fails closed when the voice receiver
is unavailable, preventing an unnoticed return to the choppy single-radio
path.

## Operator tools and documentation

- Added live P25 antenna-alignment scoring by azimuth.
- Updated deployment/runtime documentation for `/home/pi/n0jcg-scanner`.
- Refreshed the branded N0JCG Scanner user manual PDF.

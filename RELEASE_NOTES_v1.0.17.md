# PI Scanner v1.0.17

- Fixes browser analog controls that remained disabled.
- Removes the cross-origin audio-arbitrator dependency from button state.
- Derives the active analog band from the worker lock state.
- Allows squelch adjustment while either analog worker is available.
- Keeps Skip and Block limited to the currently locked analog channel.
- Normalizes both VHF and UHF squelch offsets to zero during deployment.

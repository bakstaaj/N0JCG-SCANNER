(function () {
  if (window.__piScannerAudioLiveV2) return;
  window.__piScannerAudioLiveV2 = true;
  let lastSource = null;
  let reconnecting = false;

  const statusUrl = () => `http://${window.location.hostname}:8072/api/audio/status?_=${Date.now()}`;
  const streamUrl = () => `http://${window.location.hostname}:8072/audio.wav?_=${Date.now()}`;

  async function reconnect(source) {
    const player = document.getElementById('browserAudioPlayer');
    if (!player || player.paused || reconnecting) return;
    reconnecting = true;
    try {
      player.pause();
      player.src = streamUrl();
      player.load();
      await player.play();
      const node = document.getElementById('browserAudioLastEvent');
      if (node) node.textContent = `Playing ${source} scanner audio`;
    } catch (_error) {
    } finally {
      reconnecting = false;
    }
  }

  async function poll() {
    try {
      const response = await fetch(statusUrl(), { cache: 'no-store' });
      if (!response.ok) return;
      const status = await response.json();
      const source = status.active_source || null;
      if (source && source !== lastSource) await reconnect(source);
      lastSource = source;
    } catch (_error) {
    }
  }

  window.setInterval(poll, 100);
})();

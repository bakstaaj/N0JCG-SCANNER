'use strict';

// PI-P25 V0.3P browser-audio host normalizer.
// The app is served from port 8070, while the raw browser audio bridge is on
// port 8072. Backend-side status can report Pi-local names such as 127.0.1.1;
// from a desktop browser those point at the wrong host. This small client-side
// shim always normalizes audio stream URLs to the host that served the UI.
(function () {
  const AUDIO_PORT = '8072';
  const AUDIO_PATH = '/audio.wav';
  const TEST_TONE_PATH = '/test-tone.wav';
  const PATCH_MARK = '__piP25AudioHostFixV03P';

  function pageHost() {
    return window.location.hostname || window.location.host.split(':')[0] || 'PI-SDR';
  }

  function pageProtocol() {
    return window.location.protocol || 'http:';
  }

  function audioUrl(path) {
    return `${pageProtocol()}//${pageHost()}:${AUDIO_PORT}${path || AUDIO_PATH}`;
  }

  function isAudioBridgeUrl(value) {
    if (!value) return false;
    try {
      const url = new URL(String(value), window.location.href);
      return url.port === AUDIO_PORT && (url.pathname === AUDIO_PATH || url.pathname === TEST_TONE_PATH || url.pathname.endsWith('/audio.wav') || url.pathname.endsWith('/test-tone.wav'));
    } catch (_error) {
      return false;
    }
  }

  function normalizeBridgeUrl(value) {
    if (!isAudioBridgeUrl(value)) return value;
    try {
      const url = new URL(String(value), window.location.href);
      const path = url.pathname.endsWith('/test-tone.wav') ? TEST_TONE_PATH : AUDIO_PATH;
      return audioUrl(path);
    } catch (_error) {
      return value;
    }
  }

  function patchMediaSrcSetter() {
    if (HTMLMediaElement.prototype[PATCH_MARK]) return;
    const descriptor = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'src');
    if (descriptor && descriptor.get && descriptor.set) {
      Object.defineProperty(HTMLMediaElement.prototype, 'src', {
        configurable: true,
        enumerable: descriptor.enumerable,
        get() {
          return descriptor.get.call(this);
        },
        set(value) {
          descriptor.set.call(this, normalizeBridgeUrl(value));
        },
      });
    }
    const originalSetAttribute = Element.prototype.setAttribute;
    Element.prototype.setAttribute = function patchedSetAttribute(name, value) {
      if (String(name).toLowerCase() === 'src' && (this instanceof HTMLMediaElement || this instanceof HTMLSourceElement)) {
        return originalSetAttribute.call(this, name, normalizeBridgeUrl(value));
      }
      return originalSetAttribute.call(this, name, value);
    };
    Object.defineProperty(HTMLMediaElement.prototype, PATCH_MARK, { value: true });
  }

  function forceExistingMediaUrls() {
    document.querySelectorAll('audio, video, source').forEach((element) => {
      const raw = element.getAttribute('src') || element.src || '';
      const normalized = normalizeBridgeUrl(raw);
      if (normalized && normalized !== raw) {
        const media = element instanceof HTMLMediaElement ? element : element.parentElement;
        const shouldResume = media instanceof HTMLMediaElement && !media.paused && !media.ended;
        element.setAttribute('src', normalized);
        if (media instanceof HTMLMediaElement) {
          try { media.load(); } catch (_error) { /* no-op */ }
          if (shouldResume) {
            media.play().catch(() => {});
          }
        }
      }
    });
  }

  function ensureFallbackPlayer() {
    const panel = document.querySelector('.browser-audio-panel');
    if (!panel) return;
    let player = document.getElementById('piP25RawAudioPlayer');
    if (!player) {
      player = document.createElement('audio');
      player.id = 'piP25RawAudioPlayer';
      player.controls = true;
      player.preload = 'none';
      player.style.display = 'block';
      player.style.width = '100%';
      player.style.marginTop = '0.75rem';
      panel.appendChild(player);
    }
    if (player.src !== audioUrl(AUDIO_PATH)) {
      player.src = audioUrl(AUDIO_PATH);
    }
  }

  function ensureOpenLink() {
    const panel = document.querySelector('.browser-audio-panel');
    if (!panel) return;
    let link = document.getElementById('piP25RawAudioOpenLink');
    if (!link) {
      link = document.createElement('a');
      link.id = 'piP25RawAudioOpenLink';
      link.target = '_blank';
      link.rel = 'noopener';
      link.textContent = 'Open raw audio stream';
      link.style.display = 'inline-block';
      link.style.marginTop = '0.5rem';
      panel.appendChild(link);
    }
    link.href = audioUrl(AUDIO_PATH);
  }

  function updateStatusText() {
    const streamSource = document.getElementById('browserAudioStreamSource');
    if (streamSource) {
      streamSource.textContent = audioUrl(AUDIO_PATH);
    }
    const lastEvent = document.getElementById('browserAudioLastEvent');
    if (lastEvent && /127\.0\.|localhost|pending OP25 audio bridge/i.test(lastEvent.textContent || '')) {
      lastEvent.textContent = `Audio stream normalized to ${audioUrl(AUDIO_PATH)}`;
    }
  }

  function tick() {
    window.PI_P25_BROWSER_AUDIO_URL = audioUrl(AUDIO_PATH);
    window.PI_P25_BROWSER_AUDIO_TEST_TONE_URL = audioUrl(TEST_TONE_PATH);
    forceExistingMediaUrls();
    ensureFallbackPlayer();
    ensureOpenLink();
    updateStatusText();
  }

  patchMediaSrcSetter();
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', tick, { once: true });
  } else {
    tick();
  }
  const observer = new MutationObserver(() => tick());
  observer.observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ['src'] });
  window.setInterval(tick, 750);
})();

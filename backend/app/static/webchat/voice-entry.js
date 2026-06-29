(function () {
  'use strict';
  if (window.__NEXUSDESK_WEBCHAT_VOICE_ENTRY_LOADED__) return;
  window.__NEXUSDESK_WEBCHAT_VOICE_ENTRY_LOADED__ = true;

  var script = document.currentScript || (function () {
    var scripts = document.getElementsByTagName('script');
    return scripts[scripts.length - 1];
  })();
  var scriptUrl = new URL(script.src, window.location.href);
  var apiBase = (script.getAttribute('data-api-base') || scriptUrl.origin).replace(/\/$/, '');
  var tenantKey = script.getAttribute('data-tenant') || 'default';
  var channelKey = script.getAttribute('data-channel') || 'default';
  var title = script.getAttribute('data-title') || 'WebCall';
  var locale = (script.getAttribute('data-locale') || navigator.language || 'en').toLowerCase();
  var buttonLabel = script.getAttribute('data-voice-label') || (locale.indexOf('zh') === 0 ? '网页语音' : 'WebCall');
  var liveVoiceMode = (script.getAttribute('data-live-voice-mode') || 'off').toLowerCase();
  var liveVoiceWsPath = script.getAttribute('data-live-voice-ws-path') || '/webchat/live/ws';
  var liveVoiceLabel = script.getAttribute('data-live-voice-label') || 'VOIP Call';
  var storageKey = 'nexusdesk:webchat:' + apiBase + ':' + tenantKey + ':' + channelKey;
  var lastVoiceStartedKey = storageKey + ':last_voice_started_at';
  var voiceCooldownMs = Number(script.getAttribute('data-voice-cooldown-ms') || '60000');
  if (!Number.isFinite(voiceCooldownMs) || voiceCooldownMs < 0) voiceCooldownMs = 60000;
  var state = { conversationId: null, visitorToken: null, busy: false, enabled: false, provider: 'mock', livekitUrl: null, lastVoiceStartedAt: 0 };

  function loadCache() {
    try {
      var cached = JSON.parse(window.sessionStorage.getItem(storageKey) || '{}');
      state.conversationId = cached.conversationId || null;
      state.visitorToken = cached.visitorToken || null;
      state.lastVoiceStartedAt = Number(window.sessionStorage.getItem(lastVoiceStartedKey) || cached.lastVoiceStartedAt || 0) || 0;
    } catch (err) {}
  }
  function persist() {
    try {
      window.sessionStorage.setItem(storageKey, JSON.stringify({
        conversationId: state.conversationId,
        visitorToken: state.visitorToken,
        lastVoiceStartedAt: state.lastVoiceStartedAt || 0
      }));
      if (state.lastVoiceStartedAt) window.sessionStorage.setItem(lastVoiceStartedKey, String(state.lastVoiceStartedAt));
    } catch (err) {}
  }

  function ready(fn) {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', fn, { once: true });
      return;
    }
    fn();
  }

  function byId(id) {
    return document.getElementById(id);
  }

  function safeLiveVoicePath(path) {
    var value = String(path || '').trim();
    if (!value || value.indexOf('://') !== -1 || value.charAt(0) !== '/') return '/webchat/live/ws';
    return value;
  }

  function bootEdgeVoiceCard() {
    var liveState = {
      ws: null,
      audioContext: null,
      source: null,
      processor: null,
      silentGain: null,
      stream: null,
      playingSources: [],
      currentTtsSampleRate: 24000,
      nextPlayTime: 0
    };

    function setLiveStatus(text) {
      var el = byId('nd-live-voice-status');
      if (el) el.textContent = text || '';
    }

    function addLiveMessage(kind, text) {
      if (!text) return;
      var wrap = byId('nd-live-voice-transcript');
      if (!wrap) return;
      var div = document.createElement('div');
      div.className = 'nd-live-voice-msg ' + kind;
      div.textContent = text;
      wrap.appendChild(div);
      wrap.scrollTop = wrap.scrollHeight;
    }

    function stopLivePlayback() {
      liveState.playingSources.forEach(function (sourceNode) {
        try { sourceNode.stop(); } catch (err) {}
      });
      liveState.playingSources = [];
      if (liveState.audioContext) liveState.nextPlayTime = liveState.audioContext.currentTime + 0.03;
    }

    function floatTo16BitPcm(float32Array) {
      var out = new Int16Array(float32Array.length);
      for (var index = 0; index < float32Array.length; index += 1) {
        var sample = Math.max(-1, Math.min(1, float32Array[index]));
        out[index] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
      }
      return out;
    }

    function downsampleBuffer(buffer, inputRate, outputRate) {
      if (outputRate === inputRate) return buffer;
      var ratio = inputRate / outputRate;
      var newLength = Math.round(buffer.length / ratio);
      var result = new Float32Array(newLength);
      var offsetResult = 0;
      var offsetBuffer = 0;
      while (offsetResult < result.length) {
        var nextOffsetBuffer = Math.round((offsetResult + 1) * ratio);
        var accum = 0;
        var count = 0;
        for (var i = offsetBuffer; i < nextOffsetBuffer && i < buffer.length; i += 1) {
          accum += buffer[i];
          count += 1;
        }
        result[offsetResult] = count ? accum / count : 0;
        offsetResult += 1;
        offsetBuffer = nextOffsetBuffer;
      }
      return result;
    }

    function playPcm16(arrayBuffer, sampleRate) {
      if (!liveState.audioContext) return Promise.resolve();
      var pcm = new Int16Array(arrayBuffer);
      if (!pcm.length) return Promise.resolve();
      var float32 = new Float32Array(pcm.length);
      for (var index = 0; index < pcm.length; index += 1) {
        float32[index] = pcm[index] / 32768;
      }
      var audioBuffer = liveState.audioContext.createBuffer(1, float32.length, sampleRate || 24000);
      audioBuffer.copyToChannel(float32, 0);
      var sourceNode = liveState.audioContext.createBufferSource();
      sourceNode.buffer = audioBuffer;
      sourceNode.connect(liveState.audioContext.destination);
      liveState.playingSources.push(sourceNode);
      sourceNode.onended = function () {
        liveState.playingSources = liveState.playingSources.filter(function (item) { return item !== sourceNode; });
      };
      var startAt = Math.max(liveState.audioContext.currentTime + 0.03, liveState.nextPlayTime || 0);
      sourceNode.start(startAt);
      liveState.nextPlayTime = startAt + audioBuffer.duration;
      return Promise.resolve();
    }

    function injectLiveStyles() {
      if (byId('nd-live-voice-style')) return;
      var styleEl = document.createElement('style');
      styleEl.id = 'nd-live-voice-style';
      styleEl.textContent = '\n'
        + '.nd-live-voice-trigger{display:inline-flex;flex-direction:column;align-items:center;justify-content:center;gap:4px;min-width:72px;min-height:54px;margin-left:auto;margin-right:8px;padding:8px 10px;border:0;border-radius:14px;background:#f97316;color:#fff;cursor:pointer;box-shadow:0 10px 24px rgba(249,115,22,.28);font:700 11px/1.05 system-ui,-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;white-space:nowrap}\n'
        + '.nd-live-voice-trigger:hover{transform:translateY(-1px);box-shadow:0 14px 28px rgba(249,115,22,.36)}\n'
        + '.nd-live-voice-trigger.is-live{animation:ndLiveVoicePulse 1.15s infinite}\n'
        + '@keyframes ndLiveVoicePulse{0%{box-shadow:0 0 0 0 rgba(249,115,22,.52)}70%{box-shadow:0 0 0 12px rgba(249,115,22,0)}100%{box-shadow:0 0 0 0 rgba(249,115,22,0)}}\n'
        + '.nd-live-voice-panel{display:none;border-bottom:1px solid #eef2f7;background:#fff7ed;padding:12px 16px 14px}\n'
        + '.nd-live-voice-panel.open{display:block}\n'
        + '.nd-live-voice-row{display:flex;gap:8px;align-items:center}\n'
        + '.nd-live-voice-select{flex:1;min-width:0;height:38px;border:1px solid #e2e8f0;border-radius:12px;background:#fff;color:#0f172a;padding:0 10px;font-size:13px;outline:none}\n'
        + '.nd-live-voice-start{height:38px;min-width:76px;border:0;border-radius:12px;background:#f97316;color:#fff;font-weight:800;cursor:pointer}\n'
        + '.nd-live-voice-start.stop{background:#dc2626}\n'
        + '.nd-live-voice-status{margin-top:10px;border:1px solid #fed7aa;background:#fffaf6;color:#9a3412;border-radius:13px;padding:9px 10px;font-size:12.5px;line-height:1.45}\n'
        + '.nd-live-voice-transcript{margin-top:8px;max-height:126px;overflow:auto}\n'
        + '.nd-live-voice-msg{margin-top:6px;padding:8px 10px;border-radius:12px;font-size:12.5px;line-height:1.42}\n'
        + '.nd-live-voice-msg.user{background:#eff6ff;color:#1e3a8a}\n'
        + '.nd-live-voice-msg.ai{background:#fff;color:#7c2d12;border:1px solid #fed7aa}\n'
        + '.nd-live-voice-foot{margin-top:8px;color:#64748b;font-size:11.5px}\n';
      document.head.appendChild(styleEl);
    }

    function openChatPanel() {
      var panel = byId('chatPanel');
      var backdrop = byId('chatBackdrop');
      var openBtn = byId('floatingChat');
      if (!panel) return;
      if ((panel.classList.contains('is-closed') || panel.getAttribute('aria-hidden') === 'true') && openBtn) {
        try { openBtn.click(); } catch (err) {}
      }
      panel.classList.remove('is-closed');
      panel.classList.add('is-open');
      panel.setAttribute('aria-hidden', 'false');
      document.body.classList.add('chat-open');
      if (openBtn) openBtn.setAttribute('aria-expanded', 'true');
      if (backdrop) backdrop.hidden = false;
    }

    function phoneIcon() {
      return '<svg viewBox="0 0 24 24" aria-hidden="true" width="18" height="18"><path fill="currentColor" d="M6.62 10.79a15.05 15.05 0 0 0 6.59 6.59l2.2-2.2c.27-.27.67-.36 1.02-.24 1.12.37 2.33.57 3.57.57.55 0 1 .45 1 1V20c0 .55-.45 1-1 1C10.07 21 3 13.93 3 5c0-.55.45-1 1-1h3.5c.55 0 1 .45 1 1 0 1.24.19 2.45.57 3.57.11.35.03.74-.25 1.02l-2.2 2.2z"/></svg>';
    }

    function injectLiveVoiceUI() {
      var panel = byId('chatPanel');
      if (!panel) return false;
      var head = panel.querySelector('.chat-head');
      if (!head) return false;
      var closeBtn = byId('closeChat');
      if (!byId('nd-live-voice-trigger')) {
        var trigger = document.createElement('button');
        trigger.id = 'nd-live-voice-trigger';
        trigger.className = 'nd-live-voice-trigger';
        trigger.type = 'button';
        trigger.setAttribute('title', 'Start ' + liveVoiceLabel);
        trigger.setAttribute('aria-label', 'Start ' + liveVoiceLabel);
        trigger.innerHTML = '<span>' + phoneIcon() + '</span><span>' + liveVoiceLabel + '</span>';
        if (closeBtn && closeBtn.parentElement === head) {
          head.insertBefore(trigger, closeBtn);
        } else {
          head.appendChild(trigger);
        }
        trigger.addEventListener('click', function () {
          openChatPanel();
          var tray = byId('nd-live-voice-panel');
          if (tray) tray.classList.toggle('open');
        });
      }
      if (!byId('nd-live-voice-panel')) {
        var tray = document.createElement('div');
        tray.id = 'nd-live-voice-panel';
        tray.className = 'nd-live-voice-panel';
        tray.innerHTML = ''
          + '<div class="nd-live-voice-row">'
          + '<select class="nd-live-voice-select" id="nd-live-voice-preset">'
          + '<option value="de|de_DE-thorsten-medium|1.0">Deutsch</option>'
          + '<option value="i|if_sara|1.0">Italiano</option>'
          + '<option value="f|ff_siwis|1.0">Francais</option>'
          + '<option value="b|bm_george|1.0">English UK</option>'
          + '</select>'
          + '<button class="nd-live-voice-start" id="nd-live-voice-start" type="button">Start</button>'
          + '</div>'
          + '<div class="nd-live-voice-status" id="nd-live-voice-status">Tap Start and allow microphone access.</div>'
          + '<div class="nd-live-voice-transcript" id="nd-live-voice-transcript"></div>'
          + '<div class="nd-live-voice-foot">Voice support is live. Do not share passwords or payment codes.</div>';
        head.insertAdjacentElement('afterend', tray);
        byId('nd-live-voice-start').addEventListener('click', startLiveVoice);
      }
      return true;
    }

    function buildLiveWsUrl(langCode, voice, speed) {
      var url = new URL(safeLiveVoicePath(liveVoiceWsPath), window.location.href);
      url.protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      url.host = window.location.host;
      url.search = '';
      url.searchParams.set('lang_code', langCode);
      url.searchParams.set('voice', voice);
      url.searchParams.set('speed', speed);
      return url.toString();
    }

    function resetLiveButtons(statusText) {
      var trigger = byId('nd-live-voice-trigger');
      var startBtn = byId('nd-live-voice-start');
      if (trigger) trigger.classList.remove('is-live');
      if (startBtn) {
        startBtn.classList.remove('stop');
        startBtn.textContent = 'Start';
      }
      if (statusText) setLiveStatus(statusText);
    }

    function stopLiveVoice() {
      try { if (liveState.ws) liveState.ws.close(); } catch (err) {}
      try { if (liveState.processor) liveState.processor.disconnect(); } catch (err) {}
      try { if (liveState.source) liveState.source.disconnect(); } catch (err) {}
      try { if (liveState.silentGain) liveState.silentGain.disconnect(); } catch (err) {}
      try { if (liveState.stream) liveState.stream.getTracks().forEach(function (track) { track.stop(); }); } catch (err) {}
      stopLivePlayback();
      liveState.ws = null;
      liveState.processor = null;
      liveState.source = null;
      liveState.silentGain = null;
      liveState.stream = null;
      resetLiveButtons('Voice stopped.');
    }

    function handleLiveMessage(event) {
      if (typeof event.data !== 'string') {
        return playPcm16(event.data, liveState.currentTtsSampleRate || 24000);
      }
      var payload = null;
      try { payload = JSON.parse(event.data); } catch (err) {}
      if (!payload) return Promise.resolve();
      if (payload.type === 'barge_in') {
        stopLivePlayback();
        setLiveStatus('Listening...');
      }
      if (payload.type === 'speech_start') setLiveStatus('Listening...');
      if (payload.type === 'stt_start') setLiveStatus('Transcribing...');
      if (payload.type === 'stt_result') {
        addLiveMessage('user', payload.text);
        setLiveStatus('Thinking...');
      }
      if (payload.type === 'ai_answer') {
        addLiveMessage('ai', payload.answer);
        setLiveStatus('Speaking...');
      }
      if (payload.type === 'tts_start' && payload.sample_rate) {
        liveState.currentTtsSampleRate = payload.sample_rate;
        if (liveState.audioContext) liveState.nextPlayTime = liveState.audioContext.currentTime + 0.03;
      }
      if (payload.type === 'tts_end') setLiveStatus('Ready. You can speak again.');
      if (payload.type === 'turn_error') setLiveStatus('Voice error: ' + (payload.message || payload.error || 'unknown'));
      return Promise.resolve();
    }

    function startLiveVoice() {
      var startBtn = byId('nd-live-voice-start');
      var trigger = byId('nd-live-voice-trigger');
      var presetEl = byId('nd-live-voice-preset');
      if (liveState.ws && liveState.ws.readyState === WebSocket.OPEN) {
        stopLiveVoice();
        return;
      }
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        setLiveStatus('Microphone is not available in this browser.');
        return;
      }
      var preset = (presetEl && presetEl.value ? presetEl.value : 'de|de_DE-thorsten-medium|1.0').split('|');
      var langCode = preset[0] || 'de';
      var voice = preset[1] || 'de_DE-thorsten-medium';
      var speed = preset[2] || '1.0';
      setLiveStatus('Connecting voice support...');
      liveState.ws = new WebSocket(buildLiveWsUrl(langCode, voice, speed));
      liveState.ws.binaryType = 'arraybuffer';
      liveState.ws.onopen = function () {
        Promise.resolve().then(function () {
          if (trigger) trigger.classList.add('is-live');
          if (startBtn) {
            startBtn.classList.add('stop');
            startBtn.textContent = 'Stop';
          }
          setLiveStatus('Connected. Requesting microphone access...');
          liveState.audioContext = new (window.AudioContext || window.webkitAudioContext)();
          return liveState.audioContext.resume();
        }).then(function () {
          return navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
        }).then(function (mediaStream) {
          liveState.stream = mediaStream;
          setLiveStatus('Listening...');
          liveState.source = liveState.audioContext.createMediaStreamSource(mediaStream);
          liveState.processor = liveState.audioContext.createScriptProcessor(4096, 1, 1);
          liveState.silentGain = liveState.audioContext.createGain();
          liveState.silentGain.gain.value = 0;
          liveState.processor.onaudioprocess = function (audioEvent) {
            if (!liveState.ws || liveState.ws.readyState !== WebSocket.OPEN) return;
            var input = audioEvent.inputBuffer.getChannelData(0);
            var down = downsampleBuffer(input, liveState.audioContext.sampleRate, 16000);
            var pcm16 = floatTo16BitPcm(down);
            liveState.ws.send(pcm16.buffer);
          };
          liveState.source.connect(liveState.processor);
          liveState.processor.connect(liveState.silentGain);
          liveState.silentGain.connect(liveState.audioContext.destination);
        }).catch(function (err) {
          setLiveStatus('Microphone error: ' + (err && err.message ? err.message : String(err)));
          stopLiveVoice();
        });
      };
      liveState.ws.onmessage = function (event) { handleLiveMessage(event); };
      liveState.ws.onerror = function () { setLiveStatus('WebSocket error.'); };
      liveState.ws.onclose = function () { resetLiveButtons('Voice disconnected.'); };
    }

    ready(function () {
      injectLiveStyles();
      var tries = 0;
      var timer = setInterval(function () {
        tries += 1;
        if (injectLiveVoiceUI() || tries >= 40) {
          clearInterval(timer);
        }
      }, 150);
    });
  }

  loadCache();

  if (liveVoiceMode === 'edge-card') {
    bootEdgeVoiceCard();
    return;
  }

  var style = document.createElement('style');
  style.textContent = '\n'
    + '.nd-webchat-voice-entry{position:fixed;right:22px;bottom:76px;z-index:2147482999;border:0;border-radius:999px;background:#f97316;color:#fff;padding:10px 14px;font:750 13px/18px system-ui,-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;box-shadow:0 12px 28px rgba(249,115,22,.28);cursor:pointer;display:none}\n'
    + '.nd-webchat-voice-entry[data-visible=true]{display:inline-flex;align-items:center;gap:8px}\n'
    + '.nd-webchat-voice-entry:disabled{opacity:.62;cursor:not-allowed}\n'
    + '.nd-webchat-voice-status{position:fixed;right:22px;bottom:126px;z-index:2147482999;max-width:320px;padding:9px 11px;border-radius:12px;background:#101828;color:#fff;font:600 12px/16px system-ui;box-shadow:0 10px 26px rgba(15,23,42,.2);display:none}\n'
    + '.nd-webchat-voice-status[data-visible=true]{display:block}\n'
    + '@media (max-width:480px){.nd-webchat-voice-entry{right:16px;bottom:70px}.nd-webchat-voice-status{right:16px;bottom:118px;max-width:calc(100vw - 32px)}}\n';
  document.head.appendChild(style);

  var button = document.createElement('button');
  button.type = 'button';
  button.className = 'nd-webchat-voice-entry';
  button.setAttribute('aria-label', title);
  button.textContent = '🎙 ' + buttonLabel;
  var statusEl = document.createElement('div');
  statusEl.className = 'nd-webchat-voice-status';
  document.body.appendChild(button);
  document.body.appendChild(statusEl);

  function setStatus(text, visible) {
    statusEl.textContent = text || '';
    statusEl.setAttribute('data-visible', visible ? 'true' : 'false');
  }
  function textFallbackMessage(message) {
    return (message || 'WebCall unavailable') + '. Continue in WebChat text support from this page.';
  }
  function cooldownRemainingMs() {
    var elapsed = Date.now() - (state.lastVoiceStartedAt || 0);
    return Math.max(0, voiceCooldownMs - elapsed);
  }
  function recordVoiceStarted() {
    state.lastVoiceStartedAt = Date.now();
    persist();
  }
  function api(path, options, timeoutMs) {
    options = options || {};
    timeoutMs = timeoutMs || 12000;
    var headers = Object.assign({ 'Content-Type': 'application/json' }, options.headers || {});
    var controller = window.AbortController ? new AbortController() : null;
    var timer = controller ? setTimeout(function () { controller.abort(); }, timeoutMs) : null;
    return fetch(apiBase + path, Object.assign({ mode: 'cors', signal: controller ? controller.signal : undefined }, options, { headers: headers })).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok) {
          var err = new Error(data.detail && data.detail.message ? data.detail.message : data.detail || ('HTTP ' + res.status));
          err.status = res.status;
          err.payload = data;
          throw err;
        }
        return data;
      });
    }).finally(function () { if (timer) clearTimeout(timer); });
  }
  function refreshRuntimeConfig() {
    return api('/api/webchat/voice/runtime-config', { method: 'GET' }, 7000).then(function (config) {
      state.enabled = Boolean(config && config.enabled);
      state.provider = config && config.provider ? String(config.provider) : 'mock';
      state.livekitUrl = config && config.livekit_url ? String(config.livekit_url) : null;
      button.setAttribute('data-visible', state.enabled ? 'true' : 'false');
    }).catch(function () {
      state.enabled = false;
      state.provider = 'mock';
      state.livekitUrl = null;
      button.setAttribute('data-visible', 'false');
    });
  }
  function ensureSession() {
    if (state.conversationId && state.visitorToken) return Promise.resolve();
    setStatus('Preparing WebCall session...', true);
    return api('/api/webchat/init', {
      method: 'POST',
      body: JSON.stringify({
        tenant_key: tenantKey,
        channel_key: channelKey,
        origin: window.location.origin,
        page_url: window.location.href
      })
    }, 12000).then(function (data) {
      state.conversationId = data.conversation_id;
      state.visitorToken = data.visitor_token;
      persist();
    });
  }
  function buildWebCallUrl(data) {
    if (state.provider !== 'livekit' || !state.livekitUrl || !data.participant_token) {
      return data.voice_page_url || ('/webchat/voice/' + encodeURIComponent(data.voice_session_id));
    }
    var hash = new URLSearchParams();
    hash.set('api_base', apiBase);
    hash.set('conversation_id', state.conversationId || '');
    hash.set('visitor_token', state.visitorToken || '');
    hash.set('livekit_url', state.livekitUrl);
    hash.set('participant_token', data.participant_token);
    hash.set('room_name', data.provider_room_name || data.room_name || '');
    hash.set('participant_identity', data.participant_identity || '');
    hash.set('provider', data.provider || state.provider);
    return '/webcall/' + encodeURIComponent(data.voice_session_id) + '#' + hash.toString();
  }
  function startVoiceCall() {
    if (!state.enabled || state.busy) return;
    var remainingMs = cooldownRemainingMs();
    if (remainingMs > 0) {
      var remainingSeconds = Math.max(1, Math.ceil(remainingMs / 1000));
      setStatus('A WebCall was just started. Please use the opened call window or wait ' + remainingSeconds + ' seconds before starting another one.', true);
      return;
    }
    state.busy = true;
    button.disabled = true;
    setStatus('Starting WebCall session...', true);
    ensureSession().then(function () {
      return api('/api/webchat/conversations/' + encodeURIComponent(state.conversationId) + '/voice/sessions', {
        method: 'POST',
        headers: { 'X-Webchat-Visitor-Token': state.visitorToken },
        body: JSON.stringify({ locale: locale, recording_consent: false })
      }, 12000);
    }).then(function (data) {
      recordVoiceStarted();
      setStatus('WebCall session created. Opening call room...', true);
      var url = buildWebCallUrl(data);
      var opened = window.open(apiBase + url, 'nexusdesk_webcall_' + data.voice_session_id, 'noopener,noreferrer,width=460,height=720');
      if (!opened) {
        setStatus('Popup blocked. Please allow popups and use the opened WebCall request or wait before starting another one.', true);
        return;
      }
      setTimeout(function () { setStatus('', false); }, 2600);
    }).catch(function (err) {
      setStatus(textFallbackMessage(err && err.message ? err.message : 'WebCall unavailable'), true);
    }).finally(function () {
      state.busy = false;
      button.disabled = false;
    });
  }

  button.addEventListener('click', startVoiceCall);
  refreshRuntimeConfig();
})();

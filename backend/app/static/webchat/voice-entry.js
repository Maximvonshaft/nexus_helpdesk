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
  var storageKey = 'nexusdesk:webchat:' + apiBase + ':' + tenantKey + ':' + channelKey;
  var state = { conversationId: null, visitorToken: null, busy: false, enabled: false, provider: 'mock', livekitUrl: null };

  function loadCache() {
    try {
      var cached = JSON.parse(window.sessionStorage.getItem(storageKey) || '{}');
      state.conversationId = cached.conversationId || null;
      state.visitorToken = cached.visitorToken || null;
    } catch (err) {}
  }
  function persist() {
    try { window.sessionStorage.setItem(storageKey, JSON.stringify({ conversationId: state.conversationId, visitorToken: state.visitorToken })); } catch (err) {}
  }
  loadCache();

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
      setStatus('WebCall session created. Opening call room...', true);
      var url = buildWebCallUrl(data);
      var opened = window.open(apiBase + url, 'nexusdesk_webcall_' + data.voice_session_id, 'noopener,noreferrer,width=460,height=720');
      if (!opened) {
        setStatus('Popup blocked. Please allow popups and click WebCall again.', true);
        return;
      }
      setTimeout(function () { setStatus('', false); }, 2600);
    }).catch(function (err) {
      setStatus(err && err.message ? err.message : 'WebCall unavailable', true);
    }).finally(function () {
      state.busy = false;
      button.disabled = false;
    });
  }

  button.addEventListener('click', startVoiceCall);
  refreshRuntimeConfig();
})();

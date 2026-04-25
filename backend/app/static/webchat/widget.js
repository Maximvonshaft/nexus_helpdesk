(function () {
  'use strict';

  var script = document.currentScript || (function () {
    var scripts = document.getElementsByTagName('script');
    return scripts[scripts.length - 1];
  })();

  var scriptUrl = new URL(script.src, window.location.href);
  var apiBase = (script.getAttribute('data-api-base') || scriptUrl.origin).replace(/\/$/, '');
  var tenantKey = script.getAttribute('data-tenant') || 'default';
  var channelKey = script.getAttribute('data-channel') || 'default';
  var title = script.getAttribute('data-title') || 'Customer Support';
  var welcome = script.getAttribute('data-welcome') || 'Hi, how can we help you today?';
  var storageKey = 'nexusdesk:webchat:' + apiBase + ':' + tenantKey + ':' + channelKey;
  var state = { conversationId: null, visitorToken: null, open: false, busy: false, pollTimer: null, messages: [] };

  try {
    var cached = JSON.parse(window.localStorage.getItem(storageKey) || '{}');
    state.conversationId = cached.conversationId || null;
    state.visitorToken = cached.visitorToken || null;
  } catch (err) {}

  var style = document.createElement('style');
  style.textContent = '\n'
    + '.nd-webchat-button{position:fixed;right:20px;bottom:20px;z-index:2147483000;border:0;border-radius:999px;background:#101828;color:white;padding:14px 18px;font:600 14px system-ui,-apple-system,Segoe UI,sans-serif;box-shadow:0 16px 35px rgba(15,23,42,.28);cursor:pointer}\n'
    + '.nd-webchat-panel{position:fixed;right:20px;bottom:76px;z-index:2147483000;width:360px;max-width:calc(100vw - 32px);height:520px;max-height:calc(100vh - 110px);display:none;flex-direction:column;background:white;border:1px solid #e5e7eb;border-radius:20px;box-shadow:0 24px 60px rgba(15,23,42,.22);overflow:hidden;font:14px system-ui,-apple-system,Segoe UI,sans-serif;color:#101828}\n'
    + '.nd-webchat-panel[data-open=true]{display:flex}\n'
    + '.nd-webchat-header{padding:16px;background:#101828;color:white}.nd-webchat-header strong{display:block;font-size:16px}.nd-webchat-header span{display:block;opacity:.76;font-size:12px;margin-top:4px}\n'
    + '.nd-webchat-messages{flex:1;overflow:auto;padding:14px;background:#f8fafc}.nd-webchat-msg{max-width:82%;margin:0 0 10px;padding:10px 12px;border-radius:14px;line-height:1.4;white-space:pre-wrap;word-break:break-word}.nd-webchat-msg.visitor{margin-left:auto;background:#101828;color:white;border-bottom-right-radius:4px}.nd-webchat-msg.agent,.nd-webchat-msg.system{margin-right:auto;background:white;border:1px solid #e5e7eb;border-bottom-left-radius:4px}\n'
    + '.nd-webchat-form{display:flex;gap:8px;padding:12px;border-top:1px solid #e5e7eb;background:white}.nd-webchat-input{flex:1;min-width:0;border:1px solid #d0d5dd;border-radius:12px;padding:10px 12px;font:14px system-ui}.nd-webchat-send{border:0;border-radius:12px;background:#101828;color:white;padding:0 14px;font-weight:700;cursor:pointer}.nd-webchat-status{padding:6px 14px;font-size:12px;color:#667085;border-top:1px solid #f2f4f7}\n'
    + '@media (max-width:480px){.nd-webchat-panel{right:8px;left:8px;bottom:72px;width:auto;height:min(560px,calc(100vh - 92px))}.nd-webchat-button{right:12px;bottom:12px}}';
  document.head.appendChild(style);

  var button = document.createElement('button');
  button.className = 'nd-webchat-button';
  button.type = 'button';
  button.textContent = 'Chat with us';

  var panel = document.createElement('section');
  panel.className = 'nd-webchat-panel';
  panel.setAttribute('aria-label', title);
  panel.innerHTML = ''
    + '<div class="nd-webchat-header"><strong></strong><span>Usually replies soon</span></div>'
    + '<div class="nd-webchat-messages" role="log" aria-live="polite"></div>'
    + '<form class="nd-webchat-form"><input class="nd-webchat-input" maxlength="2000" placeholder="Type your message…" autocomplete="off" /><button class="nd-webchat-send" type="submit">Send</button></form>'
    + '<div class="nd-webchat-status">Ready</div>';
  panel.querySelector('.nd-webchat-header strong').textContent = title;

  document.body.appendChild(panel);
  document.body.appendChild(button);

  var messagesEl = panel.querySelector('.nd-webchat-messages');
  var inputEl = panel.querySelector('.nd-webchat-input');
  var formEl = panel.querySelector('.nd-webchat-form');
  var statusEl = panel.querySelector('.nd-webchat-status');

  function setStatus(text) { statusEl.textContent = text; }
  function persist() { window.localStorage.setItem(storageKey, JSON.stringify({ conversationId: state.conversationId, visitorToken: state.visitorToken })); }
  function api(path, options) {
    return fetch(apiBase + path, Object.assign({ headers: { 'Content-Type': 'application/json' }, mode: 'cors' }, options || {}))
      .then(function (res) { return res.json().catch(function () { return {}; }).then(function (data) { if (!res.ok) { var err = new Error(data.detail && data.detail.message ? data.detail.message : data.detail || ('HTTP ' + res.status)); err.payload = data; throw err; } return data; }); });
  }
  function render() {
    messagesEl.innerHTML = '';
    if (!state.messages.length) {
      var w = document.createElement('div');
      w.className = 'nd-webchat-msg agent';
      w.textContent = welcome;
      messagesEl.appendChild(w);
    }
    state.messages.forEach(function (msg) {
      var el = document.createElement('div');
      el.className = 'nd-webchat-msg ' + (msg.direction === 'visitor' ? 'visitor' : msg.direction === 'agent' ? 'agent' : 'system');
      el.textContent = msg.body || '';
      messagesEl.appendChild(el);
    });
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }
  function init() {
    setStatus('Connecting…');
    return api('/api/webchat/init', { method: 'POST', body: JSON.stringify({
      tenant_key: tenantKey,
      channel_key: channelKey,
      conversation_id: state.conversationId,
      visitor_token: state.visitorToken,
      origin: window.location.origin,
      page_url: window.location.href
    })}).then(function (data) {
      state.conversationId = data.conversation_id;
      state.visitorToken = data.visitor_token;
      persist();
      setStatus('Connected');
      return poll();
    }).catch(function () { setStatus('Temporarily unavailable. Please try again later.'); });
  }
  function poll() {
    if (!state.conversationId || !state.visitorToken) return Promise.resolve();
    return api('/api/webchat/conversations/' + encodeURIComponent(state.conversationId) + '/messages?visitor_token=' + encodeURIComponent(state.visitorToken))
      .then(function (data) { state.messages = data.messages || []; render(); setStatus('Connected'); })
      .catch(function () { setStatus('Offline. Reconnecting…'); });
  }
  function startPolling() {
    if (state.pollTimer) clearInterval(state.pollTimer);
    state.pollTimer = setInterval(function () { if (state.open) poll(); }, 4000);
  }
  function openPanel() {
    state.open = !state.open;
    panel.setAttribute('data-open', state.open ? 'true' : 'false');
    if (state.open) {
      button.textContent = 'Close chat';
      init().then(startPolling);
      setTimeout(function () { inputEl.focus(); }, 50);
    } else {
      button.textContent = 'Chat with us';
    }
  }
  button.addEventListener('click', openPanel);
  formEl.addEventListener('submit', function (event) {
    event.preventDefault();
    var body = inputEl.value.trim();
    if (!body || state.busy) return;
    state.busy = true;
    setStatus('Sending…');
    init().then(function () {
      return api('/api/webchat/conversations/' + encodeURIComponent(state.conversationId) + '/messages', { method: 'POST', body: JSON.stringify({ visitor_token: state.visitorToken, body: body }) });
    }).then(function () {
      inputEl.value = '';
      setStatus('Sent');
      return poll();
    }).catch(function () {
      setStatus('Failed to send. Please retry.');
    }).finally(function () { state.busy = false; });
  });
  render();
})();

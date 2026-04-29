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
  var title = script.getAttribute('data-title') || 'Speedaf Support';
  var subtitle = script.getAttribute('data-subtitle') || 'Usually replies instantly';
  var assistantName = script.getAttribute('data-assistant-name') || 'Speedy';
  var locale = (script.getAttribute('data-locale') || navigator.language || 'en').toLowerCase();
  var defaultWelcome = locale.indexOf('zh') === 0
    ? '您好，我是 ' + assistantName + '，请问有什么可以帮您？'
    : 'Hi, this is ' + assistantName + '. How can I help you today?';
  var welcome = script.getAttribute('data-welcome') || defaultWelcome;
  var buttonLabel = script.getAttribute('data-button-label') || 'Chat with us';
  var closeLabel = script.getAttribute('data-close-label') || 'Close chat';
  var storageKey = 'nexusdesk:webchat:' + apiBase + ':' + tenantKey + ':' + channelKey;
  var state = { conversationId: null, visitorToken: null, open: false, busy: false, pollTimer: null, messages: [] };

  try {
    var cached = JSON.parse(window.localStorage.getItem(storageKey) || '{}');
    state.conversationId = cached.conversationId || null;
    state.visitorToken = cached.visitorToken || null;
  } catch (err) {}

  var style = document.createElement('style');
  style.textContent = '\n'
    + '.nd-webchat-button,.nd-webchat-panel,.nd-webchat-panel *{box-sizing:border-box}\n'
    + '.nd-webchat-button{position:fixed;right:22px;bottom:22px;z-index:2147483000;border:0;border-radius:999px;background:#101828;color:#fff;padding:12px 17px;font:650 14px/20px system-ui,-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;box-shadow:0 14px 34px rgba(15,23,42,.24);cursor:pointer}\n'
    + '.nd-webchat-panel{position:fixed;right:22px;bottom:82px;z-index:2147483000;width:380px;max-width:calc(100vw - 32px);height:590px;max-height:calc(100dvh - 112px);display:none;flex-direction:column;background:#fff;border:1px solid #e5e7eb;border-radius:22px;box-shadow:0 26px 70px rgba(15,23,42,.24);overflow:hidden;font:14px/1.45 system-ui,-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;color:#101828}\n'
    + '.nd-webchat-panel[data-open=true]{display:flex}\n'
    + '.nd-webchat-header{flex:0 0 auto;padding:14px 16px;background:#101828;color:#fff}\n'
    + '.nd-webchat-header strong{display:block;font-size:17px;line-height:22px;font-weight:760}\n'
    + '.nd-webchat-header span{display:block;opacity:.86;font-size:12.5px;line-height:18px;margin-top:3px;font-weight:520}\n'
    + '.nd-webchat-messages{flex:1 1 auto;min-height:0;overflow-y:auto;overflow-x:hidden;padding:14px;background:#f7f8fb}\n'
    + '.nd-webchat-msg{max-width:84%;margin:0 0 10px;padding:10px 12px;border-radius:16px;font-size:14.5px;line-height:1.46;white-space:pre-wrap;overflow-wrap:anywhere}\n'
    + '.nd-webchat-msg.visitor{margin-left:auto;background:#101828;color:#fff;border-bottom-right-radius:6px}\n'
    + '.nd-webchat-msg.agent,.nd-webchat-msg.system{margin-right:auto;background:#fff;color:#101828;border:1px solid #e3e7ee;border-bottom-left-radius:6px}\n'
    + '.nd-webchat-form{flex:0 0 auto;display:flex;align-items:center;gap:9px;padding:12px 14px;border-top:1px solid #edf0f4;background:#fff}\n'
    + '.nd-webchat-input{flex:1;min-width:0;height:44px;border:1px solid #d0d5dd;border-radius:15px;padding:0 14px;background:#fff;color:#101828;font:500 14.5px system-ui,-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;outline:none}\n'
    + '.nd-webchat-send{flex:0 0 auto;height:44px;min-width:74px;border:0;border-radius:15px;background:#101828;color:#fff;padding:0 16px;font:720 14px system-ui,-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;cursor:pointer}\n'
    + '.nd-webchat-send:disabled{opacity:.58;cursor:not-allowed}\n'
    + '.nd-webchat-status{flex:0 0 auto;padding:7px 15px;font-size:12.5px;font-weight:560;color:#667085;border-top:1px solid #f2f4f7;background:#fff}\n'
    + '@media (max-width:480px){.nd-webchat-panel{left:12px;right:12px;bottom:76px;width:auto;height:min(620px,calc(100dvh - 104px));max-height:calc(100dvh - 104px)}.nd-webchat-button{right:16px;bottom:16px}}\n';
  document.head.appendChild(style);

  var button = document.createElement('button');
  button.className = 'nd-webchat-button';
  button.type = 'button';
  button.textContent = buttonLabel;

  var panel = document.createElement('section');
  panel.className = 'nd-webchat-panel';
  panel.setAttribute('aria-label', title);
  panel.innerHTML = ''
    + '<div class="nd-webchat-header"><strong></strong><span></span></div>'
    + '<div class="nd-webchat-messages" role="log" aria-live="polite"></div>'
    + '<form class="nd-webchat-form"><input class="nd-webchat-input" maxlength="2000" placeholder="Type your message..." autocomplete="off" /><button class="nd-webchat-send" type="submit">Send</button></form>'
    + '<div class="nd-webchat-status">Online</div>';
  panel.querySelector('.nd-webchat-header strong').textContent = title;
  panel.querySelector('.nd-webchat-header span').textContent = subtitle;

  document.body.appendChild(panel);
  document.body.appendChild(button);

  var messagesEl = panel.querySelector('.nd-webchat-messages');
  var inputEl = panel.querySelector('.nd-webchat-input');
  var formEl = panel.querySelector('.nd-webchat-form');
  var sendEl = panel.querySelector('.nd-webchat-send');
  var statusEl = panel.querySelector('.nd-webchat-status');

  function setStatus(text) { statusEl.textContent = text; }
  function persist() { window.localStorage.setItem(storageKey, JSON.stringify({ conversationId: state.conversationId, visitorToken: state.visitorToken })); }
  function api(path, options) {
    options = options || {};
    var headers = Object.assign({ 'Content-Type': 'application/json' }, options.headers || {});
    return fetch(apiBase + path, Object.assign({ mode: 'cors' }, options, { headers: headers }))
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
    var headers = state.visitorToken ? { 'X-Webchat-Visitor-Token': state.visitorToken } : {};
    setStatus('Connecting...');
    return api('/api/webchat/init', { method: 'POST', headers: headers, body: JSON.stringify({
      tenant_key: tenantKey,
      channel_key: channelKey,
      conversation_id: state.conversationId,
      origin: window.location.origin,
      page_url: window.location.href
    })}).then(function (data) {
      state.conversationId = data.conversation_id;
      state.visitorToken = data.visitor_token;
      persist();
      setStatus('Online');
      return poll();
    }).catch(function () { setStatus('Temporarily unavailable'); });
  }
  function poll() {
    if (!state.conversationId || !state.visitorToken) return Promise.resolve();
    return api('/api/webchat/conversations/' + encodeURIComponent(state.conversationId) + '/messages', {
      headers: { 'X-Webchat-Visitor-Token': state.visitorToken }
    }).then(function (data) { state.messages = data.messages || []; render(); setStatus('Online'); })
      .catch(function () { setStatus('Reconnecting...'); });
  }
  function startPolling() {
    if (state.pollTimer) clearInterval(state.pollTimer);
    state.pollTimer = setInterval(function () { if (state.open) poll(); }, 4000);
  }
  function openPanel() {
    state.open = !state.open;
    panel.setAttribute('data-open', state.open ? 'true' : 'false');
    if (state.open) {
      button.textContent = closeLabel;
      init().then(startPolling);
      setTimeout(function () { inputEl.focus(); }, 80);
    } else {
      button.textContent = buttonLabel;
    }
  }
  button.addEventListener('click', openPanel);
  formEl.addEventListener('submit', function (event) {
    event.preventDefault();
    var body = inputEl.value.trim();
    if (!body || state.busy) return;
    state.busy = true;
    sendEl.disabled = true;
    setStatus('Sending...');
    init().then(function () {
      return api('/api/webchat/conversations/' + encodeURIComponent(state.conversationId) + '/messages', {
        method: 'POST',
        headers: { 'X-Webchat-Visitor-Token': state.visitorToken },
        body: JSON.stringify({ body: body })
      });
    }).then(function () {
      inputEl.value = '';
      setStatus('Sent');
      return poll();
    }).catch(function () {
      setStatus('Failed to send');
    }).finally(function () { state.busy = false; sendEl.disabled = false; });
  });
  render();
})();

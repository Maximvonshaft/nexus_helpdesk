(function () {
  'use strict';
  if (window.__NEXUSDESK_WEBCHAT_LOADED__) return;
  window.__NEXUSDESK_WEBCHAT_LOADED__ = true;

  var script = document.currentScript || (function () {
    var scripts = document.getElementsByTagName('script');
    return scripts[scripts.length - 1];
  })();

  var scriptUrl = new URL(script.src, window.location.href);
  var apiBase = (script.getAttribute('data-api-base') || scriptUrl.origin).replace(/\/$/, '');
  var tenantKey = script.getAttribute('data-tenant') || 'default';
  var channelKey = script.getAttribute('data-channel') || 'default';
  var title = script.getAttribute('data-title') || 'Speedaf Support';
  var subtitle = script.getAttribute('data-subtitle') || 'Secure website support';
  var assistantName = script.getAttribute('data-assistant-name') || 'Speedy';
  var locale = (script.getAttribute('data-locale') || navigator.language || 'en').toLowerCase();
  var defaultWelcome = locale.indexOf('zh') === 0
    ? '您好，我是 ' + assistantName + '，请问有什么可以帮您？'
    : 'Hi, this is ' + assistantName + '. How can I help you today?';
  var welcome = script.getAttribute('data-welcome') || defaultWelcome;
  var buttonLabel = script.getAttribute('data-button-label') || 'Chat with us';
  var closeLabel = script.getAttribute('data-close-label') || 'Close chat';
  var storageKey = 'nexusdesk:webchat:' + apiBase + ':' + tenantKey + ':' + channelKey;
  var state = { conversationId: null, visitorToken: null, open: false, busy: false, pollTimer: null, lastMessageId: 0, backoffMs: 4000, rendered: {}, optimisticSeq: 0, unread: 0, userNearBottom: true, pendingBodies: {} };

  function loadCache() { try { var cached = JSON.parse(window.sessionStorage.getItem(storageKey) || '{}'); state.conversationId = cached.conversationId || null; state.visitorToken = cached.visitorToken || null; state.lastMessageId = 0; } catch (err) {} }
  function persist() { try { window.sessionStorage.setItem(storageKey, JSON.stringify({ conversationId: state.conversationId, visitorToken: state.visitorToken })); } catch (err) {} }
  loadCache();

  var style = document.createElement('style');
  style.textContent = '\n'
    + '.nd-webchat-button,.nd-webchat-panel,.nd-webchat-panel *{box-sizing:border-box}\n'
    + '.nd-webchat-button{position:fixed;right:22px;bottom:22px;z-index:2147483000;border:0;border-radius:999px;background:#101828;color:#fff;padding:12px 17px;font:650 14px/20px system-ui,-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;box-shadow:0 14px 34px rgba(15,23,42,.24);cursor:pointer}\n'
    + '.nd-webchat-unread{position:absolute;right:2px;top:0;min-width:18px;height:18px;border-radius:999px;background:#ef4444;color:#fff;font:700 11px/18px system-ui;text-align:center;display:none}\n'
    + '.nd-webchat-panel{position:fixed;right:22px;bottom:82px;z-index:2147483000;width:388px;max-width:calc(100vw - 32px);height:610px;max-height:calc(100dvh - 112px);display:none;flex-direction:column;background:#fff;border:1px solid #e5e7eb;border-radius:22px;box-shadow:0 26px 70px rgba(15,23,42,.24);overflow:hidden;font:14px/1.45 system-ui,-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;color:#101828}\n'
    + '.nd-webchat-panel[data-open=true]{display:flex}\n'
    + '.nd-webchat-header{flex:0 0 auto;display:flex;align-items:flex-start;justify-content:space-between;gap:12px;padding:14px 16px;background:#101828;color:#fff}\n'
    + '.nd-webchat-header strong{display:block;font-size:17px;line-height:22px;font-weight:760}.nd-webchat-header span{display:block;opacity:.86;font-size:12.5px;line-height:18px;margin-top:3px;font-weight:520}\n'
    + '.nd-webchat-close{border:0;background:rgba(255,255,255,.12);color:#fff;border-radius:10px;width:32px;height:32px;cursor:pointer;font-size:18px}\n'
    + '.nd-webchat-messages{flex:1 1 auto;min-height:0;overflow-y:auto;overflow-x:hidden;padding:14px;background:#f7f8fb}\n'
    + '.nd-webchat-msg{max-width:84%;margin:0 0 10px;padding:10px 12px;border-radius:16px;font-size:14.5px;line-height:1.46;white-space:pre-wrap;overflow-wrap:anywhere}\n'
    + '.nd-webchat-msg.visitor{margin-left:auto;background:#101828;color:#fff;border-bottom-right-radius:6px}.nd-webchat-msg.agent,.nd-webchat-msg.system,.nd-webchat-msg.action{margin-right:auto;background:#fff;color:#101828;border:1px solid #e3e7ee;border-bottom-left-radius:6px}\n'
    + '.nd-webchat-msg.failed{outline:2px solid #fca5a5}.nd-webchat-msg.sending{opacity:.72}\n'
    + '.nd-webchat-retry{display:block;margin-top:7px;border:1px solid #fff;background:rgba(255,255,255,.12);color:#fff;border-radius:999px;padding:5px 9px;font:700 12px system-ui;cursor:pointer}.nd-webchat-retry:disabled{opacity:.6;cursor:not-allowed}\n'
    + '.nd-webchat-card{max-width:92%;margin:0 0 12px;padding:12px;border:1px solid #d7dde8;border-radius:18px;background:#fff;box-shadow:0 8px 20px rgba(15,23,42,.06)}\n'
    + '.nd-webchat-card-title{font-weight:760;font-size:14.5px;margin-bottom:5px}.nd-webchat-card-body{font-size:13.5px;color:#475467;margin-bottom:10px;white-space:pre-wrap;overflow-wrap:anywhere}.nd-webchat-card-actions{display:flex;flex-wrap:wrap;gap:8px}.nd-webchat-card-action{border:1px solid #101828;background:#fff;color:#101828;border-radius:999px;padding:8px 10px;font:700 12.5px system-ui;cursor:pointer}.nd-webchat-card-action:disabled{opacity:.55;cursor:not-allowed}\n'
    + '.nd-webchat-form{flex:0 0 auto;display:flex;align-items:center;gap:9px;padding:12px 14px;border-top:1px solid #edf0f4;background:#fff;padding-bottom:max(12px,env(safe-area-inset-bottom))}\n'
    + '.nd-webchat-input{flex:1;min-width:0;height:44px;border:1px solid #d0d5dd;border-radius:15px;padding:0 14px;background:#fff;color:#101828;font:500 14.5px system-ui,-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;outline:none}\n'
    + '.nd-webchat-send{flex:0 0 auto;height:44px;min-width:74px;border:0;border-radius:15px;background:#101828;color:#fff;padding:0 16px;font:720 14px system-ui,-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;cursor:pointer}.nd-webchat-send:disabled{opacity:.58;cursor:not-allowed}\n'
    + '.nd-webchat-status{flex:0 0 auto;padding:7px 15px;font-size:12.5px;font-weight:560;color:#667085;border-top:1px solid #f2f4f7;background:#fff}\n'
    + '@media (max-width:480px){.nd-webchat-panel{left:0;right:0;bottom:0;width:100vw;height:100dvh;max-height:100dvh;max-width:100vw;border-radius:0}.nd-webchat-button{right:16px;bottom:16px}}\n';
  document.head.appendChild(style);

  var button = document.createElement('button'); button.className = 'nd-webchat-button'; button.type = 'button'; button.setAttribute('aria-label', buttonLabel);
  var buttonText = document.createElement('span'); buttonText.textContent = buttonLabel;
  var unread = document.createElement('span'); unread.className = 'nd-webchat-unread'; button.appendChild(buttonText); button.appendChild(unread);
  var panel = document.createElement('section'); panel.className = 'nd-webchat-panel'; panel.setAttribute('aria-label', title);
  var header = document.createElement('div'); header.className = 'nd-webchat-header'; var headerText = document.createElement('div');
  var h = document.createElement('strong'); h.textContent = title; var s = document.createElement('span'); s.textContent = subtitle; headerText.appendChild(h); headerText.appendChild(s);
  var close = document.createElement('button'); close.className = 'nd-webchat-close'; close.type = 'button'; close.setAttribute('aria-label', closeLabel); close.textContent = '×'; header.appendChild(headerText); header.appendChild(close);
  var messagesEl = document.createElement('div'); messagesEl.className = 'nd-webchat-messages'; messagesEl.setAttribute('role', 'log'); messagesEl.setAttribute('aria-live', 'polite');
  var formEl = document.createElement('form'); formEl.className = 'nd-webchat-form'; var inputEl = document.createElement('input'); inputEl.className = 'nd-webchat-input'; inputEl.maxLength = 2000; inputEl.placeholder = 'Type your message...'; inputEl.autocomplete = 'off';
  var sendEl = document.createElement('button'); sendEl.className = 'nd-webchat-send'; sendEl.type = 'submit'; sendEl.textContent = 'Send'; formEl.appendChild(inputEl); formEl.appendChild(sendEl);
  var statusEl = document.createElement('div'); statusEl.className = 'nd-webchat-status'; statusEl.textContent = 'Online'; panel.appendChild(header); panel.appendChild(messagesEl); panel.appendChild(formEl); panel.appendChild(statusEl); document.body.appendChild(panel); document.body.appendChild(button);

  function setStatus(text) { statusEl.textContent = text; panel.setAttribute('data-status', text.toLowerCase().replace(/\s+/g, '-')); }
  function updateUnread() { unread.style.display = state.unread > 0 ? 'block' : 'none'; unread.textContent = String(Math.min(state.unread, 9)); }
  function isNearBottom() { return messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < 80; }
  messagesEl.addEventListener('scroll', function () { state.userNearBottom = isNearBottom(); });
  function api(path, options, timeoutMs) { options = options || {}; timeoutMs = timeoutMs || 12000; var headers = Object.assign({ 'Content-Type': 'application/json' }, options.headers || {}); var controller = window.AbortController ? new AbortController() : null; var timer = controller ? setTimeout(function () { controller.abort(); }, timeoutMs) : null; return fetch(apiBase + path, Object.assign({ mode: 'cors', signal: controller ? controller.signal : undefined }, options, { headers: headers })).then(function (res) { return res.json().catch(function () { return {}; }).then(function (data) { if (!res.ok) { var err = new Error(data.detail && data.detail.message ? data.detail.message : data.detail || ('HTTP ' + res.status)); err.payload = data; throw err; } return data; }); }).finally(function () { if (timer) clearTimeout(timer); }); }
  function clientMessageId() { return 'wc_client_' + Date.now().toString(36) + '_' + (++state.optimisticSeq).toString(36); }
  function renderWelcomeIfEmpty() { if (Object.keys(state.rendered).length) return; var w = document.createElement('div'); w.className = 'nd-webchat-msg agent'; w.textContent = welcome; messagesEl.appendChild(w); }
  function appendTextMessage(msg, extraClass) { var key = msg.id ? String(msg.id) : msg.client_message_id; if (key && state.rendered[key]) return; var el = document.createElement('div'); var role = msg.direction === 'visitor' ? 'visitor' : msg.direction === 'action' ? 'action' : msg.direction === 'system' ? 'system' : 'agent'; el.className = 'nd-webchat-msg ' + role + (extraClass ? ' ' + extraClass : ''); el.textContent = msg.body_text || msg.body || ''; if (key) state.rendered[key] = el; messagesEl.appendChild(el); }
  function renderUnknownCard(card) { var text = (card && (card.title || card.body)) || 'Unsupported card. Please type your request.'; var el = document.createElement('div'); el.className = 'nd-webchat-msg system'; el.textContent = text; messagesEl.appendChild(el); }
  function appendCardMessage(msg) { var key = String(msg.id); if (state.rendered[key]) return; var payload = msg.payload_json || {}; if (!payload || ['quick_replies', 'handoff'].indexOf(payload.card_type) === -1) { renderUnknownCard(payload); state.rendered[key] = true; return; } var card = document.createElement('div'); card.className = 'nd-webchat-card'; var titleEl = document.createElement('div'); titleEl.className = 'nd-webchat-card-title'; titleEl.textContent = payload.title || 'Support options'; card.appendChild(titleEl); if (payload.body) { var bodyEl = document.createElement('div'); bodyEl.className = 'nd-webchat-card-body'; bodyEl.textContent = payload.body; card.appendChild(bodyEl); } var actionsEl = document.createElement('div'); actionsEl.className = 'nd-webchat-card-actions'; card.appendChild(actionsEl); (payload.actions || []).forEach(function (action) { var a = document.createElement('button'); a.type = 'button'; a.className = 'nd-webchat-card-action'; a.textContent = action.label || action.id; a.disabled = msg.action_status === 'submitted'; a.addEventListener('click', function () { submitAction(msg, payload, action, a); }); actionsEl.appendChild(a); }); state.rendered[key] = card; messagesEl.appendChild(card); }
  function appendMessages(messages) { var wasNearBottom = state.userNearBottom; renderWelcomeIfEmpty(); (messages || []).forEach(function (msg) { if (msg.id && msg.id > state.lastMessageId) state.lastMessageId = msg.id; if (msg.message_type === 'card') appendCardMessage(msg); else appendTextMessage(msg); if (!state.open && msg.direction !== 'visitor') state.unread += 1; }); persist(); updateUnread(); if (wasNearBottom || state.open) messagesEl.scrollTop = messagesEl.scrollHeight; }
  function init() { var headers = state.visitorToken ? { 'X-Webchat-Visitor-Token': state.visitorToken } : {}; setStatus('Connecting...'); return api('/api/webchat/init', { method: 'POST', headers: headers, body: JSON.stringify({ tenant_key: tenantKey, channel_key: channelKey, conversation_id: state.conversationId, origin: window.location.origin, page_url: window.location.href })}, 12000).then(function (data) { state.conversationId = data.conversation_id; state.visitorToken = data.visitor_token; persist(); setStatus('Online'); return poll(true); }).catch(function () { setStatus('Temporarily unavailable'); }); }
  function poll(resetBackoff) { if (!state.conversationId || !state.visitorToken) return Promise.resolve(); var qs = '?limit=50'; if (state.lastMessageId) qs += '&after_id=' + encodeURIComponent(state.lastMessageId); return api('/api/webchat/conversations/' + encodeURIComponent(state.conversationId) + '/messages' + qs, { headers: { 'X-Webchat-Visitor-Token': state.visitorToken } }, 10000).then(function (data) { appendMessages(data.messages || []); setStatus('Online'); if (resetBackoff !== false) state.backoffMs = 4000; }).catch(function () { setStatus('Reconnecting...'); state.backoffMs = Math.min(30000, Math.max(4000, state.backoffMs * 1.6)); }); }
  function schedulePoll() { if (state.pollTimer) clearTimeout(state.pollTimer); state.pollTimer = setTimeout(function tick() { if (state.open && document.visibilityState !== 'hidden') poll(false).finally(schedulePoll); else schedulePoll(); }, document.visibilityState === 'hidden' ? Math.max(state.backoffMs, 15000) : state.backoffMs); }
  function openPanel(force) { state.open = typeof force === 'boolean' ? force : !state.open; panel.setAttribute('data-open', state.open ? 'true' : 'false'); if (state.open) { state.unread = 0; updateUnread(); buttonText.textContent = closeLabel; init().then(schedulePoll); setTimeout(function () { inputEl.focus(); }, 80); } else { buttonText.textContent = buttonLabel; } }
  function submitAction(msg, payload, action, buttonEl) { if (!state.conversationId || !state.visitorToken || buttonEl.disabled) return; buttonEl.disabled = true; setStatus('Sending selection...'); api('/api/webchat/conversations/' + encodeURIComponent(state.conversationId) + '/actions', { method: 'POST', headers: { 'X-Webchat-Visitor-Token': state.visitorToken }, body: JSON.stringify({ message_id: msg.id, card_id: payload.card_id, action_id: action.id, action_type: action.action_type || 'quick_reply', payload: action.payload || {} }) }, 12000).then(function (data) { appendMessages([data.message]); setStatus(data.handoff_triggered ? 'Human support requested' : 'Selection sent'); return poll(true); }).catch(function () { buttonEl.disabled = false; setStatus('Failed to send selection'); }); }
  function markFailed(cmid, body) { var optimistic = state.rendered[cmid]; if (!optimistic) return; optimistic.className = optimistic.className.replace('sending', 'failed'); if (optimistic.querySelector('.nd-webchat-retry')) return; var retry = document.createElement('button'); retry.type = 'button'; retry.className = 'nd-webchat-retry'; retry.textContent = 'Retry'; retry.addEventListener('click', function () { retry.disabled = true; resendMessage(body, cmid); }); optimistic.appendChild(retry); }
  function markSending(cmid) { var optimistic = state.rendered[cmid]; if (!optimistic) return; optimistic.className = optimistic.className.replace('failed', 'sending'); var retry = optimistic.querySelector('.nd-webchat-retry'); if (retry) retry.remove(); }
  function markSent(cmid) { var optimistic = state.rendered[cmid]; if (!optimistic) return; optimistic.className = optimistic.className.replace('sending', '').replace('failed', ''); var retry = optimistic.querySelector('.nd-webchat-retry'); if (retry) retry.remove(); }
  function sendMessage(body, cmid) { state.pendingBodies[cmid] = body; return init().then(function () { return api('/api/webchat/conversations/' + encodeURIComponent(state.conversationId) + '/messages', { method: 'POST', headers: { 'X-Webchat-Visitor-Token': state.visitorToken }, body: JSON.stringify({ body: body, client_message_id: cmid }) }); }).then(function (data) { inputEl.value = ''; setStatus(data.idempotent ? 'Already sent' : 'Sent'); markSent(cmid); delete state.pendingBodies[cmid]; if (data.message && data.message.id) state.lastMessageId = Math.max(state.lastMessageId, data.message.id); return poll(true); }).catch(function () { markFailed(cmid, body); setStatus('Failed to send. Please retry.'); throw new Error('send_failed'); }); }
  function resendMessage(body, cmid) { markSending(cmid); state.busy = true; sendEl.disabled = true; sendMessage(body, cmid).catch(function () {}).finally(function () { state.busy = false; sendEl.disabled = false; }); }

  button.addEventListener('click', function () { openPanel(); }); close.addEventListener('click', function () { openPanel(false); });
  formEl.addEventListener('submit', function (event) { event.preventDefault(); var body = inputEl.value.trim(); if (!body || state.busy) return; var cmid = clientMessageId(); state.busy = true; sendEl.disabled = true; setStatus('Sending...'); appendTextMessage({ direction: 'visitor', body: body, body_text: body, client_message_id: cmid }, 'sending'); sendMessage(body, cmid).catch(function () {}).finally(function () { state.busy = false; sendEl.disabled = false; }); });
  document.addEventListener('visibilitychange', schedulePoll); renderWelcomeIfEmpty();
})();

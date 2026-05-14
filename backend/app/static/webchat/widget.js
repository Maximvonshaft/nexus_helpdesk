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
  var channelKey = script.getAttribute('data-channel') || 'website';
  var mode = (script.getAttribute('data-webchat-mode') || 'fast_ai').toLowerCase();
  var title = script.getAttribute('data-title') || 'Speedaf Support';
  var subtitle = script.getAttribute('data-subtitle') || (mode === 'legacy' ? 'Secure website support' : 'AI support · fast reply');
  var assistantName = script.getAttribute('data-assistant-name') || 'Speedy';
  var locale = (script.getAttribute('data-locale') || navigator.language || 'en').toLowerCase();
  var welcome = script.getAttribute('data-welcome') || (locale.indexOf('zh') === 0 ? '您好，我是 ' + assistantName + '，Speedaf AI 客服。您可以输入运单号或选择下方服务。' : 'Hi, this is ' + assistantName + ', your Speedaf AI assistant. Enter a tracking number or choose a service below.');
  var buttonLabel = script.getAttribute('data-button-label') || 'Chat with Speedaf';
  var closeLabel = script.getAttribute('data-close-label') || 'Close chat';
  var storageKey = 'nexusdesk:webchat:' + apiBase + ':' + tenantKey + ':' + channelKey + ':' + mode;
  var contextKey = storageKey + ':recent-context';
  var sessionKey = storageKey + ':session-id';
  var MAX_CONTEXT_TURNS = 5;

  var state = {
    open: false,
    busy: false,
    composing: false,
    unread: 0,
    userNearBottom: true,
    optimisticSeq: 0,
    typingEl: null,
    sessionId: loadSessionId(),
    recentContext: loadRecentContext(),
    legacyConversationId: null,
    legacyVisitorToken: null,
    legacyLastMessageId: 0,
    legacyPollTimer: null,
    rendered: {}
  };

  var QUICK_ACTIONS = [
    ['speedaf-webchat-action-track', 'Track my parcel', 'I want to track my parcel. My tracking number is '],
    ['speedaf-webchat-action-redelivery', 'Redelivery', 'I need help arranging redelivery for my parcel.'],
    ['speedaf-webchat-action-refuse', 'Refuse delivery', 'I want to refuse delivery for this parcel.'],
    ['speedaf-webchat-action-problem', 'Delivery problem', 'I have a delivery problem and need help checking it.'],
    ['speedaf-webchat-action-human', 'Talk to human', 'Please connect me to a human support specialist.']
  ];

  var style = document.createElement('style');
  style.textContent = '\n'
    + '.nd-webchat-button,.nd-webchat-panel,.nd-webchat-panel *{box-sizing:border-box}\n'
    + '.nd-webchat-button{position:fixed;right:22px;bottom:22px;z-index:2147483000;border:0;border-radius:999px;background:#f97316;color:#fff;padding:13px 18px;font:750 14px/20px system-ui,-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;box-shadow:0 18px 42px rgba(249,115,22,.32);cursor:pointer;display:inline-flex;align-items:center;gap:10px;transition:transform .18s ease,box-shadow .18s ease}\n'
    + '.nd-webchat-button:before{content:"";width:10px;height:10px;border-radius:999px;background:#fff;box-shadow:0 0 0 5px rgba(255,255,255,.22)}.nd-webchat-button:hover{transform:translateY(-2px);box-shadow:0 22px 52px rgba(249,115,22,.38)}\n'
    + '.nd-webchat-unread{position:absolute;right:2px;top:0;min-width:18px;height:18px;border-radius:999px;background:#ef4444;color:#fff;font:700 11px/18px system-ui;text-align:center;display:none}\n'
    + '.nd-webchat-panel{position:fixed;right:22px;bottom:82px;z-index:2147483000;width:408px;max-width:calc(100vw - 32px);height:650px;max-height:calc(100dvh - 112px);display:flex;flex-direction:column;background:#fff;border:1px solid #e5e7eb;border-radius:24px;box-shadow:0 28px 76px rgba(15,23,42,.24);overflow:hidden;font:14px/1.45 system-ui,-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;color:#101828;opacity:0;pointer-events:none;transform:translateY(14px) scale(.98);transition:opacity .2s ease,transform .2s ease}\n'
    + '.nd-webchat-panel[data-open=true]{opacity:1;pointer-events:auto;transform:translateY(0) scale(1)}\n'
    + '.nd-webchat-header{flex:0 0 auto;display:flex;align-items:flex-start;justify-content:space-between;gap:12px;padding:15px 16px;background:linear-gradient(135deg,#f97316 0%,#fb923c 100%);color:#fff}\n'
    + '.nd-webchat-brand{display:flex;align-items:center;gap:11px;min-width:0}.nd-webchat-avatar{width:40px;height:40px;border-radius:14px;background:#fff;color:#f97316;display:grid;place-items:center;font:900 18px/1 system-ui}\n'
    + '.nd-webchat-header strong{display:block;font-size:17px;line-height:22px;font-weight:800}.nd-webchat-header span{display:block;opacity:.92;font-size:12.5px;line-height:18px;margin-top:3px;font-weight:560}\n'
    + '.nd-webchat-online{margin-top:6px;display:inline-flex!important;align-items:center;gap:6px;width:max-content;padding:3px 8px;border-radius:999px;background:rgba(255,255,255,.18);font-size:11.5px!important;line-height:15px!important}.nd-webchat-online:before{content:"";width:7px;height:7px;border-radius:999px;background:#12b76a}\n'
    + '.nd-webchat-close{border:0;background:rgba(255,255,255,.16);color:#fff;border-radius:12px;width:34px;height:34px;cursor:pointer;font-size:20px;line-height:34px}\n'
    + '.nd-webchat-messages{flex:1 1 auto;min-height:0;overflow-y:auto;overflow-x:hidden;padding:14px;background:#f7f8fb}.nd-webchat-welcome{margin:0 0 12px;padding:13px;border:1px solid #fed7aa;background:#fff7ed;border-radius:18px;color:#9a3412;font-size:13.5px}\n'
    + '.nd-webchat-quick-actions{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:0 0 12px}.nd-webchat-action{border:1px solid #e5e7eb;background:#fff;border-radius:15px;padding:10px;text-align:left;color:#101828;font:750 13px/17px system-ui;cursor:pointer;box-shadow:0 4px 12px rgba(15,23,42,.04)}.nd-webchat-action:hover{border-color:#fdba74;background:#fff7ed}.nd-webchat-action:nth-child(5){grid-column:1/-1}\n'
    + '.nd-webchat-card{margin:0 0 10px;padding:11px 12px;background:#fff;border:1px solid #e5e7eb;border-left:4px solid #f97316;border-radius:16px;color:#101828}.nd-webchat-card strong{display:block;font-size:13.5px;margin-bottom:4px}.nd-webchat-card p{margin:0;color:#475467;font-size:12.8px}\n'
    + '.nd-webchat-msg{max-width:84%;margin:0 0 10px;padding:10px 12px;border-radius:16px;font-size:14.5px;line-height:1.46;white-space:pre-wrap;overflow-wrap:anywhere}\n'
    + '.nd-webchat-msg.visitor{margin-left:auto;background:#101828;color:#fff;border-bottom-right-radius:6px}.nd-webchat-msg.agent,.nd-webchat-msg.system{margin-right:auto;background:#fff;color:#101828;border:1px solid #e3e7ee;border-bottom-left-radius:6px}\n'
    + '.nd-webchat-msg.sending{opacity:.72}.nd-webchat-msg.failed{outline:2px solid #fca5a5}\n'
    + '.nd-webchat-typing{max-width:84%;margin:0 0 10px;padding:10px 12px;border-radius:16px;border-bottom-left-radius:6px;background:#fff;border:1px solid #e3e7ee;display:inline-flex;align-items:center;gap:4px}.nd-webchat-typing-dot{width:6px;height:6px;border-radius:999px;background:#98a2b3;animation:ndTypingBounce 1.1s infinite ease-in-out}.nd-webchat-typing-dot:nth-child(2){animation-delay:.15s}.nd-webchat-typing-dot:nth-child(3){animation-delay:.3s}\n'
    + '@keyframes ndTypingBounce{0%,80%,100%{transform:translateY(0);opacity:.45}40%{transform:translateY(-4px);opacity:1}}\n'
    + '.nd-webchat-retry{display:block;margin-top:7px;border:1px solid currentColor;background:transparent;color:inherit;border-radius:999px;padding:5px 9px;font:700 12px system-ui;cursor:pointer}.nd-webchat-retry:disabled{opacity:.6;cursor:not-allowed}\n'
    + '.nd-webchat-form{flex:0 0 auto;display:flex;align-items:center;gap:8px;padding:11px 13px;border-top:1px solid #edf0f4;background:#fff;padding-bottom:max(11px,env(safe-area-inset-bottom))}.nd-webchat-input{flex:1;min-width:0;height:44px;border:1px solid #d0d5dd;border-radius:16px;padding:0 13px;background:#fff;color:#101828;font:500 14.5px system-ui;outline:none}.nd-webchat-input:focus{border-color:#f97316;box-shadow:0 0 0 3px rgba(249,115,22,.14)}\n'
    + '.nd-webchat-attach{flex:0 0 auto;width:42px;height:42px;border:1px solid #e5e7eb;border-radius:14px;background:#fff;color:#667085;font:800 18px/40px system-ui;cursor:pointer}.nd-webchat-send{flex:0 0 auto;width:44px;height:44px;border:0;border-radius:999px;background:#f97316;color:#fff;padding:0;font:900 18px/44px system-ui;cursor:pointer;box-shadow:0 10px 22px rgba(249,115,22,.24)}.nd-webchat-send:disabled{opacity:.58;cursor:not-allowed}\n'
    + '.nd-webchat-status{flex:0 0 auto;display:flex;align-items:center;justify-content:space-between;gap:10px;padding:7px 14px;font-size:12.5px;font-weight:560;color:#667085;border-top:1px solid #f2f4f7;background:#fff}.nd-webchat-safety{color:#98a2b3;font-size:11.5px;white-space:nowrap}\n'
    + '.nd-webcall-cta{margin:0 0 12px;border:1px solid #fed7aa;background:#fff;color:#f97316;border-radius:16px;padding:10px 12px;display:flex;align-items:center;justify-content:space-between;gap:10px;font:800 13px/17px system-ui}.nd-webcall-cta button{border:0;border-radius:999px;background:#f97316;color:#fff;padding:7px 11px;font:800 12px system-ui;cursor:pointer}\n'
    + '@media (max-width:480px){.nd-webchat-panel{left:0;right:0;bottom:0;width:100vw;height:100dvh;max-height:100dvh;max-width:100vw;border-radius:0}.nd-webchat-button{right:16px;bottom:16px}.nd-webchat-button[data-open=true]{display:none}.nd-webchat-form{padding-right:max(13px,env(safe-area-inset-right));padding-left:max(13px,env(safe-area-inset-left))}.nd-webchat-quick-actions{grid-template-columns:1fr}.nd-webchat-safety{display:none}}\n';
  document.head.appendChild(style);

  var button = document.createElement('button');
  button.className = 'nd-webchat-button';
  button.type = 'button';
  button.setAttribute('aria-label', buttonLabel);
  button.setAttribute('data-testid', 'speedaf-webchat-launcher');
  var buttonText = document.createElement('span');
  buttonText.textContent = buttonLabel;
  var unread = document.createElement('span');
  unread.className = 'nd-webchat-unread';
  button.appendChild(buttonText);
  button.appendChild(unread);

  var panel = document.createElement('section');
  panel.className = 'nd-webchat-panel';
  panel.setAttribute('aria-label', title);
  panel.setAttribute('data-testid', 'speedaf-webchat-panel');
  var header = document.createElement('div');
  header.className = 'nd-webchat-header';
  header.setAttribute('data-testid', 'speedaf-webchat-header');
  var brand = document.createElement('div');
  brand.className = 'nd-webchat-brand';
  var avatar = document.createElement('div');
  avatar.className = 'nd-webchat-avatar';
  avatar.setAttribute('data-testid', 'speedaf-webchat-avatar');
  avatar.textContent = 'S';
  var headerText = document.createElement('div');
  var h = document.createElement('strong');
  h.textContent = title;
  var s = document.createElement('span');
  s.textContent = subtitle;
  var online = document.createElement('span');
  online.className = 'nd-webchat-online';
  online.setAttribute('data-testid', 'speedaf-webchat-online-badge');
  online.textContent = 'Online';
  headerText.appendChild(h); headerText.appendChild(s); headerText.appendChild(online);
  brand.appendChild(avatar); brand.appendChild(headerText);
  var close = document.createElement('button');
  close.className = 'nd-webchat-close';
  close.type = 'button';
  close.setAttribute('aria-label', closeLabel);
  close.textContent = '×';
  header.appendChild(brand); header.appendChild(close);

  var messagesEl = document.createElement('div');
  messagesEl.className = 'nd-webchat-messages';
  messagesEl.setAttribute('role', 'log');
  messagesEl.setAttribute('aria-live', 'polite');
  var welcomeBox = document.createElement('div');
  welcomeBox.className = 'nd-webchat-welcome';
  welcomeBox.textContent = welcome;
  messagesEl.appendChild(welcomeBox);
  var quickActionsEl = document.createElement('div');
  quickActionsEl.className = 'nd-webchat-quick-actions';
  quickActionsEl.setAttribute('data-testid', 'speedaf-webchat-quick-actions');
  QUICK_ACTIONS.forEach(function (action) {
    var actionButton = document.createElement('button');
    actionButton.type = 'button';
    actionButton.className = 'nd-webchat-action';
    actionButton.textContent = action[1];
    actionButton.setAttribute('data-testid', action[0]);
    actionButton.addEventListener('click', function () { inputEl.value = action[2]; inputEl.focus(); renderActionCard(action[1]); });
    quickActionsEl.appendChild(actionButton);
  });
  messagesEl.appendChild(quickActionsEl);
  var webcallCta = document.createElement('div');
  webcallCta.className = 'nd-webcall-cta';
  webcallCta.setAttribute('data-testid', 'speedaf-webcall-cta');
  var webcallText = document.createElement('span');
  webcallText.textContent = 'WebCall voice support';
  var webcallButton = document.createElement('button');
  webcallButton.type = 'button';
  webcallButton.textContent = 'WebCall';
  webcallButton.addEventListener('click', function () { window.dispatchEvent(new CustomEvent('nexusdesk:webcall-requested', { detail: { tenant_key: tenantKey, channel_key: channelKey } })); });
  webcallCta.appendChild(webcallText); webcallCta.appendChild(webcallButton); messagesEl.appendChild(webcallCta);

  var formEl = document.createElement('form');
  formEl.className = 'nd-webchat-form';
  var attachEl = document.createElement('button');
  attachEl.className = 'nd-webchat-attach';
  attachEl.type = 'button';
  attachEl.title = 'Attach file';
  attachEl.setAttribute('aria-label', 'Attach file');
  attachEl.setAttribute('data-testid', 'speedaf-webchat-attachment');
  attachEl.textContent = '+';
  attachEl.addEventListener('click', function () { renderInfoCard('speedaf-ai-unavailable', 'Attachment upload', 'File upload is not available in this widget yet. Please describe the issue or share the tracking number.'); });
  var inputEl = document.createElement('input');
  inputEl.className = 'nd-webchat-input';
  inputEl.maxLength = 2000;
  inputEl.placeholder = script.getAttribute('data-input-placeholder') || 'Type tracking number or message...';
  inputEl.autocomplete = 'off';
  inputEl.setAttribute('data-testid', 'speedaf-webchat-input');
  var sendEl = document.createElement('button');
  sendEl.className = 'nd-webchat-send';
  sendEl.type = 'submit';
  sendEl.textContent = script.getAttribute('data-send-label') || '➜';
  sendEl.setAttribute('aria-label', 'Send message');
  sendEl.setAttribute('data-testid', 'speedaf-webchat-send');
  formEl.appendChild(attachEl); formEl.appendChild(inputEl); formEl.appendChild(sendEl);
  var statusEl = document.createElement('div');
  statusEl.className = 'nd-webchat-status';
  var statusTextEl = document.createElement('span');
  var safetyEl = document.createElement('span');
  safetyEl.className = 'nd-webchat-safety';
  safetyEl.setAttribute('data-testid', 'speedaf-webchat-safety-notice');
  safetyEl.textContent = 'Do not share passwords or payment codes.';
  statusEl.appendChild(statusTextEl); statusEl.appendChild(safetyEl);
  panel.appendChild(header); panel.appendChild(messagesEl); panel.appendChild(formEl); panel.appendChild(statusEl);
  document.body.appendChild(panel); document.body.appendChild(button);

  setStatus('Online');
  button.addEventListener('click', function () { openPanel(); });
  close.addEventListener('click', function () { openPanel(false); });
  inputEl.addEventListener('compositionstart', function () { state.composing = true; });
  inputEl.addEventListener('compositionend', function () { state.composing = false; });
  messagesEl.addEventListener('scroll', function () { state.userNearBottom = isNearBottom(); });
  formEl.addEventListener('submit', function (event) {
    event.preventDefault();
    if (state.composing || event.isComposing || state.busy) return;
    var body = inputEl.value.trim();
    if (!body) return;
    if (mode === 'legacy') sendLegacyMessage(body);
    else sendFastMessage(body);
  });
  if (mode === 'legacy') restoreLegacySession();

  function setStatus(text) { statusTextEl.textContent = text; panel.setAttribute('data-status', String(text || '').toLowerCase().replace(/\s+/g, '-')); }
  function openPanel(force) { state.open = typeof force === 'boolean' ? force : !state.open; panel.setAttribute('data-open', state.open ? 'true' : 'false'); button.setAttribute('data-open', state.open ? 'true' : 'false'); buttonText.textContent = state.open ? closeLabel : buttonLabel; if (state.open) { state.unread = 0; updateUnread(); setTimeout(function () { inputEl.focus(); }, 80); if (mode === 'legacy') ensureLegacySession().then(scheduleLegacyPoll); } }
  function renderInfoCard(testId, titleText, bodyText) { var card = document.createElement('div'); card.className = 'nd-webchat-card'; card.setAttribute('data-testid', testId); var strong = document.createElement('strong'); strong.textContent = titleText; var p = document.createElement('p'); p.textContent = bodyText; card.appendChild(strong); card.appendChild(p); messagesEl.appendChild(card); scrollToBottomIfNeeded(true); return card; }
  function renderActionCard(actionTitle) { if (actionTitle === 'Track my parcel') renderInfoCard('speedaf-parcel-status-card', 'Parcel Status Card', 'Enter your tracking number. Speedy will not invent parcel status without verified data.'); else if (actionTitle === 'Redelivery') renderInfoCard('speedaf-redelivery-card', 'Redelivery Card', 'Share the tracking number and preferred redelivery details.'); else if (actionTitle === 'Refuse delivery') renderInfoCard('speedaf-refuse-card', 'Refuse Delivery Card', 'Share the tracking number so refusal support can be checked.'); else if (actionTitle === 'Talk to human') renderInfoCard('speedaf-handoff-card', 'Human Handoff Card', 'Send this request and Speedy will decide whether a support specialist is needed.'); }
  function renderHeuristicCard(data, replyText) { var intent = data && data.intent ? String(data.intent) : ''; if (data && data.handoff_required === true) return renderInfoCard('speedaf-handoff-card', 'Human Handoff Card', 'A support specialist review has been requested. Use your tracking number as the customer reference.'); if (intent.indexOf('tracking') >= 0 || /tracking|parcel|shipment/i.test(replyText || '')) renderInfoCard('speedaf-parcel-status-card', 'Parcel Status Card', 'Use the tracking number for verified follow-up.'); else if (/redelivery/i.test(replyText || '')) renderInfoCard('speedaf-redelivery-card', 'Redelivery Card', 'Redelivery help will continue through this chat.'); else if (/refuse|refusal/i.test(replyText || '')) renderInfoCard('speedaf-refuse-card', 'Refuse Delivery Card', 'Refusal support will continue through this chat.'); }
  function appendMessage(role, text, extraClass, key) { if (key && state.rendered[key]) return state.rendered[key]; var el = document.createElement('div'); el.className = 'nd-webchat-msg ' + role + (extraClass ? ' ' + extraClass : ''); el.textContent = text || ''; if (key) state.rendered[key] = el; messagesEl.appendChild(el); if (!state.open && role !== 'visitor') { state.unread += 1; updateUnread(); } scrollToBottomIfNeeded(); return el; }
  function updateMessage(el, text, role, extraClass) { if (!el) return; el.textContent = text || ''; el.className = 'nd-webchat-msg ' + role + (extraClass ? ' ' + extraClass : ''); scrollToBottomIfNeeded(); }
  function appendTextToMessage(el, text) { if (!el || !text) return; el.textContent = (el.textContent || '') + text; scrollToBottomIfNeeded(); }
  function setBubbleState(el, stateName) { if (!el) return; el.setAttribute('data-ai-state', stateName || ''); }
  function appendRetry(el, body, handler) { if (!el || el.querySelector('.nd-webchat-retry')) return; var retry = document.createElement('button'); retry.type = 'button'; retry.className = 'nd-webchat-retry'; retry.textContent = 'Retry'; retry.addEventListener('click', function () { retry.disabled = true; handler(body, el); }); el.appendChild(retry); }
  function showTyping() { if (state.typingEl) return; var wrapper = document.createElement('div'); wrapper.className = 'nd-webchat-typing'; wrapper.setAttribute('aria-label', assistantName + ' is replying'); for (var i = 0; i < 3; i += 1) { var dot = document.createElement('span'); dot.className = 'nd-webchat-typing-dot'; wrapper.appendChild(dot); } state.typingEl = wrapper; messagesEl.appendChild(wrapper); scrollToBottomIfNeeded(true); }
  function hideTyping() { if (!state.typingEl) return; state.typingEl.remove(); state.typingEl = null; }
  function isNearBottom() { return messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < 80; }
  function scrollToBottomIfNeeded(force) { if (force || state.open || state.userNearBottom) messagesEl.scrollTop = messagesEl.scrollHeight; }
  function updateUnread() { unread.style.display = state.unread > 0 ? 'block' : 'none'; unread.textContent = String(Math.min(state.unread, 9)); }

  function api(path, options, timeoutMs) { options = options || {}; timeoutMs = timeoutMs || 10000; var headers = Object.assign({ 'Content-Type': 'application/json' }, options.headers || {}); var controller = window.AbortController ? new AbortController() : null; var timer = controller ? setTimeout(function () { controller.abort(); }, timeoutMs) : null; return fetch(apiBase + path, Object.assign({ mode: 'cors', signal: controller ? controller.signal : undefined }, options, { headers: headers })).then(function (res) { return res.json().catch(function () { return {}; }).then(function (data) { if (!res.ok) { var err = new Error(data.detail && data.detail.message ? data.detail.message : data.detail || ('HTTP ' + res.status)); err.status = res.status; err.payload = data; throw err; } return data; }); }).finally(function () { if (timer) clearTimeout(timer); }); }
  function parseSseBlock(block) { var lines = String(block || '').split(/\r?\n/); var eventName = ''; var dataLines = []; for (var i = 0; i < lines.length; i += 1) { var line = lines[i]; if (!line || line.charAt(0) === ':') continue; if (line.indexOf('event:') === 0) eventName = line.slice(6).trim(); else if (line.indexOf('data:') === 0) dataLines.push(line.slice(5).trim()); } if (!eventName && !dataLines.length) return null; var payload = {}; if (dataLines.length) { try { payload = JSON.parse(dataLines.join('\n')); } catch (err) { payload = {}; } } return { event: eventName, payload: payload }; }
  function streamApi(path, payload, timeoutMs, onEvent) { var controller = window.AbortController ? new AbortController() : null; var timer = controller ? setTimeout(function () { controller.abort(); }, timeoutMs || 90000) : null; return fetch(apiBase + path, { method: 'POST', mode: 'cors', signal: controller ? controller.signal : undefined, headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' }, body: JSON.stringify(payload) }).then(async function (res) { var contentType = String(res.headers.get('content-type') || '').toLowerCase(); if (!res.ok || contentType.indexOf('text/event-stream') === -1 || !res.body || !res.body.getReader) { var data = {}; try { data = await res.json(); } catch (err) {} var error = new Error(data.detail || data.error_code || ('HTTP ' + res.status)); error.status = res.status; error.payload = data; throw error; } var reader = res.body.getReader(); var decoder = new TextDecoder(); var buffer = ''; while (true) { var chunk = await reader.read(); if (chunk.done) break; buffer += decoder.decode(chunk.value, { stream: true }); while (buffer.indexOf('\n\n') >= 0) { var idx = buffer.indexOf('\n\n'); var block = buffer.slice(0, idx); buffer = buffer.slice(idx + 2); var parsed = parseSseBlock(block); if (parsed) onEvent(parsed.event, parsed.payload || {}); } } buffer += decoder.decode(); if (buffer.trim()) { var trailing = parseSseBlock(buffer); if (trailing) onEvent(trailing.event, trailing.payload || {}); } }).finally(function () { if (timer) clearTimeout(timer); }); }
  function clientMessageId() { return 'wc_client_' + Date.now().toString(36) + '_' + (++state.optimisticSeq).toString(36); }
  function randomId(prefix) { var suffix = ''; if (window.crypto && window.crypto.getRandomValues) { var arr = new Uint32Array(2); window.crypto.getRandomValues(arr); suffix = arr[0].toString(36) + arr[1].toString(36); } else { suffix = Math.random().toString(36).slice(2) + Date.now().toString(36); } return prefix + '_' + suffix; }
  function loadSessionId() { try { var existing = window.sessionStorage.getItem(sessionKey); if (existing) return existing; var created = randomId('wc_session'); window.sessionStorage.setItem(sessionKey, created); return created; } catch (err) { return randomId('wc_session'); } }
  function loadRecentContext() { try { var parsed = JSON.parse(window.sessionStorage.getItem(contextKey) || '[]'); return Array.isArray(parsed) ? parsed.slice(-MAX_CONTEXT_TURNS * 2) : []; } catch (err) { return []; } }
  function persistRecentContext() { try { window.sessionStorage.setItem(contextKey, JSON.stringify(state.recentContext.slice(-MAX_CONTEXT_TURNS * 2))); } catch (err) {} }
  function pushRecentContext(role, text) { var cleanText = String(text || '').trim().slice(0, 500); if (!cleanText) return; state.recentContext.push({ role: role, text: cleanText }); state.recentContext = state.recentContext.slice(-MAX_CONTEXT_TURNS * 2); persistRecentContext(); }

  function sendFastMessage(body, existingEl, reuseClientMessageId) {
    var cmid = reuseClientMessageId || (existingEl && existingEl.getAttribute('data-client-message-id')) || clientMessageId();
    var bubble = existingEl || appendMessage('visitor', body, 'sending', 'client:' + cmid);
    bubble.setAttribute('data-client-message-id', cmid);
    state.busy = true; sendEl.disabled = true; inputEl.value = ''; setStatus(assistantName + ' is replying...'); showTyping();
    var aiBubble = null, aiText = '', sawVisibleStreamText = false, finalSeen = false, replayed = false;
    var requestPayload = { tenant_key: tenantKey, channel_key: channelKey, session_id: state.sessionId, client_message_id: cmid, body: body, recent_context: state.recentContext };
    var timeoutMs = Number(script.getAttribute('data-fast-reply-timeout-ms') || script.getAttribute('data-timeout-ms') || 90000);
    function ensureAIBubble() { if (!aiBubble) aiBubble = appendMessage('agent', '', 'streaming', 'agent:' + cmid); return aiBubble; }
    function markReplyComplete(stateName, finalPayload) { hideTyping(); updateMessage(bubble, body, 'visitor'); if (aiBubble) { updateMessage(aiBubble, aiText, 'agent', 'complete'); setBubbleState(aiBubble, stateName || 'complete'); } pushRecentContext('customer', body); if (aiText) pushRecentContext('ai', aiText); renderHeuristicCard(finalPayload || {}, aiText); setStatus('Online'); }
    function markReplyInterrupted() { hideTyping(); updateMessage(bubble, body, 'visitor'); aiBubble = ensureAIBubble(); aiText = aiText ? aiText + '\n\nThis reply was interrupted. Please retry.' : 'This reply was interrupted. Please retry.'; updateMessage(aiBubble, aiText, 'agent', 'failed'); setBubbleState(aiBubble, 'failed_incomplete'); setStatus('Connection issue. Please try again.'); renderInfoCard('speedaf-network-error', 'Network error / retry', 'The reply was interrupted. Retry keeps the same request reference.'); appendRetry(aiBubble, body, function (retryBody) { sendFastMessage(retryBody, bubble, cmid); }); }
    function fallbackToNonStream() { hideTyping(); api('/api/webchat/fast-reply', { method: 'POST', body: JSON.stringify(requestPayload) }, timeoutMs).then(function (data) { updateMessage(bubble, body, 'visitor'); if (data && data.ok === true && data.ai_generated === true && data.reply) { aiText = String(data.reply || ''); aiBubble = ensureAIBubble(); updateMessage(aiBubble, aiText, 'agent', 'complete'); setBubbleState(aiBubble, 'complete'); pushRecentContext('customer', body); pushRecentContext('ai', aiText); renderHeuristicCard(data, aiText); setStatus(data.handoff_required === true ? 'Support handoff requested' : 'Online'); return; } updateMessage(bubble, body, 'visitor', 'failed'); setStatus(data && data.retry_after_ms ? 'Speedy is reconnecting...' : 'Connection issue. Please try again.'); renderInfoCard(data && data.retry_after_ms ? 'speedaf-ai-unavailable' : 'speedaf-network-error', data && data.retry_after_ms ? 'AI unavailable' : 'Network error / retry', 'Please retry in a moment.'); appendRetry(bubble, body, function (retryBody) { sendFastMessage(retryBody, bubble, cmid); }); }).catch(function () { updateMessage(bubble, body, 'visitor', 'failed'); setStatus('Connection issue. Please try again.'); renderInfoCard('speedaf-network-error', 'Network error / retry', 'Please check your connection and retry.'); appendRetry(bubble, body, function (retryBody) { sendFastMessage(retryBody, bubble, cmid); }); }); }
    streamApi('/api/webchat/fast-reply/stream', requestPayload, timeoutMs, function (eventName, data) { if (eventName === 'reply_delta' && data && typeof data.text === 'string' && data.text) { hideTyping(); sawVisibleStreamText = true; aiBubble = ensureAIBubble(); appendTextToMessage(aiBubble, data.text); aiText += data.text; updateMessage(aiBubble, aiText, 'agent', 'streaming'); setBubbleState(aiBubble, 'streaming'); return; } if (eventName === 'replay' && data && typeof data.reply === 'string') { hideTyping(); replayed = true; sawVisibleStreamText = true; aiText = data.reply; aiBubble = ensureAIBubble(); updateMessage(aiBubble, aiText, 'agent', 'complete'); setBubbleState(aiBubble, 'replayed_complete'); return; } if (eventName === 'final') { finalSeen = true; markReplyComplete(replayed || (data && data.replayed === true) ? 'replayed_complete' : 'complete', data || {}); return; } if (eventName === 'error') { if (sawVisibleStreamText) markReplyInterrupted(); else throw new Error('stream_failed_before_reply'); } }).then(function () { if (!finalSeen && sawVisibleStreamText) markReplyInterrupted(); else if (!finalSeen && !sawVisibleStreamText) fallbackToNonStream(); }).catch(function () { if (sawVisibleStreamText) markReplyInterrupted(); else fallbackToNonStream(); }).finally(function () { state.busy = false; sendEl.disabled = false; });
  }

  function restoreLegacySession() { try { var cached = JSON.parse(window.sessionStorage.getItem(storageKey + ':legacy') || '{}'); state.legacyConversationId = cached.conversationId || null; state.legacyVisitorToken = cached.visitorToken || null; state.legacyLastMessageId = 0; } catch (err) {} }
  function persistLegacySession() { try { window.sessionStorage.setItem(storageKey + ':legacy', JSON.stringify({ conversationId: state.legacyConversationId, visitorToken: state.legacyVisitorToken })); } catch (err) {} }
  function ensureLegacySession() { if (state.legacyConversationId && state.legacyVisitorToken) return Promise.resolve(); setStatus('Connecting...'); return api('/api/webchat/init', { method: 'POST', headers: state.legacyVisitorToken ? { 'X-Webchat-Visitor-Token': state.legacyVisitorToken } : {}, body: JSON.stringify({ tenant_key: tenantKey, channel_key: channelKey, conversation_id: state.legacyConversationId, origin: window.location.origin, page_url: window.location.href }) }, 12000).then(function (data) { state.legacyConversationId = data.conversation_id; state.legacyVisitorToken = data.visitor_token; persistLegacySession(); setStatus('Online'); return pollLegacy(true); }).catch(function () { setStatus('Temporarily unavailable'); }); }
  function pollLegacy(reset) { if (!state.legacyConversationId || !state.legacyVisitorToken) return Promise.resolve(); var qs = '?limit=50'; if (state.legacyLastMessageId) qs += '&after_id=' + encodeURIComponent(state.legacyLastMessageId); return api('/api/webchat/conversations/' + encodeURIComponent(state.legacyConversationId) + '/messages' + qs, { headers: { 'X-Webchat-Visitor-Token': state.legacyVisitorToken } }, Number(script.getAttribute('data-fast-reply-timeout-ms') || script.getAttribute('data-timeout-ms') || 90000)).then(function (data) { (data.messages || []).forEach(function (msg) { if (msg.id && msg.id > state.legacyLastMessageId) state.legacyLastMessageId = msg.id; var role = msg.direction === 'visitor' ? 'visitor' : 'agent'; appendMessage(role, msg.body_text || msg.body || (msg.payload_json && (msg.payload_json.title || msg.payload_json.body)) || '', '', 'server:' + String(msg.id)); }); if (reset) setStatus('Online'); }).catch(function () { setStatus('Reconnecting...'); }); }
  function scheduleLegacyPoll() { if (mode !== 'legacy') return; if (state.legacyPollTimer) clearTimeout(state.legacyPollTimer); state.legacyPollTimer = setTimeout(function tick() { if (state.open && document.visibilityState !== 'hidden') pollLegacy(false).finally(scheduleLegacyPoll); else scheduleLegacyPoll(); }, document.visibilityState === 'hidden' ? 15000 : 4000); }
  function sendLegacyMessage(body, existingEl) { var cmid = clientMessageId(); var bubble = existingEl || appendMessage('visitor', body, 'sending', 'client:' + cmid); state.busy = true; sendEl.disabled = true; inputEl.value = ''; setStatus('Sending...'); ensureLegacySession().then(function () { return api('/api/webchat/conversations/' + encodeURIComponent(state.legacyConversationId) + '/messages', { method: 'POST', headers: { 'X-Webchat-Visitor-Token': state.legacyVisitorToken }, body: JSON.stringify({ body: body, client_message_id: cmid }) }, 12000); }).then(function (data) { updateMessage(bubble, body, 'visitor'); if (data && data.message) appendMessage(data.message.direction === 'visitor' ? 'visitor' : 'agent', data.message.body_text || data.message.body || '', '', 'server:' + String(data.message.id)); setStatus('Sent'); return pollLegacy(true); }).catch(function () { updateMessage(bubble, body, 'visitor', 'failed'); setStatus('Failed to send. Please retry.'); appendRetry(bubble, body, sendLegacyMessage); }).finally(function () { state.busy = false; sendEl.disabled = false; }); }
})();

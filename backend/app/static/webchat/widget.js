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
  var mode = 'public';
  var title = script.getAttribute('data-title') || 'Speedaf Support';
  var subtitle = script.getAttribute('data-subtitle') || 'AI support · human handoff when needed';
  var assistantName = script.getAttribute('data-assistant-name') || 'Speedy';
  var locale = (script.getAttribute('data-locale') || navigator.language || 'en').toLowerCase();
  var welcome = script.getAttribute('data-welcome') || '';
  var buttonLabel = script.getAttribute('data-button-label') || 'Chat with us';
  var closeLabel = script.getAttribute('data-close-label') || 'Close chat';
  var avatarUrl = new URL('/webchat/demo/assets/speedaf-ai-bot-avatar.png', scriptUrl.origin).toString();
  var requestedAccentColor = script.getAttribute('data-accent-color') || '';
  var accentColor = /^#[0-9a-f]{6}$/i.test(requestedAccentColor) ? requestedAccentColor : '#ff5a00';
  var securityNote = script.getAttribute('data-security-note') || 'Do not share passwords or payment codes.';
  var autoOpen = script.getAttribute('data-auto-open') === 'true';
  var liveVoiceMode = (script.getAttribute('data-live-voice-mode') || 'off').toLowerCase();
  var liveVoiceLabel = script.getAttribute('data-live-voice-label') || 'VOIP Call';
  var storageKey = 'nexusdesk:webchat:' + apiBase + ':' + tenantKey + ':' + channelKey + ':' + mode;
  var contextKey = storageKey + ':recent-context';
  var sessionKey = storageKey + ':session-id';
  var MAX_CONTEXT_TURNS = 5;
  var LEGACY_POLL_IDLE_MS = Number(script.getAttribute('data-poll-ms') || 4000);
  var LEGACY_POLL_PENDING_MS = Number(script.getAttribute('data-pending-poll-ms') || 350);

  var state = {
    open: false,
    busy: false,
    composing: false,
    unread: 0,
    userNearBottom: true,
    optimisticSeq: 0,
    typingEl: null,
    aiWaitingSince: null,
    sessionId: loadSessionId(),
    recentContext: loadRecentContext(),
    legacyConversationId: null,
    legacyVisitorToken: null,
    legacyLastMessageId: 0,
    legacyLastEventId: 0,
    legacyPollTimer: null,
    legacyWs: null,
    legacyWsReconnectTimer: null,
    legacyRecoveryPromise: null,
    voiceOpen: false,
    liveVoice: null,
    rendered: {}
  };

  var style = document.createElement('style');
  style.textContent = '\n'
    + '.nd-webchat-button,.nd-webchat-panel,.nd-webchat-panel *{box-sizing:border-box}\n'
    + '.nd-webchat-button{position:fixed;right:24px;bottom:24px;z-index:2147483000;width:64px;min-width:64px;height:64px;border:0;border-radius:999px;background:linear-gradient(135deg,' + accentColor + ',#ff7a1a);color:#fff;display:inline-flex;align-items:center;justify-content:center;box-shadow:0 18px 42px rgba(255,90,0,.34);cursor:pointer;transition:opacity .2s ease,visibility .2s ease,transform .22s ease}\n'
    + '.nd-webchat-button:hover{transform:translateY(-2px)}.nd-webchat-button[data-open=true]{display:none;opacity:0;visibility:hidden;transform:translateY(14px) scale(.95);pointer-events:none}\n'
    + '.nd-webchat-button-icon{display:grid;place-items:center}.nd-webchat-button-icon svg{width:28px;height:28px}.nd-webchat-button-label{position:absolute;right:76px;top:50%;transform:translateY(-50%) translateX(8px);white-space:nowrap;background:#071126;color:#fff;padding:10px 12px;border-radius:14px;box-shadow:0 16px 36px rgba(7,17,38,.18);opacity:0;visibility:hidden;pointer-events:none;transition:opacity .18s ease,visibility .18s ease,transform .18s ease;font:720 13.5px/18px system-ui,-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif}.nd-webchat-button:hover .nd-webchat-button-label,.nd-webchat-button:focus-visible .nd-webchat-button-label{opacity:1;visibility:visible;transform:translateY(-50%) translateX(0)}\n'
    + '.nd-webchat-unread{position:absolute;right:8px;top:7px;min-width:18px;height:18px;border-radius:999px;background:#ef4444;color:#fff;font:700 11px/18px system-ui;text-align:center;display:none}.nd-webchat-unread:empty{display:block;width:10px;min-width:10px;height:10px;background:#25d391;box-shadow:0 0 0 4px rgba(37,211,145,.20)}\n'
    + '.nd-webchat-panel{position:fixed;right:28px;bottom:24px;z-index:2147483000;width:min(432px,calc(100vw - 32px));height:min(680px,calc(100dvh - 48px));display:none;flex-direction:column;background:#fff;border:1px solid rgba(231,235,241,.95);border-radius:28px;box-shadow:0 34px 96px rgba(7,17,38,.26);overflow:hidden;font:14px/1.45 system-ui,-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;color:#071126}\n'
    + '.nd-webchat-panel[data-open=true]{display:flex}\n'
    + '.nd-webchat-header{flex:0 0 auto;min-height:94px;display:grid;grid-template-columns:56px minmax(0,1fr) auto auto;gap:12px;align-items:center;padding:18px;background:linear-gradient(135deg,#fff7f1,#fff);border-bottom:1px solid #edf1f6;color:#071126}\n'
    + '.nd-webchat-avatar{width:56px;height:56px;border-radius:20px;overflow:hidden;background:#fff;border:1px solid rgba(255,90,0,.20);box-shadow:0 8px 18px rgba(255,90,0,.10)}.nd-webchat-avatar img{width:100%;height:100%;object-fit:cover;display:block}.nd-webchat-title-row{display:flex;align-items:center;gap:8px;min-width:0}.nd-webchat-title-row strong{display:block;font-size:17px;line-height:22px;font-weight:780;min-width:0}.nd-webchat-online{font-size:11px;font-weight:800;padding:4px 8px;border-radius:999px;background:#eafaf3;color:#14805c}.nd-webchat-header-subtitle{display:block;margin-top:4px;font-size:13px;line-height:18px;color:#657085;font-weight:560}\n'
    + '.nd-webchat-close,.nd-webchat-voice{border:0;cursor:pointer}.nd-webchat-close{width:38px;height:38px;border-radius:14px;background:#f2f5f9;position:relative;color:#374155}.nd-webchat-close:before,.nd-webchat-close:after{content:"";position:absolute;left:12px;right:12px;top:18px;height:2px;border-radius:2px;background:currentColor}.nd-webchat-close:before{transform:rotate(45deg)}.nd-webchat-close:after{transform:rotate(-45deg)}\n'
    + '.nd-webchat-voice{display:none;min-width:72px;min-height:54px;margin-left:auto;margin-right:8px;padding:8px 10px;border-radius:14px;background:#f97316;color:#fff;box-shadow:0 10px 24px rgba(249,115,22,.28);font:800 12px/1.05 system-ui,-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;white-space:nowrap}.nd-webchat-voice[data-visible=true]{display:inline-flex;flex-direction:column;align-items:center;justify-content:center;gap:4px}.nd-webchat-voice svg{width:18px;height:18px}.nd-webchat-voice.is-live{animation:ndLiveVoicePulse 1.15s infinite}@keyframes ndLiveVoicePulse{0%{box-shadow:0 0 0 0 rgba(249,115,22,.52)}70%{box-shadow:0 0 0 12px rgba(249,115,22,0)}100%{box-shadow:0 0 0 0 rgba(249,115,22,0)}}\n'
    + '.nd-webchat-voice-panel{display:none;border-bottom:1px solid #eef2f7;background:#fff7ed;padding:12px 16px 14px}.nd-webchat-voice-panel[data-open=true]{display:block}.nd-webchat-voice-row{display:flex;gap:8px;align-items:center}.nd-webchat-voice-auto{flex:1;min-width:0;color:#64748b;font-size:13px;font-weight:650;line-height:1.35}.nd-webchat-voice-start{height:38px;min-width:76px;border:0;border-radius:12px;background:#f97316;color:#fff;font-weight:800;cursor:pointer}.nd-webchat-voice-start.stop{background:#dc2626}.nd-webchat-voice-status{margin-top:10px;border:1px solid #fed7aa;background:#fffaf6;color:#9a3412;border-radius:13px;padding:9px 10px;font-size:12.5px;line-height:1.45}.nd-webchat-voice-transcript{margin-top:8px;max-height:126px;overflow:auto}.nd-webchat-voice-msg{margin-top:6px;padding:8px 10px;border-radius:12px;font-size:12.5px;line-height:1.42}.nd-webchat-voice-msg.user{background:#eff6ff;color:#1e3a8a}.nd-webchat-voice-msg.ai{background:#fff;color:#7c2d12;border:1px solid #fed7aa}.nd-webchat-voice-foot{margin-top:8px;color:#64748b;font-size:11.5px}\n'
    + '.nd-webchat-messages{flex:1 1 auto;min-height:0;overflow-y:auto;overflow-x:hidden;padding:18px 18px 8px;background:linear-gradient(180deg,#fff,#fbfcfe)}\n'
    + '.nd-webchat-msg{max-width:84%;margin:0 0 12px;padding:13px 14px;border-radius:18px 18px 18px 6px;font-size:14.5px;line-height:1.46;white-space:pre-wrap;overflow-wrap:anywhere}\n'
    + '.nd-webchat-msg.visitor{margin-left:auto;background:' + accentColor + ';color:#fff;border-radius:18px 18px 6px 18px}.nd-webchat-msg.agent,.nd-webchat-msg.system{margin-right:auto;background:#f1f4f8;color:#243044;border:0;border-bottom-left-radius:6px}\n'
    + '.nd-webchat-msg.sending{opacity:.72}.nd-webchat-msg.failed{outline:2px solid #fca5a5}\n'
    + '.nd-webchat-typing{max-width:84%;margin:0 0 12px;padding:13px 15px;border-radius:18px;border-bottom-left-radius:6px;background:#f1f4f8;display:inline-flex;align-items:center;gap:5px}\n'
    + '.nd-webchat-typing-dot{width:6px;height:6px;border-radius:999px;background:#9aa4b5;animation:ndTypingBounce 1s infinite ease-in-out}.nd-webchat-typing-dot:nth-child(2){animation-delay:.12s}.nd-webchat-typing-dot:nth-child(3){animation-delay:.24s}\n'
    + '.nd-webchat-typing-status{display:none}\n'
    + '@keyframes ndTypingBounce{0%,80%,100%{transform:translateY(0);opacity:.45}40%{transform:translateY(-4px);opacity:1}}@media (prefers-reduced-motion:reduce){.nd-webchat-typing-dot{animation:none}}\n'
    + '.nd-webchat-retry{display:block;margin-top:7px;border:1px solid currentColor;background:transparent;color:inherit;border-radius:999px;padding:5px 9px;font:700 12px system-ui;cursor:pointer}.nd-webchat-retry:disabled{opacity:.6;cursor:not-allowed}\n'
    + '.nd-webchat-composer-wrap{flex:0 0 auto;padding:12px 14px 14px;border-top:1px solid #edf1f6;background:#fff;padding-bottom:max(14px,env(safe-area-inset-bottom))}.nd-webchat-form{height:50px;display:grid;grid-template-columns:22px 1fr 42px;gap:8px;align-items:center;padding:0 6px 0 12px;border:1px solid #dfe5ee;border-radius:18px;background:#fff}.nd-webchat-attach{display:grid;place-items:center;color:#152033}.nd-webchat-input{min-width:0;border:0;background:#fff;color:#071126;font:500 14.5px system-ui,-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;outline:none}.nd-webchat-send{width:42px;height:40px;border:0;border-radius:14px;background:' + accentColor + ';color:#fff;display:grid;place-items:center;cursor:pointer}.nd-webchat-send svg{width:22px;height:22px}.nd-webchat-send:disabled{opacity:.58;cursor:not-allowed}\n'
    + '.nd-webchat-security{display:flex;align-items:center;gap:6px;margin:9px 2px 0;color:#7a8495;font-size:12px}.nd-webchat-status{display:none;flex:0 0 auto;padding:7px 15px;font-size:12.5px;font-weight:560;color:#667085;border-top:1px solid #f2f4f7;background:#fff}.nd-webchat-status:not(:empty){display:block}\n'
    + '@media (max-width:640px){.nd-webchat-panel{right:8px;bottom:8px;width:calc(100vw - 16px);height:calc(100dvh - 16px);max-width:none;max-height:none;border-radius:24px}.nd-webchat-header{grid-template-columns:48px minmax(0,1fr) auto 36px;min-height:82px;padding:14px}.nd-webchat-avatar{width:48px;height:48px;border-radius:17px}.nd-webchat-button{width:58px;min-width:58px;height:58px;right:16px;bottom:16px}.nd-webchat-button-label{display:none!important}}\n'
    + '@media (max-width:390px){.nd-webchat-online{display:none}.nd-webchat-title-row{gap:5px}.nd-webchat-voice{min-width:62px;padding:7px 8px;font-size:11px}}\n';
  document.head.appendChild(style);

  var button = document.createElement('button');
  button.className = 'nd-webchat-button';
  button.type = 'button';
  button.setAttribute('aria-label', buttonLabel);
  button.innerHTML = '<span class="nd-webchat-button-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M21 12a8 8 0 0 1-8 8H7l-4 3v-5.4A8 8 0 1 1 21 12Z"/><path d="M8 12h.01M12 12h.01M16 12h.01"/></svg></span>';
  var buttonText = document.createElement('span');
  buttonText.className = 'nd-webchat-button-label';
  buttonText.textContent = buttonLabel;
  var unread = document.createElement('span');
  unread.className = 'nd-webchat-unread';
  button.appendChild(buttonText);
  button.appendChild(unread);

  var panel = document.createElement('section');
  panel.className = 'nd-webchat-panel';
  panel.setAttribute('aria-label', title);
  var header = document.createElement('div');
  header.className = 'nd-webchat-header';
  var avatar = document.createElement('div');
  avatar.className = 'nd-webchat-avatar';
  var avatarImg = document.createElement('img');
  avatarImg.src = avatarUrl;
  avatarImg.alt = '';
  avatarImg.decoding = 'async';
  avatar.appendChild(avatarImg);
  var headerText = document.createElement('div');
  var titleRow = document.createElement('div');
  titleRow.className = 'nd-webchat-title-row';
  var h = document.createElement('strong');
  h.textContent = title;
  var online = document.createElement('span');
  online.className = 'nd-webchat-online';
  online.textContent = 'AI';
  var s = document.createElement('span');
  s.className = 'nd-webchat-header-subtitle';
  s.textContent = subtitle;
  titleRow.appendChild(h);
  titleRow.appendChild(online);
  headerText.appendChild(titleRow);
  headerText.appendChild(s);
  var voiceBtn = document.createElement('button');
  voiceBtn.className = 'nd-webchat-voice';
  voiceBtn.type = 'button';
  voiceBtn.setAttribute('data-visible', liveVoiceMode === 'livekit-room' ? 'true' : 'false');
  voiceBtn.setAttribute('aria-label', liveVoiceLabel);
  voiceBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6.6 10.8a15.5 15.5 0 0 0 6.6 6.6l2.2-2.2a1 1 0 0 1 1-.24c1.08.36 2.24.56 3.44.56a1 1 0 0 1 1 1V20a1 1 0 0 1-1 1C10.53 21 3 13.47 3 4.16a1 1 0 0 1 1-1H7.5a1 1 0 0 1 1 1c0 1.2.2 2.36.56 3.44a1 1 0 0 1-.24 1Z"/></svg><span>VOIP<br/>Call</span>';
  var close = document.createElement('button');
  close.className = 'nd-webchat-close';
  close.type = 'button';
  close.setAttribute('aria-label', 'Close');
  header.appendChild(avatar);
  header.appendChild(headerText);
  header.appendChild(voiceBtn);
  header.appendChild(close);
  panel.appendChild(header);

  var voicePanel = document.createElement('div');
  voicePanel.className = 'nd-webchat-voice-panel';
  voicePanel.innerHTML = '<div class="nd-webchat-voice-row"><div class="nd-webchat-voice-auto">Secure LiveKit voice room with server-owned recording and transcript policy.</div><button type="button" class="nd-webchat-voice-start">Start call</button></div><div class="nd-webchat-voice-status">Voice is idle.</div><div class="nd-webchat-voice-transcript"></div><div class="nd-webchat-voice-foot">Microphone access is requested only in the dedicated call window.</div>';
  panel.appendChild(voicePanel);

  var messagesEl = document.createElement('div');
  messagesEl.className = 'nd-webchat-messages';
  panel.appendChild(messagesEl);
  var statusEl = document.createElement('div');
  statusEl.className = 'nd-webchat-status';
  panel.appendChild(statusEl);
  var composerWrap = document.createElement('div');
  composerWrap.className = 'nd-webchat-composer-wrap';
  var form = document.createElement('form');
  form.className = 'nd-webchat-form';
  var attach = document.createElement('span');
  attach.className = 'nd-webchat-attach';
  attach.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 1 1-2.83-2.83l8.49-8.48"/></svg>';
  var inputEl = document.createElement('input');
  inputEl.className = 'nd-webchat-input';
  inputEl.type = 'text';
  inputEl.maxLength = 2000;
  inputEl.placeholder = 'Type your message...';
  inputEl.autocomplete = 'off';
  var sendEl = document.createElement('button');
  sendEl.className = 'nd-webchat-send';
  sendEl.type = 'submit';
  sendEl.setAttribute('aria-label', 'Send');
  sendEl.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/></svg>';
  form.appendChild(attach);
  form.appendChild(inputEl);
  form.appendChild(sendEl);
  composerWrap.appendChild(form);
  var security = document.createElement('div');
  security.className = 'nd-webchat-security';
  security.textContent = '🔒 ' + securityNote;
  composerWrap.appendChild(security);
  panel.appendChild(composerWrap);
  document.body.appendChild(button);
  document.body.appendChild(panel);

  var voiceStart = voicePanel.querySelector('.nd-webchat-voice-start');

  button.addEventListener('click', function () { openPanel(true); });
  close.addEventListener('click', function () { openPanel(false); });
  voiceBtn.addEventListener('click', function () { toggleVoicePanel(); });
  voiceStart.addEventListener('click', startLiveVoice);
  messagesEl.addEventListener('scroll', function () { state.userNearBottom = isNearBottom(); });
  document.addEventListener('visibilitychange', function () { if (state.open) scheduleLegacyPoll(); });
  inputEl.addEventListener('compositionstart', function () { state.composing = true; });
  inputEl.addEventListener('compositionend', function () { state.composing = false; });
  inputEl.addEventListener('keydown', function (event) {
    if (event.key === 'Enter' && !event.shiftKey && !state.composing) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  form.addEventListener('submit', function (event) {
    event.preventDefault();
    var body = String(inputEl.value || '').trim();
    if (!body || state.busy) return;
    sendConversationMessage(body);
  });

  if (welcome) appendMessage('agent', welcome);
  restoreLegacySession();
  exposePublicApi();
  bindPageTriggers();
  if (autoOpen) setTimeout(function () { openPanel(true); }, 150);

  function setStatus(text) {
    statusEl.textContent = text || '';
  }

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function exposePublicApi() {
    window.NexusDeskWebChat = {
      open: function () { openPanel(true); },
      close: function () { openPanel(false); },
      toggle: function () { openPanel(); },
      prefill: function (text) {
        inputEl.value = String(text || '').slice(0, inputEl.maxLength || 2000);
        openPanel(true);
        setTimeout(function () { inputEl.focus(); }, 80);
      },
      send: function (text) {
        var body = String(text || '').trim();
        if (!body) return;
        openPanel(true);
        sendConversationMessage(body);
      }
    };
  }

  function findClosest(node, selector) {
    while (node && node !== document) {
      if (node.matches && node.matches(selector)) return node;
      node = node.parentNode;
    }
    return null;
  }

  function bindPageTriggers() {
    document.addEventListener('click', function (event) {
      var trigger = findClosest(event.target, '[data-open-chat]');
      if (!trigger) return;
      event.preventDefault();
      var message = trigger.getAttribute('data-webchat-message') || '';
      var inputSelector = trigger.getAttribute('data-webchat-input') || '';
      if (inputSelector) {
        var source = document.querySelector(inputSelector);
        if (source && source.value) message = source.value;
      }
      if (trigger.getAttribute('data-webchat-send') === 'true' && String(message || '').trim()) {
        sendConversationMessage(String(message).trim());
      } else {
        openPanel(true);
        if (message) inputEl.value = String(message).slice(0, inputEl.maxLength || 2000);
      }
    });

    document.addEventListener('submit', function (event) {
      var pageForm = findClosest(event.target, '[data-webchat-form]');
      if (!pageForm) return;
      event.preventDefault();
      var inputSelector = pageForm.getAttribute('data-webchat-input') || 'input,textarea';
      var source = pageForm.querySelector(inputSelector) || document.querySelector(inputSelector);
      var value = source && source.value ? String(source.value).trim() : '';
      if (!value) {
        if (source && source.focus) source.focus();
        return;
      }
      if (pageForm.getAttribute('data-webchat-submit') === 'prefill') {
        window.NexusDeskWebChat.prefill(value);
      } else {
        window.NexusDeskWebChat.send(value);
      }
    });
  }

  function openPanel(force) {
    state.open = typeof force === 'boolean' ? force : !state.open;
    panel.setAttribute('data-open', state.open ? 'true' : 'false');
    button.setAttribute('data-open', state.open ? 'true' : 'false');
    buttonText.textContent = state.open ? closeLabel : buttonLabel;
    if (state.open) {
      state.unread = 0;
      updateUnread();
      setTimeout(function () { inputEl.focus(); }, 80);
      ensureLegacySession().then(scheduleLegacyPoll);
    }
    panel.dispatchEvent(new CustomEvent('nexusdesk:webchat:open-change', { detail: { open: state.open } }));
  }

  function appendMessage(role, text, extraClass, key) {
    if (key && state.rendered[key]) return state.rendered[key];
    var el = document.createElement('div');
    el.className = 'nd-webchat-msg ' + role + (extraClass ? ' ' + extraClass : '');
    el.textContent = text || '';
    if (key) state.rendered[key] = el;
    messagesEl.appendChild(el);
    if (!state.open && role !== 'visitor') {
      state.unread += 1;
      updateUnread();
    }
    scrollToBottomIfNeeded();
    return el;
  }

  function updateMessage(el, text, role, extraClass) {
    if (!el) return;
    el.textContent = text || '';
    el.className = 'nd-webchat-msg ' + role + (extraClass ? ' ' + extraClass : '');
    scrollToBottomIfNeeded();
  }

  function appendTextToMessage(el, text) {
    if (!el || !text) return;
    el.textContent = (el.textContent || '') + text;
    scrollToBottomIfNeeded();
  }

  function setBubbleState(el, stateName) {
    if (!el) return;
    el.setAttribute('data-ai-state', stateName || '');
  }

  function appendRetry(el, body, handler) {
    if (!el || el.querySelector('.nd-webchat-retry')) return;
    var retry = document.createElement('button');
    retry.type = 'button';
    retry.className = 'nd-webchat-retry';
    retry.textContent = 'Retry';
    retry.addEventListener('click', function () {
      retry.disabled = true;
      handler(body, el);
    });
    el.appendChild(retry);
  }

  function showTyping() {
    if (state.typingEl) {
      updateTypingStatus();
      return;
    }
    var wrapper = document.createElement('div');
    wrapper.className = 'nd-webchat-typing';
    wrapper.setAttribute('aria-label', assistantName + ' is replying');
    for (var i = 0; i < 3; i += 1) {
      var dot = document.createElement('span');
      dot.className = 'nd-webchat-typing-dot';
      wrapper.appendChild(dot);
    }
    var label = document.createElement('span');
    label.className = 'nd-webchat-typing-status';
    wrapper.appendChild(label);
    state.typingEl = wrapper;
    messagesEl.appendChild(wrapper);
    updateTypingStatus();
    scrollToBottomIfNeeded(true);
  }

  function hideTyping() {
    if (!state.typingEl) return;
    state.typingEl.remove();
    state.typingEl = null;
    state.aiWaitingSince = null;
  }

  function updateTypingStatus(status, elapsedMs) {
    if (!state.typingEl) return;
    var label = state.typingEl.querySelector('.nd-webchat-typing-status');
    var normalized = String(status || '').toLowerCase();
    var elapsed = Number(elapsedMs);
    if (!Number.isFinite(elapsed)) {
      elapsed = state.aiWaitingSince ? Date.now() - state.aiWaitingSince : 0;
    }
    state.typingEl.setAttribute('data-ai-status', normalized || 'processing');
    state.typingEl.setAttribute('data-ai-wait-ms', String(Math.max(0, Math.round(elapsed))));
    if (label) {
      label.textContent = '';
      label.setAttribute('aria-hidden', 'true');
    }
  }

  function syncAiTyping(status, pending, elapsedMs) {
    var normalized = String(status || '').toLowerCase();
    var active = pending === true || normalized === 'queued' || normalized === 'processing' || normalized === 'bridge_calling' || normalized === 'fallback_generating';
    var terminal = normalized === 'completed' || normalized === 'failed' || normalized === 'timeout' || normalized === 'cancelled' || normalized === 'superseded';
    if (active) {
      var elapsed = Number(elapsedMs);
      state.aiWaitingSince = Number.isFinite(elapsed) ? Date.now() - Math.max(0, elapsed) : (state.aiWaitingSince || Date.now());
      showTyping();
      updateTypingStatus(normalized, elapsedMs);
    }
    else if (terminal || pending === false) hideTyping();
  }

  function legacyPollDelayMs() {
    if (document.visibilityState === 'hidden') return Math.max(15000, LEGACY_POLL_IDLE_MS * 3);
    if (state.typingEl) {
      updateTypingStatus();
      return Math.max(250, Math.min(LEGACY_POLL_PENDING_MS, 2000));
    }
    return Math.max(1000, LEGACY_POLL_IDLE_MS);
  }

  function isNearBottom() {
    return messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < 80;
  }

  function scrollToBottomIfNeeded(force) {
    if (force || state.open || state.userNearBottom) messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function updateUnread() {
    unread.style.display = state.unread > 0 ? 'block' : 'none';
    unread.textContent = String(Math.min(state.unread, 9));
  }

  function toggleVoicePanel(force) {
    if (liveVoiceMode !== 'livekit-room') return;
    openPanel(true);
    state.voiceOpen = typeof force === 'boolean' ? force : !state.voiceOpen;
    voicePanel.setAttribute('data-open', state.voiceOpen ? 'true' : 'false');
  }

  function voiceStatus(text) {
    var el = voicePanel.querySelector('.nd-webchat-voice-status');
    if (el) el.textContent = text || '';
  }

  function stopLiveVoice(statusText) {
    state.liveVoice = null;
    voiceBtn.classList.remove('is-live');
    if (statusText) voiceStatus(statusText);
  }

  function encodeVoiceBootstrap(payload) {
    var encoded = window.btoa(unescape(encodeURIComponent(JSON.stringify(payload))))
      .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
    return encoded;
  }

  function voiceComplianceEvidence(requirement) {
    if (!requirement || requirement.policy === 'disabled') return null;
    var prompt = String(requirement.prompt || '').trim();
    if (!prompt) throw new Error('Voice compliance prompt is unavailable.');
    var accepted = window.confirm(
      prompt + '\n\nSelect OK to enable this capability, or Cancel to continue the call without it.'
    );
    var decision = accepted
      ? (requirement.policy === 'notice' ? 'notice_delivered' : 'accepted')
      : 'declined';
    return {
      capability: requirement.capability,
      policy: requirement.policy,
      policy_version: requirement.policy_version,
      prompt_sha256: requirement.prompt_sha256,
      decision: decision,
      idempotency_key: [
        'browser-compliance',
        state.sessionId,
        state.legacyConversationId,
        requirement.capability,
        requirement.policy_version,
        String(requirement.prompt_sha256 || '').slice(0, 32)
      ].join(':').slice(0, 180)
    };
  }

  function collectVoiceCompliance(policy) {
    var evidence = [];
    ['recording', 'transcript_persistence'].forEach(function (key) {
      var item = voiceComplianceEvidence(policy && policy[key]);
      if (item) evidence.push(item);
    });
    return evidence;
  }

  function startLiveVoice() {
    if (liveVoiceMode !== 'livekit-room') return;
    toggleVoicePanel(true);
    voiceStatus('Preparing a secure LiveKit room...');
    ensureLegacySession().then(function () {
      return api('/api/webchat/conversations/' + encodeURIComponent(state.legacyConversationId) + '/voice/policy', {
        headers: { 'X-Webchat-Visitor-Token': state.legacyVisitorToken }
      }, 12000);
    }).then(function (policy) {
      var evidence = collectVoiceCompliance(policy);
      return api('/api/webchat/conversations/' + encodeURIComponent(state.legacyConversationId) + '/voice/sessions', {
        method: 'POST',
        headers: { 'X-Webchat-Visitor-Token': state.legacyVisitorToken },
        body: JSON.stringify({ locale: locale || null, compliance_evidence: evidence })
      }, 12000);
    }).then(function (session) {
      if (!session.livekit_url || !session.participant_token) throw new Error('LiveKit voice is unavailable.');
      var bootstrap = encodeVoiceBootstrap({
        role: 'visitor',
        voice_session_id: session.voice_session_id,
        provider: session.provider,
        livekit_url: session.livekit_url,
        participant_token: session.participant_token,
        participant_identity: session.participant_identity,
        visitor_token: state.legacyVisitorToken,
        conversation_id: state.legacyConversationId
      });
      var opened = window.open('/webcall/' + encodeURIComponent(session.voice_session_id) + '#' + bootstrap, '_blank', 'noopener,noreferrer');
      if (!opened) window.location.assign('/webcall/' + encodeURIComponent(session.voice_session_id) + '#' + bootstrap);
      voiceStatus('Voice room opened in a secure call window.');
    }).catch(function (err) {
      voiceStatus('Voice start failed: ' + (err && err.message ? err.message : 'unknown'));
    });
  }

  function api(path, options, timeoutMs) {
    options = options || {};
    timeoutMs = timeoutMs || 10000;
    var headers = Object.assign({ 'Content-Type': 'application/json' }, options.headers || {});
    var controller = window.AbortController ? new AbortController() : null;
    var timer = controller ? setTimeout(function () { controller.abort(); }, timeoutMs) : null;
    return fetch(apiBase + path, Object.assign({ mode: 'cors', signal: controller ? controller.signal : undefined }, options, { headers: headers }))
      .then(function (res) {
        return res.json().catch(function () { return {}; }).then(function (data) {
          if (!res.ok) {
            var err = new Error(data.detail && data.detail.message ? data.detail.message : data.detail || ('HTTP ' + res.status));
            err.status = res.status;
            err.payload = data;
            throw err;
          }
          return data;
        });
      })
      .finally(function () { if (timer) clearTimeout(timer); });
  }

  function wsUrl() {
    var url = new URL('/api/webchat/ws', apiBase || window.location.origin);
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    return url.toString();
  }

  function parseSseBlock(block) {
    var lines = String(block || '').split(/\r?\n/);
    var eventName = '';
    var dataLines = [];
    for (var i = 0; i < lines.length; i += 1) {
      var line = lines[i];
      if (!line || line.charAt(0) === ':') continue;
      if (line.indexOf('event:') === 0) eventName = line.slice(6).trim();
      else if (line.indexOf('data:') === 0) dataLines.push(line.slice(5).trim());
    }
    if (!eventName && !dataLines.length) return null;
    var payload = {};
    if (dataLines.length) {
      try {
        payload = JSON.parse(dataLines.join('\n'));
      } catch (err) {
        payload = {};
      }
    }
    return { event: eventName, payload: payload };
  }

  function clientMessageId() {
    return 'wc_client_' + Date.now().toString(36) + '_' + (++state.optimisticSeq).toString(36);
  }

  function randomId(prefix) {
    var suffix = '';
    if (window.crypto && window.crypto.getRandomValues) {
      var arr = new Uint32Array(2);
      window.crypto.getRandomValues(arr);
      suffix = arr[0].toString(36) + arr[1].toString(36);
    } else {
      suffix = Math.random().toString(36).slice(2) + Date.now().toString(36);
    }
    return prefix + '_' + suffix;
  }

  function loadSessionId() {
    try {
      var existing = window.sessionStorage.getItem(sessionKey);
      if (existing) return existing;
      var created = randomId('wc_session');
      window.sessionStorage.setItem(sessionKey, created);
      return created;
    } catch (err) {
      return randomId('wc_session');
    }
  }

  function loadRecentContext() {
    try {
      var parsed = JSON.parse(window.sessionStorage.getItem(contextKey) || '[]');
      return Array.isArray(parsed) ? parsed.slice(-MAX_CONTEXT_TURNS * 2) : [];
    } catch (err) {
      return [];
    }
  }

  function persistRecentContext() {
    try {
      window.sessionStorage.setItem(contextKey, JSON.stringify(state.recentContext.slice(-MAX_CONTEXT_TURNS * 2)));
    } catch (err) {}
  }

  function pushRecentContext(role, text) {
    var cleanText = String(text || '').trim().slice(0, 500);
    if (!cleanText) return;
    state.recentContext.push({ role: role, text: cleanText });
    state.recentContext = state.recentContext.slice(-MAX_CONTEXT_TURNS * 2);
    persistRecentContext();
  }

  function restoreLegacySession() {
    try {
      var cached = JSON.parse(window.sessionStorage.getItem(storageKey + ':legacy') || '{}');
      state.legacyConversationId = cached.conversationId || null;
      state.legacyVisitorToken = cached.visitorToken || null;
      state.legacyLastMessageId = Number(cached.lastMessageId || 0);
      state.legacyLastEventId = Number(cached.lastEventId || 0);
    } catch (err) {}
  }

  function persistLegacySession() {
    try {
      window.sessionStorage.setItem(storageKey + ':legacy', JSON.stringify({ conversationId: state.legacyConversationId, visitorToken: state.legacyVisitorToken, lastMessageId: state.legacyLastMessageId, lastEventId: state.legacyLastEventId }));
    } catch (err) {}
  }

  function isLegacySessionAuthError(err) {
    return Boolean(err && (err.status === 401 || err.status === 403 || err.status === 404));
  }

  function clearLegacySession() {
    if (state.legacyWsReconnectTimer) clearTimeout(state.legacyWsReconnectTimer);
    state.legacyWsReconnectTimer = null;
    try {
      if (state.legacyWs && state.legacyWs.readyState < WebSocket.CLOSING) state.legacyWs.close(1000, 'session_reset');
    } catch (err) {}
    state.legacyWs = null;
    state.legacyConversationId = null;
    state.legacyVisitorToken = null;
    state.legacyLastMessageId = 0;
    state.legacyLastEventId = 0;
    try { window.sessionStorage.removeItem(storageKey + ':legacy'); } catch (err) {}
  }

  function recoverLegacySession() {
    if (state.legacyRecoveryPromise) return state.legacyRecoveryPromise;
    clearLegacySession();
    state.legacyRecoveryPromise = ensureLegacySession().then(function () {
      if (!state.legacyConversationId || !state.legacyVisitorToken) throw new Error('webchat_session_recovery_failed');
    }).finally(function () {
      state.legacyRecoveryPromise = null;
    });
    return state.legacyRecoveryPromise;
  }

  function rememberPublicSession(data) {
    var session = data && (data.webchat_session || data);
    if (!session || !session.conversation_id || !session.visitor_token) return;
    state.legacyConversationId = String(session.conversation_id || '');
    state.legacyVisitorToken = String(session.visitor_token || '');
    var lastMessageId = Number(session.last_message_id || session.webchat_last_message_id || 0);
    var lastEventId = Number(session.last_event_id || session.webchat_last_event_id || 0);
    if (lastMessageId > state.legacyLastMessageId) state.legacyLastMessageId = lastMessageId;
    if (lastEventId > state.legacyLastEventId) state.legacyLastEventId = lastEventId;
    persistLegacySession();
    startLegacyWs();
    scheduleLegacyPoll();
  }

  function ensureLegacySession() {
    if (state.legacyConversationId && state.legacyVisitorToken) {
      startLegacyWs();
      return Promise.resolve();
    }
    setStatus('');
    return api('/api/webchat/init', {
      method: 'POST',
      headers: state.legacyVisitorToken ? { 'X-Webchat-Visitor-Token': state.legacyVisitorToken } : {},
      body: JSON.stringify({
        tenant_key: tenantKey,
        channel_key: channelKey,
        conversation_id: state.legacyConversationId,
        origin: window.location.origin,
        page_url: window.location.href
      })
    }, 12000).then(function (data) {
      state.legacyConversationId = data.conversation_id;
      state.legacyVisitorToken = data.visitor_token;
      persistLegacySession();
      setStatus('');
      startLegacyWs();
      return pollLegacy(true);
    }).catch(function (err) {
      setStatus('');
      if (isLegacySessionAuthError(err)) return recoverLegacySession();
    });
  }

  function renderServerMessage(msg) {
    if (!msg || !msg.id) return;
    if (msg.id && msg.id > state.legacyLastMessageId) state.legacyLastMessageId = msg.id;
    var role = msg.direction === 'visitor' ? 'visitor' : 'agent';
    var text = msg.body_text || msg.body || (msg.payload_json && (msg.payload_json.title || msg.payload_json.body)) || '';
    if (!String(text || '').trim()) return;
    var serverKey = 'server:' + String(msg.id);
    if (state.rendered[serverKey]) return;
    var clientKey = msg.client_message_id ? 'client:' + String(msg.client_message_id) : null;
    if (clientKey && state.rendered[clientKey]) {
      updateMessage(state.rendered[clientKey], text, role);
      state.rendered[serverKey] = state.rendered[clientKey];
      persistLegacySession();
      return;
    }
    var el = appendMessage(role, text, '', serverKey);
    if (clientKey) state.rendered[clientKey] = el;
    persistLegacySession();
  }

  function pollLegacy(reset) {
    if (!state.legacyConversationId || !state.legacyVisitorToken) return Promise.resolve();
    var qs = '?limit=50';
    if (state.legacyLastMessageId) qs += '&after_id=' + encodeURIComponent(state.legacyLastMessageId);
    var headers = { 'X-Webchat-Visitor-Token': state.legacyVisitorToken };
    if (!(state.legacyWs && state.legacyWs.readyState === WebSocket.OPEN)) headers['X-Webchat-WS-Fallback'] = 'true';
    return api('/api/webchat/conversations/' + encodeURIComponent(state.legacyConversationId) + '/messages' + qs, {
      headers: headers
    }, Number(script.getAttribute('data-timeout-ms') || 90000)).then(function (data) {
      (data.messages || []).forEach(renderServerMessage);
      syncAiTyping(data.ai_status, data.ai_pending, data.ai_status_elapsed_ms);
      if (reset) setStatus('');
    }).catch(function () {
      setStatus('');
    });
  }

  function scheduleLegacyPoll() {
    if (state.legacyPollTimer) clearTimeout(state.legacyPollTimer);
    state.legacyPollTimer = setTimeout(function tick() {
      if (state.open && document.visibilityState !== 'hidden' && !(state.legacyWs && state.legacyWs.readyState === WebSocket.OPEN)) pollLegacy(false).finally(scheduleLegacyPoll);
      else scheduleLegacyPoll();
    }, legacyPollDelayMs());
  }

  function startLegacyWs() {
    if (script.getAttribute('data-websocket') === 'false') return;
    if (!window.WebSocket || !state.legacyConversationId || !state.legacyVisitorToken) return;
    if (state.legacyWs && state.legacyWs.readyState === WebSocket.OPEN) return;
    if (state.legacyWsReconnectTimer) clearTimeout(state.legacyWsReconnectTimer);
    try {
      if (state.legacyWs && state.legacyWs.readyState < WebSocket.CLOSING) state.legacyWs.close(1000, 'reconnect');
    } catch (err) {}
    try {
      state.legacyWs = new WebSocket(wsUrl());
      state.legacyWs.onopen = function () {
        state.legacyWs.send(JSON.stringify({
          type: 'connection.hello',
          client_type: 'visitor',
          conversation_id: state.legacyConversationId,
          visitor_token: state.legacyVisitorToken,
          last_event_id: state.legacyLastEventId
        }));
        setStatus('');
      };
      state.legacyWs.onmessage = function (event) {
        var data = {};
        try { data = JSON.parse(String(event.data || '{}')); } catch (err) { return; }
        if (data.type === 'connection.ready' || data.type === 'subscription.ready' || data.type === 'pong') return;
        if (data.type === 'error') {
          if (data.code === 'request_failed' && data.retryable !== true) recoverLegacySession().catch(function () {});
          try { state.legacyWs.close(1000, 'server_error'); } catch (err) {}
          return;
        }
        if (typeof data.event_id === 'number') {
          state.legacyLastEventId = Math.max(state.legacyLastEventId, data.event_id);
          persistLegacySession();
        }
        if (data.type === 'message.created' && data.message) {
          hideTyping();
          renderServerMessage(data.message);
          setStatus('');
        } else if (String(data.type || '').indexOf('ai_turn.') === 0) {
          syncAiTyping(String(data.type || '').slice('ai_turn.'.length));
        }
      };
      state.legacyWs.onclose = function () {
        if (!state.open) return;
        setStatus('');
        state.legacyWsReconnectTimer = setTimeout(startLegacyWs, 4000);
        scheduleLegacyPoll();
      };
      state.legacyWs.onerror = function () {
        setStatus('');
      };
    } catch (err) {
      scheduleLegacyPoll();
    }
  }

  function sendConversationMessage(body, existingEl) {
    if (state.busy && !existingEl) return;
    var cmid = clientMessageId();
    var bubble = existingEl || appendMessage('visitor', body, 'sending', 'client:' + cmid);
    state.busy = true;
    sendEl.disabled = true;
    inputEl.value = '';
    setStatus('');
    showTyping();
    function submit() {
      return api('/api/webchat/conversations/' + encodeURIComponent(state.legacyConversationId) + '/messages', {
        method: 'POST',
        headers: { 'X-Webchat-Visitor-Token': state.legacyVisitorToken },
        body: JSON.stringify({ body: body, client_message_id: cmid })
      }, 12000);
    }
    ensureLegacySession().then(function () {
      return submit().catch(function (err) {
        if (!isLegacySessionAuthError(err)) throw err;
        return recoverLegacySession().then(submit);
      });
    }).then(function (data) {
      updateMessage(bubble, body, 'visitor');
      if (data && data.message) {
        renderServerMessage(data.message);
      }
      setStatus('');
      startLegacyWs();
      scheduleLegacyPoll();
      return pollLegacy(true);
    }).catch(function () {
      hideTyping();
      updateMessage(bubble, body, 'visitor', 'failed');
      setStatus('');
      appendRetry(bubble, body, sendConversationMessage);
    }).finally(function () {
      state.busy = false;
      sendEl.disabled = false;
    });
  }
})();

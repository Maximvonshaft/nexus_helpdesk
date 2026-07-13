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
  var avatarUrl = script.getAttribute('data-avatar-url') || new URL('/webchat/demo/assets/speedaf-ai-bot-avatar.png', scriptUrl.origin).toString();
  var accentColor = script.getAttribute('data-accent-color') || '#ff5a00';
  var securityNote = script.getAttribute('data-security-note') || 'Do not share passwords or payment codes.';
  var autoOpen = script.getAttribute('data-auto-open') === 'true';
  var liveVoiceMode = (script.getAttribute('data-live-voice-mode') || 'off').toLowerCase();
  var liveVoiceWsPath = script.getAttribute('data-live-voice-ws-path') || '/webchat/live/ws';
  var liveVoiceLabel = script.getAttribute('data-live-voice-label') || 'VOIP Call';
  var storageKey = 'nexusdesk:webchat:' + apiBase + ':' + tenantKey + ':' + channelKey + ':' + mode;
  var contextKey = storageKey + ':recent-context';
  var sessionKey = storageKey + ':session-id';
  var MAX_CONTEXT_TURNS = 5;
  var LEGACY_POLL_IDLE_MS = Number(script.getAttribute('data-poll-ms') || 4000);
  var LEGACY_POLL_PENDING_MS = Number(script.getAttribute('data-pending-poll-ms') || 350);

var LIVE_VOICE_WORKLET_URL = new URL('/webchat/live-voice-capture-worklet.js?v=1', scriptUrl.origin).toString();
var LIVE_VOICE_PROCESSOR_NAME = 'nexus-live-voice-capture-v1';
var LIVE_VOICE_FRAME_SAMPLES = 320;
var MAX_CAPTURE_PACKET_BYTES = 4096;
// Legacy createScriptProcessor capture was removed; AudioWorklet is mandatory.

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
    + '.nd-webchat-voice-panel{display:none;border-bottom:1px solid #eef2f7;background:#fff7ed;padding:12px 16px 14px}.nd-webchat-voice-panel[data-open=true]{display:block}.nd-webchat-voice-row{display:flex;gap:8px;align-items:center}.nd-webchat-voice-select{flex:1;min-width:0;height:38px;border:1px solid #e2e8f0;border-radius:12px;background:#fff;color:#0f172a;padding:0 10px;font-size:13px;outline:none}.nd-webchat-voice-start{height:38px;min-width:76px;border:0;border-radius:12px;background:#f97316;color:#fff;font-weight:800;cursor:pointer}.nd-webchat-voice-start.stop{background:#dc2626}.nd-webchat-voice-status{margin-top:10px;border:1px solid #fed7aa;background:#fffaf6;color:#9a3412;border-radius:13px;padding:9px 10px;font-size:12.5px;line-height:1.45}.nd-webchat-voice-transcript{margin-top:8px;max-height:126px;overflow:auto}.nd-webchat-voice-msg{margin-top:6px;padding:8px 10px;border-radius:12px;font-size:12.5px;line-height:1.42}.nd-webchat-voice-msg.user{background:#eff6ff;color:#1e3a8a}.nd-webchat-voice-msg.ai{background:#fff;color:#7c2d12;border:1px solid #fed7aa}.nd-webchat-voice-foot{margin-top:8px;color:#64748b;font-size:11.5px}\n'
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
  voiceBtn.setAttribute('aria-label', liveVoiceLabel);
  voiceBtn.setAttribute('data-visible', liveVoiceMode === 'edge-card' ? 'true' : 'false');
  voiceBtn.innerHTML = '<span aria-hidden="true"><svg viewBox="0 0 24 24"><path fill="currentColor" d="M6.62 10.79a15.05 15.05 0 0 0 6.59 6.59l2.2-2.2c.27-.27.67-.36 1.02-.24 1.12.37 2.33.57 3.57.57.55 0 1 .45 1 1V20c0 .55-.45 1-1 1C10.07 21 3 13.93 3 5c0-.55.45-1 1-1h3.5c.55 0 1 .45 1 1 0 1.24.19 2.45.57 3.57.11.35.03.74-.25 1.02l-2.2 2.2z"/></svg></span><span>' + escapeHtml(liveVoiceLabel) + '</span>';
  var close = document.createElement('button');
  close.className = 'nd-webchat-close';
  close.type = 'button';
  close.setAttribute('aria-label', closeLabel);
  close.setAttribute('title', closeLabel);
  header.appendChild(avatar);
  header.appendChild(headerText);
  header.appendChild(voiceBtn);
  header.appendChild(close);

  var voicePanel = document.createElement('div');
  voicePanel.className = 'nd-webchat-voice-panel';
  voicePanel.innerHTML = ''
    + '<div class="nd-webchat-voice-row">'
    + '<select class="nd-webchat-voice-select" aria-label="Voice language">'
    + '<option value="de|de_DE-thorsten-medium|1.0">Deutsch</option>'
    + '<option value="i|if_sara|1.0">Italiano</option>'
    + '<option value="f|ff_siwis|1.0">Francais</option>'
    + '<option value="b|bm_george|1.0">English UK</option>'
    + '</select>'
    + '<button class="nd-webchat-voice-start" type="button">Start</button>'
    + '</div>'
    + '<div class="nd-webchat-voice-status">Tap Start and allow microphone access.</div>'
    + '<div class="nd-webchat-voice-transcript"></div>'
    + '<div class="nd-webchat-voice-foot">Voice support is live. Do not share passwords or payment codes.</div>';
  var messagesEl = document.createElement('div');
  messagesEl.className = 'nd-webchat-messages';
  messagesEl.setAttribute('role', 'log');
  messagesEl.setAttribute('aria-live', 'polite');
  var composerWrap = document.createElement('div');
  composerWrap.className = 'nd-webchat-composer-wrap';
  var formEl = document.createElement('form');
  formEl.className = 'nd-webchat-form';
  var attachIcon = document.createElement('span');
  attachIcon.className = 'nd-webchat-attach';
  attachIcon.setAttribute('aria-hidden', 'true');
  attachIcon.innerHTML = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.4 11.6 12 21a6 6 0 0 1-8.5-8.5l9.9-9.9a4 4 0 0 1 5.7 5.7L9.2 18.2a2 2 0 1 1-2.8-2.8l8.5-8.5"/></svg>';
  var inputEl = document.createElement('input');
  inputEl.className = 'nd-webchat-input';
  inputEl.maxLength = 2000;
  inputEl.placeholder = script.getAttribute('data-input-placeholder') || 'Message';
  inputEl.autocomplete = 'off';
  var sendEl = document.createElement('button');
  sendEl.className = 'nd-webchat-send';
  sendEl.type = 'submit';
  sendEl.setAttribute('aria-label', script.getAttribute('data-send-label') || 'Send');
  sendEl.innerHTML = '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="m4 12 16-8-5 16-3.2-6.2L4 12Z" fill="currentColor"/></svg>';
  formEl.appendChild(attachIcon);
  formEl.appendChild(inputEl);
  formEl.appendChild(sendEl);
  var securityEl = document.createElement('div');
  securityEl.className = 'nd-webchat-security';
  securityEl.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z"/></svg><span>' + escapeHtml(securityNote) + '</span>';
  composerWrap.appendChild(formEl);
  composerWrap.appendChild(securityEl);
  var statusEl = document.createElement('div');
  statusEl.className = 'nd-webchat-status';
  panel.appendChild(header);
  panel.appendChild(voicePanel);
  panel.appendChild(messagesEl);
  panel.appendChild(composerWrap);
  panel.appendChild(statusEl);
  document.body.appendChild(panel);
  document.body.appendChild(button);

  setStatus('');
  if (welcome) appendMessage('agent', welcome);

  button.addEventListener('click', function () { openPanel(); });
  close.addEventListener('click', function () { stopLiveVoice('Voice stopped.'); openPanel(false); });
  voiceBtn.addEventListener('click', function () { toggleVoicePanel(); });
  var voiceStartBtn = voicePanel.querySelector('.nd-webchat-voice-start');
  if (voiceStartBtn) voiceStartBtn.addEventListener('click', startLiveVoice);
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'hidden' && state.liveVoice) {
      stopLiveVoice('Voice stopped while the page is hidden.');
    }
  });
  window.addEventListener('pagehide', function () {
    if (state.liveVoice) stopLiveVoice('Voice stopped.');
  });
  inputEl.addEventListener('compositionstart', function () { state.composing = true; });
  inputEl.addEventListener('compositionend', function () { state.composing = false; });
  messagesEl.addEventListener('scroll', function () { state.userNearBottom = isNearBottom(); });
  formEl.addEventListener('submit', function (event) {
    event.preventDefault();
    if (state.composing || event.isComposing || state.busy) return;
    var body = inputEl.value.trim();
    if (!body) return;
    sendConversationMessage(body);
  });

  restoreLegacySession();
  bindPageTriggers();
  exposePublicApi();
  if (autoOpen) {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', function () { openPanel(true); }, { once: true });
    } else {
      setTimeout(function () { openPanel(true); }, 0);
    }
  }

  function setStatus(text) {
    statusEl.textContent = text;
    panel.setAttribute('data-status', String(text || '').toLowerCase().replace(/\s+/g, '-'));
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
      var form = findClosest(event.target, '[data-webchat-form]');
      if (!form) return;
      event.preventDefault();
      var inputSelector = form.getAttribute('data-webchat-input') || 'input,textarea';
      var source = form.querySelector(inputSelector) || document.querySelector(inputSelector);
      var value = source && source.value ? String(source.value).trim() : '';
      if (!value) {
        if (source && source.focus) source.focus();
        return;
      }
      if (form.getAttribute('data-webchat-submit') === 'prefill') {
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
    if (liveVoiceMode !== 'edge-card') return;
    openPanel(true);
    state.voiceOpen = typeof force === 'boolean' ? force : !state.voiceOpen;
    voicePanel.setAttribute('data-open', state.voiceOpen ? 'true' : 'false');
    if (!state.voiceOpen && state.liveVoice) stopLiveVoice('Voice stopped.');
  }

  function voiceStatus(text) {
    var el = voicePanel.querySelector('.nd-webchat-voice-status');
    if (el) el.textContent = text || '';
  }

  function addVoiceTranscript(kind, text) {
    if (!text) return;
    var wrap = voicePanel.querySelector('.nd-webchat-voice-transcript');
    if (!wrap) return;
    var div = document.createElement('div');
    div.className = 'nd-webchat-voice-msg ' + kind;
    div.textContent = text;
    wrap.appendChild(div);
    wrap.scrollTop = wrap.scrollHeight;
  }

  function stopLivePlayback(liveOverride) {
    var live = liveOverride || state.liveVoice;
    if (!live) return;
    (live.playingSources || []).forEach(function (sourceNode) {
      try { sourceNode.stop(); } catch (err) {}
      try { sourceNode.disconnect(); } catch (err) {}
    });
    live.playingSources = [];
    if (live.audioContext && live.audioContext.state !== 'closed') {
      live.nextPlayTime = live.audioContext.currentTime + 0.03;
    }
  }

  function resetLiveVoice(statusText) {
    voiceBtn.classList.remove('is-live');
    var startBtn = voicePanel.querySelector('.nd-webchat-voice-start');
    if (startBtn) {
      startBtn.classList.remove('stop');
      startBtn.textContent = 'Start';
    }
    if (statusText) voiceStatus(statusText);
  }

  function releaseLiveVoice(live, statusText, closeSocket) {
    if (!live || live.released) {
      if (statusText && !state.liveVoice) resetLiveVoice(statusText);
      return;
    }
    live.released = true;
    stopLivePlayback(live);
    if (state.liveVoice === live) state.liveVoice = null;

    if (live.captureNode) {
      try { live.captureNode.port.onmessage = null; } catch (err) {}
      try { live.captureNode.port.postMessage({ type: 'stop' }); } catch (err) {}
      try { live.captureNode.port.close(); } catch (err) {}
      try { live.captureNode.disconnect(); } catch (err) {}
    }
    try { if (live.source) live.source.disconnect(); } catch (err) {}
    try {
      if (live.stream) live.stream.getTracks().forEach(function (track) { track.stop(); });
    } catch (err) {}

    if (live.ws) {
      live.ws.onopen = null;
      live.ws.onmessage = null;
      live.ws.onerror = null;
      live.ws.onclose = null;
      if (closeSocket) {
        try {
if (live.ws.readyState < WebSocket.CLOSING) live.ws.close(1000, 'client_stop');
        } catch (err) {}
      }
    }

    if (live.audioContext && live.audioContext.state !== 'closed') {
      try { Promise.resolve(live.audioContext.close()).catch(function () {}); } catch (err) {}
    }
    resetLiveVoice(statusText || 'Voice stopped.');
  }

  function stopLiveVoice(statusText) {
    releaseLiveVoice(state.liveVoice, statusText || 'Voice stopped.', true);
  }

  function safeLiveVoicePath(path) {
    var value = String(path || '').trim();
    if (!value || value.indexOf('://') !== -1 || value.charAt(0) !== '/') return '/webchat/live/ws';
    return value;
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

  function playPcm16(arrayBuffer, sampleRate) {
    var live = state.liveVoice;
    if (!live || !live.audioContext) return Promise.resolve();
    var pcm = new Int16Array(arrayBuffer);
    if (!pcm.length) return Promise.resolve();
    var float32 = new Float32Array(pcm.length);
    for (var index = 0; index < pcm.length; index += 1) {
      float32[index] = pcm[index] / 32768;
    }
    var audioBuffer = live.audioContext.createBuffer(1, float32.length, sampleRate || 24000);
    audioBuffer.copyToChannel(float32, 0);
    var sourceNode = live.audioContext.createBufferSource();
    sourceNode.buffer = audioBuffer;
    sourceNode.connect(live.audioContext.destination);
    live.playingSources.push(sourceNode);
    sourceNode.onended = function () {
      live.playingSources = live.playingSources.filter(function (item) { return item !== sourceNode; });
    };
    var startAt = Math.max(live.audioContext.currentTime + 0.03, live.nextPlayTime || 0);
    sourceNode.start(startAt);
    live.nextPlayTime = startAt + audioBuffer.duration;
    return Promise.resolve();
  }

  function handleLiveMessage(event) {
    var live = state.liveVoice;
    if (!live) return Promise.resolve();
    if (typeof event.data !== 'string') {
      return playPcm16(event.data, live.currentTtsSampleRate || 24000);
    }
    var payload = null;
    try { payload = JSON.parse(event.data); } catch (err) {}
    if (!payload) return Promise.resolve();
    if (payload.type === 'barge_in') {
      stopLivePlayback();
      voiceStatus('Listening...');
    }
    if (payload.type === 'speech_start') voiceStatus('Listening...');
    if (payload.type === 'stt_start') voiceStatus('Transcribing...');
    if (payload.type === 'stt_result') {
      addVoiceTranscript('user', payload.text);
      voiceStatus('Thinking...');
    }
    if (payload.type === 'ai_answer') {
      addVoiceTranscript('ai', payload.answer);
      voiceStatus('Speaking...');
    }
    if (payload.type === 'tts_start' && payload.sample_rate) {
      live.currentTtsSampleRate = payload.sample_rate;
      if (live.audioContext) live.nextPlayTime = live.audioContext.currentTime + 0.03;
    }
    if (payload.type === 'tts_end') voiceStatus('Ready. You can speak again.');
    if (payload.type === 'turn_error') voiceStatus('Voice error: ' + (payload.message || payload.error || 'unknown'));
    return Promise.resolve();
  }

  function openLiveVoiceSocket(live, langCode, voice, speed) {
    return new Promise(function (resolve, reject) {
      if (live.released || state.liveVoice !== live) {
        reject(new Error('Voice start was cancelled.'));
        return;
      }
      var socket;
      try {
        socket = new WebSocket(buildLiveWsUrl(langCode, voice, speed));
      } catch (err) {
        reject(err);
        return;
      }
      live.ws = socket;
      socket.binaryType = 'arraybuffer';
      socket.onmessage = function (event) { handleLiveMessage(event); };
      socket.onopen = function () {
        if (live.released || state.liveVoice !== live) {
try { socket.close(1000, 'cancelled'); } catch (err) {}
reject(new Error('Voice start was cancelled.'));
return;
        }
        resolve(socket);
      };
      socket.onerror = function () {
        reject(new Error('Voice connection failed.'));
      };
      socket.onclose = function () {
        if (!live.released) releaseLiveVoice(live, 'Voice disconnected.', false);
      };
    });
  }

  function startLiveVoice() {
    if (liveVoiceMode !== 'edge-card') return;
    toggleVoicePanel(true);
    if (state.liveVoice) {
      stopLiveVoice('Voice stopped.');
      return;
    }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      voiceStatus('Microphone is not available in this browser.');
      return;
    }
    var AudioContextConstructor = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextConstructor || !window.AudioWorkletNode) {
      voiceStatus('AudioWorklet support is required for voice capture.');
      return;
    }

    var startBtn = voicePanel.querySelector('.nd-webchat-voice-start');
    var presetEl = voicePanel.querySelector('.nd-webchat-voice-select');
    var preset = (presetEl && presetEl.value ? presetEl.value : 'de|de_DE-thorsten-medium|1.0').split('|');
    var langCode = preset[0] || 'de';
    var voice = preset[1] || 'de_DE-thorsten-medium';
    var speed = preset[2] || '1.0';
    var live = {
      ws: null,
      audioContext: null,
      source: null,
      captureNode: null,
      stream: null,
      playingSources: [],
      currentTtsSampleRate: 24000,
      nextPlayTime: 0,
      released: false
    };
    state.liveVoice = live;
    voiceStatus('Preparing secure voice capture...');

    Promise.resolve().then(function () {
      if (live.released || state.liveVoice !== live) throw new Error('Voice start was cancelled.');
      live.audioContext = new AudioContextConstructor();
      if (!live.audioContext.audioWorklet || !live.audioContext.audioWorklet.addModule) {
        throw new Error('AudioWorklet support is unavailable.');
      }
      return live.audioContext.resume();
    }).then(function () {
      if (live.released || state.liveVoice !== live) throw new Error('Voice start was cancelled.');
      return live.audioContext.audioWorklet.addModule(LIVE_VOICE_WORKLET_URL);
    }).then(function () {
      if (live.released || state.liveVoice !== live) throw new Error('Voice start was cancelled.');
      return openLiveVoiceSocket(live, langCode, voice, speed);
    }).then(function () {
      if (live.released || state.liveVoice !== live) throw new Error('Voice start was cancelled.');
      voiceStatus('Connected. Requesting microphone access...');
      return navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }
      });
    }).then(function (mediaStream) {
      if (live.released || state.liveVoice !== live) {
        mediaStream.getTracks().forEach(function (track) { track.stop(); });
        throw new Error('Voice start was cancelled.');
      }
      live.stream = mediaStream;
      live.source = live.audioContext.createMediaStreamSource(mediaStream);
      live.captureNode = new AudioWorkletNode(live.audioContext, LIVE_VOICE_PROCESSOR_NAME, {
        numberOfInputs: 1,
        numberOfOutputs: 1,
        outputChannelCount: [1],
        processorOptions: {
outputSampleRate: 16000,
frameSamples: LIVE_VOICE_FRAME_SAMPLES
        }
      });
      live.captureNode.port.onmessage = function (event) {
        if (live.released || state.liveVoice !== live) return;
        var data = event && event.data;
        var packet = data && data.type === 'pcm16' ? data.buffer : null;
        if (!(packet instanceof ArrayBuffer) || packet.byteLength === 0) return;
        if (packet.byteLength > MAX_CAPTURE_PACKET_BYTES) {
releaseLiveVoice(live, 'Voice capture stopped because an audio packet exceeded the safety limit.', true);
return;
        }
        if (live.ws && live.ws.readyState === WebSocket.OPEN) live.ws.send(packet);
      };
      live.source.connect(live.captureNode);
      live.captureNode.connect(live.audioContext.destination);
      voiceBtn.classList.add('is-live');
      if (startBtn) {
        startBtn.classList.add('stop');
        startBtn.textContent = 'Stop';
      }
      voiceStatus('Listening...');
    }).catch(function (err) {
      if (live.released && state.liveVoice !== live) return;
      var errorName = err && err.name ? String(err.name) : '';
      var message = err && err.message ? String(err.message) : String(err || 'unknown');
      var status = 'Voice start failed: ' + message;
      if (errorName === 'NotAllowedError' || errorName === 'PermissionDeniedError') {
        status = 'Microphone access was denied.';
      } else if (message.indexOf('AudioWorklet') !== -1) {
        status = 'AudioWorklet support is unavailable.';
      }
      releaseLiveVoice(live, status, true);
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
    }).catch(function () {
      setStatus('');
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
    ensureLegacySession().then(function () {
      return api('/api/webchat/conversations/' + encodeURIComponent(state.legacyConversationId) + '/messages', {
        method: 'POST',
        headers: { 'X-Webchat-Visitor-Token': state.legacyVisitorToken },
        body: JSON.stringify({ body: body, client_message_id: cmid })
      }, 12000);
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

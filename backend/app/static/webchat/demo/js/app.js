(function () {
  'use strict';

  const CONFIG = Object.freeze({
    apiBase: window.location.origin.replace(/\/$/, ''),
    tenantKey: 'default',
    channelKey: 'website',
    timeoutMs: 240000,
    sessionKey: 'speedaf-demo:webchat:session-id',
    contextKey: 'speedaf-demo:webchat:recent-context',
    fastReplyPath: '/api/webchat/fast-reply'
  });

  const panel = document.getElementById('chatPanel');
  const closeBtn = document.getElementById('closeChat');
  const openBtn = document.getElementById('floatingChat');
  const backdrop = document.getElementById('chatBackdrop');
  const log = document.getElementById('messageLog');
  const input = document.getElementById('chatInput');
  const sendBtn = document.getElementById('sendBtn');
  const mobileMenuBtn = document.getElementById('mobileMenuBtn');
  const mobileNav = document.getElementById('mobileNav');
  const trackForm = document.getElementById('trackForm');
  const trackingInput = document.getElementById('trackingInput');

  let busy = false;
  let recentContext = loadContext();
  const sessionId = loadSessionId();

  window.SpeedafSiteConfig = {
    API_BASE_URL: CONFIG.apiBase,
    tenant_key: CONFIG.tenantKey,
    channel_key: CONFIG.channelKey,
    session_id: sessionId,
    requestTimeoutMs: CONFIG.timeoutMs
  };

  if (openBtn) openBtn.addEventListener('click', openChat);
  if (closeBtn) closeBtn.addEventListener('click', closeChat);
  if (backdrop) backdrop.addEventListener('click', closeChat);
  if (sendBtn) sendBtn.addEventListener('click', submitMessage);

  if (input) {
    input.addEventListener('keydown', function (event) {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        submitMessage();
      }
    });
  }

  Array.from(document.querySelectorAll('[data-open-chat]')).forEach(function (node) {
    node.addEventListener('click', function (event) {
      event.preventDefault();
      closeMobileMenu();
      openChat();
    });
  });

  Array.from(document.querySelectorAll('.quick-btn[data-action]')).forEach(function (button) {
    button.addEventListener('click', function () {
      const action = button.getAttribute('data-action') || 'general';
      const message = button.textContent ? button.textContent.trim() : action;
      if (input) input.value = message;
      submitMessage();
    });
  });

  if (trackForm && trackingInput) {
    trackForm.addEventListener('submit', function (event) {
      event.preventDefault();
      const value = trackingInput.value.trim();
      if (!value) return trackingInput.focus();
      openChat();
      if (input) input.value = 'Track parcel ' + value;
      submitMessage();
    });
  }

  if (mobileMenuBtn && mobileNav) {
    mobileMenuBtn.addEventListener('click', function () {
      const open = mobileNav.classList.toggle('is-open');
      mobileNav.hidden = !open;
      mobileMenuBtn.setAttribute('aria-expanded', String(open));
    });
  }

  function openChat() {
    if (!panel) return;
    panel.classList.remove('is-closed');
    panel.setAttribute('aria-hidden', 'false');
    document.body.classList.add('chat-open');
    if (openBtn) openBtn.setAttribute('aria-expanded', 'true');
    if (backdrop) backdrop.hidden = false;
    setTimeout(function () { if (input) input.focus(); }, 100);
  }

  function closeChat() {
    if (!panel) return;
    panel.classList.add('is-closed');
    panel.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('chat-open');
    if (openBtn) openBtn.setAttribute('aria-expanded', 'false');
    if (backdrop) backdrop.hidden = true;
  }

  function closeMobileMenu() {
    if (!mobileMenuBtn || !mobileNav) return;
    mobileNav.classList.remove('is-open');
    mobileNav.hidden = true;
    mobileMenuBtn.setAttribute('aria-expanded', 'false');
  }

  function submitMessage() {
    if (busy || !input || !log) return;
    const body = input.value.trim();
    if (!body) return;

    appendMessage('user', body);
    input.value = '';
    busy = true;
    if (sendBtn) sendBtn.disabled = true;
    showTyping();

    sendFastReply(body)
      .then(function (data) {
        hideTyping();
        const reply = data && data.reply ? String(data.reply).trim() : '';
        const debugContext = data && data.__debug_context ? data.__debug_context : makeDebugContext({ error_code: 'render_error' });
        try {
          appendMessage('bot', reply, { handoff: Boolean(data.handoff_required) });
          remember(body, reply);
        } catch (renderError) {
          reportDemoError('webchat_demo_render_error', renderError, withDebug(debugContext, { error_code: 'render_error' }));
        }
      })
      .catch(function (error) {
        hideTyping();
        reportDemoError('webchat_demo_api_error', error, error && error.debug_context);
        appendMessage('bot', userVisibleErrorMessage(error));
      })
      .finally(function () {
        busy = false;
        if (sendBtn) sendBtn.disabled = false;
        if (input) input.focus();
      });
  }

  function makeDebugContext(extra) {
    return Object.assign({
      session_id: sessionId,
      tenant_key: CONFIG.tenantKey,
      channel_key: CONFIG.channelKey,
      request_path: CONFIG.fastReplyPath,
      http_status: null,
      backend_error_code: null,
      client_message_id: null,
      error_code: null
    }, extra || {});
  }

  function withDebug(base, extra) {
    return Object.assign({}, base || {}, extra || {});
  }

  function classifiedError(errorCode, message, debugContext) {
    const error = new Error(message || errorCode);
    error.name = 'WebchatDemoError';
    error.error_code = errorCode;
    error.debug_context = withDebug(debugContext, { error_code: errorCode });
    if (error.debug_context.http_status) error.status = error.debug_context.http_status;
    return error;
  }

  function reportDemoError(label, error, debugContext) {
    const safeDebug = withDebug(debugContext || (error && error.debug_context), {
      error_type: error && error.name ? error.name : 'Error',
      error_message: error && error.message ? String(error.message).slice(0, 160) : undefined
    });
    if (window.console && typeof window.console.error === 'function') {
      window.console.error(label, safeDebug);
    }
  }

  function userVisibleErrorMessage(error) {
    const code = error && (error.error_code || (error.debug_context && error.debug_context.error_code));
    if (code === 'network_timeout') return 'Connection timed out. Please try again.';
    if (code === 'origin_forbidden' || code === 'http_403') return 'Chat is not allowed from this website. Please contact support.';
    if (code === 'empty_reply') return 'The assistant returned an empty reply. Please retry.';
    if (code === 'api_error_code') return 'The assistant is temporarily unavailable. Please retry.';
    if (code === 'render_error') return 'The reply was received but could not be displayed. Please refresh and try again.';
    return 'Connection issue. Please try again.';
  }

  function submitDebugPayload(data, debugContext) {
    if (data && typeof data === 'object') {
      data.__debug_context = debugContext;
    }
    return data;
  }

  function backendErrorCode(data) {
    if (!data || typeof data !== 'object') return null;
    if (typeof data.error_code === 'string' && data.error_code) return data.error_code;
    if (data.detail && typeof data.detail === 'object' && typeof data.detail.code === 'string') return data.detail.code;
    if (typeof data.detail === 'string' && data.detail) return data.detail.slice(0, 120);
    return null;
  }

  function sendFastReply(body) {
    const controller = new AbortController();
    const timer = setTimeout(function () { controller.abort(); }, CONFIG.timeoutMs);
    const clientMessageId = makeId('msg');
    const debugBase = makeDebugContext({ client_message_id: clientMessageId });
    const payload = {
      tenant_key: CONFIG.tenantKey,
      channel_key: CONFIG.channelKey,
      session_id: sessionId,
      client_message_id: clientMessageId,
      body: body,
      recent_context: recentContext.slice(-10)
    };

    return fetch(CONFIG.apiBase + CONFIG.fastReplyPath, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: controller.signal
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        const apiCode = backendErrorCode(data);
        const debugContext = withDebug(debugBase, { http_status: res.status, backend_error_code: apiCode });
        if (!res.ok) {
          const httpCode = res.status === 403 ? 'origin_forbidden' : 'http_error';
          throw classifiedError(httpCode, 'http_' + res.status, debugContext);
        }
        if (!data || data.ok !== true) {
          throw classifiedError(apiCode ? 'api_error_code' : 'api_not_ok', apiCode || 'api_not_ok', debugContext);
        }
        const reply = data.reply ? String(data.reply).trim() : '';
        if (!reply) throw classifiedError('empty_reply', 'empty_reply', debugContext);
        return submitDebugPayload(data, debugContext);
      });
    }).catch(function (error) {
      if (error && error.debug_context) throw error;
      if (error && error.name === 'AbortError') {
        throw classifiedError('network_timeout', 'network_timeout', debugBase);
      }
      throw classifiedError('network_error', error && error.message ? error.message : 'network_error', debugBase);
    }).finally(function () {
      clearTimeout(timer);
    });
  }

  function appendMessage(role, text, options) {
    const row = document.createElement('div');
    row.className = 'message-row ' + (role === 'user' ? 'user' : 'bot');
    const bubble = document.createElement('div');
    bubble.className = 'bubble ' + (role === 'user' ? 'user-bubble' : '');
    if (options && options.handoff && role !== 'user') bubble.classList.add('handoff-bubble');
    bubble.appendChild(document.createTextNode(text));
    const time = document.createElement('span');
    time.className = 'time';
    time.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    bubble.appendChild(time);
    row.appendChild(bubble);
    log.appendChild(row);
    log.scrollTop = log.scrollHeight;
  }

  function showTyping() {
    hideTyping();
    if (!log) return;
    const row = document.createElement('div');
    row.className = 'message-row bot dynamic-typing';
    const typing = document.createElement('div');
    typing.className = 'typing';
    for (let i = 0; i < 3; i += 1) typing.appendChild(document.createElement('i'));
    row.appendChild(typing);
    log.appendChild(row);
    log.scrollTop = log.scrollHeight;
  }

  function hideTyping() {
    if (!log) return;
    log.querySelectorAll('.dynamic-typing').forEach(function (node) { node.remove(); });
  }

  function remember(userText, replyText) {
    recentContext.push({ role: 'visitor', text: String(userText || '').slice(0, 500) });
    recentContext.push({ role: 'agent', text: String(replyText || '').slice(0, 500) });
    recentContext = recentContext.filter(function (item) { return item && item.text; }).slice(-20);
    try { sessionStorage.setItem(CONFIG.contextKey, JSON.stringify(recentContext)); } catch (_) {}
  }

  function loadContext() {
    try {
      const parsed = JSON.parse(sessionStorage.getItem(CONFIG.contextKey) || '[]');
      return Array.isArray(parsed) ? parsed.slice(-20) : [];
    } catch (_) {
      return [];
    }
  }

  function loadSessionId() {
    try {
      const existing = sessionStorage.getItem(CONFIG.sessionKey);
      if (existing) return existing;
      const created = makeId('session');
      sessionStorage.setItem(CONFIG.sessionKey, created);
      return created;
    } catch (_) {
      return makeId('session');
    }
  }

  function makeId(prefix) {
    return prefix + '_' + Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);
  }
})();
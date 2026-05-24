(function () {
  'use strict';

  const CONFIG = Object.freeze({
    apiBase: window.location.origin.replace(/\/$/, ''),
    tenantKey: 'default',
    channelKey: 'website',
    timeoutMs: 240000,
    sessionKey: 'speedaf-demo:webchat:session-id',
    contextKey: 'speedaf-demo:webchat:recent-context'
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
        const reply = data && data.ok === true && data.reply ? String(data.reply).trim() : '';
        if (!reply) throw new Error('empty_reply');
        appendMessage('bot', reply, { handoff: Boolean(data.handoff_required) });
        remember(body, reply);
      })
      .catch(function () {
        hideTyping();
        appendMessage('bot', 'Connection issue. Please try again.');
      })
      .finally(function () {
        busy = false;
        if (sendBtn) sendBtn.disabled = false;
        if (input) input.focus();
      });
  }

  function sendFastReply(body) {
    const controller = new AbortController();
    const timer = setTimeout(function () { controller.abort(); }, CONFIG.timeoutMs);
    const payload = {
      tenant_key: CONFIG.tenantKey,
      channel_key: CONFIG.channelKey,
      session_id: sessionId,
      client_message_id: makeId('msg'),
      body: body,
      recent_context: recentContext.slice(-10)
    };

    return fetch(CONFIG.apiBase + '/api/webchat/fast-reply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: controller.signal
    }).then(function (res) {
      return res.json().then(function (data) {
        if (!res.ok) throw new Error('http_' + res.status);
        return data;
      });
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
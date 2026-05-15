(function () {
  'use strict';

  const SiteConfig = Object.freeze({
    API_BASE_URL: window.location.origin,
    tenant_key: 'speedaf_public_site',
    channel_key: 'speedaf_webchat',
    session_id: makeId('session'),
    requestTimeoutMs: 90000
  });

  window.SpeedafSiteConfig = SiteConfig;

  const panel = document.getElementById('chatPanel');
  const closeChat = document.getElementById('closeChat');
  const floatingChat = document.getElementById('floatingChat');
  const chatBackdrop = document.getElementById('chatBackdrop');
  const messageLog = document.getElementById('messageLog');
  const input = document.getElementById('chatInput');
  const sendBtn = document.getElementById('sendBtn');
  const mobileMenuBtn = document.getElementById('mobileMenuBtn');
  const mobileNav = document.getElementById('mobileNav');
  const trackForm = document.getElementById('trackForm');
  const trackingInput = document.getElementById('trackingInput');
  const trackResult = document.getElementById('trackResult');
  const openChatTriggers = Array.from(document.querySelectorAll('[data-open-chat]'));

  let lastIntent = 'general';
  let busy = false;

  const quickActionMessages = Object.freeze({
    track: 'I want to track a parcel.',
    redelivery: 'I need help with redelivery.',
    refuse: 'I want to refuse a delivery.',
    problem: 'I have a delivery issue.',
    human: 'I need a human agent.'
  });

  Array.from(document.querySelectorAll('.chat-panel .quick-btn[data-action]')).forEach((button) => {
    button.addEventListener('click', () => {
      const action = button.dataset.action || 'general';
      lastIntent = action;
      input.value = quickActionMessages[action] || 'I need help.';
      submitMessage();
    });
  });

  if (sendBtn) sendBtn.addEventListener('click', submitMessage);
  if (input) {
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        submitMessage();
      }
    });
  }

  if (floatingChat) floatingChat.addEventListener('click', openChat);
  if (closeChat) closeChat.addEventListener('click', closeChatPanel);
  if (chatBackdrop) chatBackdrop.addEventListener('click', closeChatPanel);

  openChatTriggers.forEach((trigger) => {
    trigger.addEventListener('click', (event) => {
      event.preventDefault();
      closeMobileMenu();
      openChat();
    });
  });

  if (mobileMenuBtn && mobileNav) {
    mobileMenuBtn.addEventListener('click', () => {
      const open = mobileNav.classList.toggle('is-open');
      mobileNav.hidden = !open;
      mobileMenuBtn.setAttribute('aria-expanded', String(open));
    });
    mobileNav.querySelectorAll('a').forEach((link) => link.addEventListener('click', closeMobileMenu));
  }

  if (trackForm && trackingInput) {
    trackForm.addEventListener('submit', (event) => {
      event.preventDefault();
      const value = trackingInput.value.trim();
      if (!value) {
        trackingInput.focus();
        return;
      }
      if (trackResult) trackResult.hidden = false;
      lastIntent = 'track';
      openChat();
      input.value = 'Track parcel ' + value;
      submitMessage();
    });
  }

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      if (panel && !panel.classList.contains('is-closed')) {
        closeChatPanel();
        floatingChat && floatingChat.focus();
      } else {
        closeMobileMenu();
      }
    }
  });

  function openChat() {
    if (!panel) return;
    panel.classList.remove('is-closed');
    panel.setAttribute('aria-hidden', 'false');
    floatingChat && floatingChat.setAttribute('aria-expanded', 'true');
    document.body.classList.add('chat-open');
    if (chatBackdrop) chatBackdrop.hidden = false;
    window.setTimeout(() => input && input.focus(), 180);
  }

  function closeChatPanel() {
    if (!panel) return;
    panel.classList.add('is-closed');
    panel.setAttribute('aria-hidden', 'true');
    floatingChat && floatingChat.setAttribute('aria-expanded', 'false');
    document.body.classList.remove('chat-open');
    if (chatBackdrop) {
      window.setTimeout(() => {
        if (panel.classList.contains('is-closed')) chatBackdrop.hidden = true;
      }, 240);
    }
  }

  function closeMobileMenu() {
    if (!mobileMenuBtn || !mobileNav) return;
    mobileNav.classList.remove('is-open');
    mobileNav.hidden = true;
    mobileMenuBtn.setAttribute('aria-expanded', 'false');
  }

  function submitMessage() {
    if (busy || !input || !messageLog) return;
    const raw = input.value.trim();
    if (!raw) {
      input.focus();
      return;
    }
    appendMessage('user', raw);
    input.value = '';
    busy = true;
    addTyping();
    getReply(raw)
      .then((reply) => {
        removeTyping();
        appendMessage('bot', reply.message, { handoff: Boolean(reply.handoff_required) });
        if (reply.intent) lastIntent = reply.intent;
      })
      .catch(() => {
        removeTyping();
        appendMessage('bot', 'Connection issue. Please try again.');
      })
      .finally(() => {
        busy = false;
        input.focus();
      });
  }

  async function getReply(text) {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), SiteConfig.requestTimeoutMs);
    const payload = {
      tenant_key: SiteConfig.tenant_key,
      channel_key: SiteConfig.channel_key,
      session_id: SiteConfig.session_id,
      client_message_id: makeId('msg'),
      body: text,
      recent_context: []
    };

    try {
      const res = await fetch(SiteConfig.API_BASE_URL.replace(/\/$/, '') + '/api/webchat/fast-reply', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: controller.signal
      });

      if (!res.ok) throw new Error('support_api_http_error');
      const data = await res.json();
      return normalizeSupportReply(data);
    } finally {
      window.clearTimeout(timer);
    }
  }

  function normalizeSupportReply(data) {
    if (!data || data.ok !== true || typeof data.reply !== 'string' || !data.reply.trim()) {
      throw new Error('invalid_support_reply');
    }

    return {
      message: data.reply.trim(),
      handoff_required: Boolean(data.handoff_required),
      intent: data.intent || lastIntent || 'general'
    };
  }

  function appendMessage(role, text, options) {
    if (!messageLog) return;
    const row = document.createElement('div');
    row.className = 'message-row ' + (role === 'user' ? 'user' : 'bot');

    if (role !== 'user') {
      const avatar = document.createElement('div');
      avatar.className = 'mini-avatar';
      const img = document.createElement('img');
      img.src = 'assets/speedaf-ai-bot-avatar.png';
      img.alt = '';
      avatar.appendChild(img);
      row.appendChild(avatar);
    }

    const bubble = document.createElement('div');
    bubble.className = 'bubble ' + (role === 'user' ? 'user-bubble' : '');
    if (options && options.handoff && role !== 'user') bubble.classList.add('handoff-bubble');

    if (options && options.handoff && role !== 'user') {
      const strong = document.createElement('strong');
      strong.textContent = 'Agent follow-up prepared';
      bubble.appendChild(strong);
    }

    bubble.appendChild(document.createTextNode(text));
    const time = document.createElement('span');
    time.className = 'time';
    time.textContent = currentTime() + (role === 'user' ? ' ✓✓' : '');
    bubble.appendChild(time);
    row.appendChild(bubble);
    messageLog.appendChild(row);
    messageLog.scrollTop = messageLog.scrollHeight;
  }

  function addTyping() {
    if (!messageLog) return;
    removeTyping();
    const row = document.createElement('div');
    row.className = 'message-row bot dynamic-typing';
    const avatar = document.createElement('div');
    avatar.className = 'mini-avatar';
    const img = document.createElement('img');
    img.src = 'assets/speedaf-ai-bot-avatar.png';
    img.alt = '';
    avatar.appendChild(img);
    const typing = document.createElement('div');
    typing.className = 'typing';
    typing.setAttribute('aria-label', 'AI is typing');
    for (let i = 0; i < 3; i += 1) typing.appendChild(document.createElement('i'));
    row.appendChild(avatar);
    row.appendChild(typing);
    messageLog.appendChild(row);
    messageLog.scrollTop = messageLog.scrollHeight;
  }

  function removeTyping() {
    if (!messageLog) return;
    messageLog.querySelectorAll('.dynamic-typing').forEach((node) => node.remove());
  }

  function currentTime() {
    const now = new Date();
    return now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  function makeId(prefix) {
    return prefix + '_' + Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);
  }
})();
(function () {
  'use strict';
  if (window.__NEXUSDESK_WEBCHAT_VOICE_ENTRY_LOADED__) return;
  window.__NEXUSDESK_WEBCHAT_VOICE_ENTRY_LOADED__ = true;

  var source = document.currentScript || (function () {
    var scripts = document.getElementsByTagName('script');
    return scripts[scripts.length - 1];
  })();
  var sourceUrl = new URL(source.src, window.location.href);

  if (window.__NEXUSDESK_WEBCHAT_LOADED__) return;

  var widget = document.createElement('script');
  widget.src = new URL('/webchat/widget.js', sourceUrl.origin).toString();
  widget.defer = true;

  [
    'data-api-base',
    'data-tenant',
    'data-channel',
    'data-locale',
    'data-title',
    'data-subtitle',
    'data-button-label',
    'data-accent-color',
    'data-security-note',
    'data-auto-open',
    'data-live-voice-mode',
    'data-live-voice-label',
    'data-websocket',
    'data-poll-ms',
    'data-pending-poll-ms'
  ].forEach(function (name) {
    var value = source.getAttribute(name);
    if (value !== null) widget.setAttribute(name, value);
  });

  if (!widget.getAttribute('data-title')) widget.setAttribute('data-title', 'Speedaf Support');
  if (!widget.getAttribute('data-subtitle')) widget.setAttribute('data-subtitle', 'AI support · human handoff when needed');
  if (!widget.getAttribute('data-button-label')) widget.setAttribute('data-button-label', 'Chat with Speedaf');
  if (!widget.getAttribute('data-live-voice-mode')) widget.setAttribute('data-live-voice-mode', 'off');
  if (!widget.getAttribute('data-live-voice-label')) {
    widget.setAttribute('data-live-voice-label', source.getAttribute('data-voice-label') || 'VOIP Call');
  }

  source.parentNode.insertBefore(widget, source.nextSibling);
})();

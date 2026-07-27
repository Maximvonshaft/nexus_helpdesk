(function () {
  'use strict';

  if (window.__NEXUSDESK_WEBCHAT_BOOTSTRAP_LOADED__) return;
  window.__NEXUSDESK_WEBCHAT_BOOTSTRAP_LOADED__ = true;

  var loader = document.currentScript || (function () {
    var scripts = document.getElementsByTagName('script');
    return scripts[scripts.length - 1];
  })();
  var loaderUrl = new URL(loader.src, window.location.href);
  var apiBase = (loader.getAttribute('data-api-base') || loaderUrl.origin).replace(/\/$/, '');
  var webCallOrigin = loaderUrl.origin;
  var originalOpen = window.open;
  var pendingWebCallWindow = null;
  var bridgeTimer = null;
  var bridgeActive = false;

  function canonicalWebCallTarget(target) {
    if (typeof target !== 'string' || target.indexOf('/webcall/') !== 0) return null;
    return new URL(target, webCallOrigin + '/').toString();
  }

  function restoreOpenBridge(closePendingWindow) {
    if (bridgeTimer) window.clearTimeout(bridgeTimer);
    bridgeTimer = null;
    if (bridgeActive && window.open === interceptWebCallOpen) {
      window.open = originalOpen;
    }
    bridgeActive = false;
    if (closePendingWindow && pendingWebCallWindow) {
      try {
        if (!pendingWebCallWindow.closed) pendingWebCallWindow.close();
      } catch (err) {}
      pendingWebCallWindow = null;
    }
  }

  function interceptWebCallOpen(target, name, features) {
    var canonicalTarget = canonicalWebCallTarget(target);
    if (!canonicalTarget) return originalOpen.call(window, target, name, features);

    var reserved = pendingWebCallWindow;
    pendingWebCallWindow = null;
    restoreOpenBridge(false);
    if (reserved) {
      try {
        if (!reserved.closed) {
          reserved.location.replace(canonicalTarget);
          return reserved;
        }
      } catch (err) {}
    }

    var opened = originalOpen.call(window, canonicalTarget, name, features);
    if (opened) return opened;

    // The VoiceSession already exists. Preserve the customer journey when the
    // browser blocks a delayed popup, but always navigate to the Nexus-owned
    // WebCall origin rather than the embedding site's relative /webcall route.
    window.location.assign(canonicalTarget);
    return window;
  }

  function reserveWebCallWindow() {
    if (bridgeActive) return;
    try {
      pendingWebCallWindow = originalOpen.call(
        window,
        'about:blank',
        'nexusdesk-webcall'
      );
      if (pendingWebCallWindow) {
        pendingWebCallWindow.opener = null;
        pendingWebCallWindow.document.title = 'Preparing secure WebCall';
      }
    } catch (err) {
      pendingWebCallWindow = null;
    }
    bridgeActive = true;
    window.open = interceptWebCallOpen;
    bridgeTimer = window.setTimeout(function () {
      restoreOpenBridge(true);
    }, 20000);
  }

  function onDocumentClick(event) {
    var target = event.target;
    var trigger = target && target.closest
      ? target.closest('.nd-webchat-voice-start')
      : null;
    if (!trigger || trigger.disabled) return;
    reserveWebCallWindow();
  }

  document.addEventListener('click', onDocumentClick, true);

  var runtime = document.createElement('script');
  runtime.src = new URL('/webchat/widget-runtime.js', loaderUrl.origin).toString();
  runtime.async = false;
  runtime.defer = loader.defer;
  if (loader.nonce) runtime.nonce = loader.nonce;

  Array.prototype.forEach.call(loader.attributes || [], function (attribute) {
    if (attribute.name.indexOf('data-') === 0) {
      runtime.setAttribute(attribute.name, attribute.value);
    }
  });
  if (!runtime.getAttribute('data-api-base')) {
    runtime.setAttribute('data-api-base', apiBase);
  }

  runtime.addEventListener('error', function () {
    restoreOpenBridge(true);
    document.removeEventListener('click', onDocumentClick, true);
    window.__NEXUSDESK_WEBCHAT_BOOTSTRAP_LOADED__ = false;
  });

  loader.parentNode.insertBefore(runtime, loader.nextSibling);
})();

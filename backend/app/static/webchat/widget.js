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
  var nativeOpen = window.open.bind(window);

  function canonicalWebCallTarget(target) {
    if (typeof target !== 'string' || target.indexOf('/webcall/') !== 0) return null;
    return new URL(target, webCallOrigin + '/').toString();
  }

  window.open = function (target, name, features) {
    var canonicalTarget = canonicalWebCallTarget(target);
    if (!canonicalTarget) return nativeOpen(target, name, features);

    var opened = nativeOpen(canonicalTarget, name, features);
    if (opened) return opened;

    // The runtime creates the VoiceSession before navigation. When the browser
    // blocks the delayed popup, preserve the customer journey by navigating the
    // current tab to the same server-owned WebCall origin. Return a truthy value
    // so the runtime cannot execute its historical host-page relative fallback.
    window.location.assign(canonicalTarget);
    return window;
  };

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
    window.open = nativeOpen;
    window.__NEXUSDESK_WEBCHAT_BOOTSTRAP_LOADED__ = false;
  });

  loader.parentNode.insertBefore(runtime, loader.nextSibling);
})();

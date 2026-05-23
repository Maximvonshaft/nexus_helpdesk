(function () {
  var script = document.currentScript;
  var sessionId = script ? script.getAttribute('data-voice-session-id') : '';
  if (!sessionId || !/^[A-Za-z0-9_-]{1,80}$/.test(sessionId)) {
    return;
  }
  window.location.replace('/webcall/' + encodeURIComponent(sessionId) + window.location.hash);
}());

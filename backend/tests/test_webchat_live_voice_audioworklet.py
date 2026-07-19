from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WIDGET = ROOT / "backend" / "app" / "static" / "webchat" / "widget.js"
WORKLET = ROOT / "backend" / "app" / "static" / "webchat" / "live-voice-capture-worklet.js"
BROWSER_SPEC = ROOT / "webapp" / "e2e" / "live-voice-audioworklet.spec.ts"
RUNTIME_TEST = ROOT / "webapp" / "tests" / "live-voice-worklet-runtime.test.mjs"


def test_widget_uses_versioned_audioworklet_before_socket_and_permission() -> None:
    widget = WIDGET.read_text(encoding="utf-8")
    start = widget[widget.index("  function startLiveVoice() {") : widget.index("  function api(")]
    allocation_guard = (
        "if (live.released || state.liveVoice !== live) "
        "throw new Error('Voice start was cancelled.');"
    )

    assert "/webchat/live-voice-capture-worklet.js?v=1" in widget
    assert "audioWorklet.addModule" in start
    assert "new AudioWorkletNode" in start
    assert "createScriptProcessor(" not in widget
    assert start.index(allocation_guard) < start.index("live.audioContext = new AudioContextConstructor()")
    assert start.index("audioWorklet.addModule") < start.index("openLiveVoiceSocket")
    assert start.index("openLiveVoiceSocket") < start.index("getUserMedia({")
    assert "getUserMedia(" not in widget[: widget.index("  function startLiveVoice() {")]


def test_widget_bounds_packets_and_releases_every_browser_resource() -> None:
    widget = WIDGET.read_text(encoding="utf-8")

    assert "MAX_CAPTURE_PACKET_BYTES = 4096" in widget
    assert "packet.byteLength > MAX_CAPTURE_PACKET_BYTES" in widget
    assert "live.captureNode.port.postMessage({ type: 'stop' })" in widget
    assert "live.captureNode.port.close()" in widget
    assert "live.captureNode.disconnect()" in widget
    assert "live.source.disconnect()" in widget
    assert "live.stream.getTracks().forEach" in widget
    assert "live.audioContext.close()" in widget
    assert "document.addEventListener('visibilitychange'" in widget
    assert "window.addEventListener('pagehide'" in widget
    assert "socket.onclose" in widget
    assert "setTimeout(openLiveVoiceSocket" not in widget


def test_widget_gates_microphone_until_complete_tts_playback() -> None:
    widget = WIDGET.read_text(encoding="utf-8")

    assert "LIVE_VOICE_PLAYBACK_GUARD_SECONDS = 0.35" in widget
    assert "function livePlaybackActive(live)" in widget
    assert "if (livePlaybackActive(live)) return;" in widget
    assert "live.captureResumeAt = live.nextPlayTime + LIVE_VOICE_PLAYBACK_GUARD_SECONDS" in widget
    assert "if (payload.type === 'tts_end') scheduleLiveVoiceReady(live);" in widget
    assert "if (payload.type === 'tts_end') voiceStatus('Ready. You can speak again.');" not in widget


def test_widget_voice_boundary_is_same_origin_and_secret_free() -> None:
    widget = WIDGET.read_text(encoding="utf-8")
    worklet = WORKLET.read_text(encoding="utf-8")
    combined = widget + "\n" + worklet

    assert "value.indexOf('://') !== -1" in widget
    assert "url.host = window.location.host" in widget
    assert "token=" not in combined
    assert "LIVE_VOICE_UPSTREAM_TOKEN" not in combined
    assert "visitor_token" not in widget[widget.index("  function buildLiveWsUrl") : widget.index("  function playPcm16")]
    assert "connection_ticket" in widget
    assert "/live-voice/session" in widget
    assert "47.87.143.41" not in combined
    assert "fetch(" not in worklet
    assert "WebSocket" not in worklet


def test_widget_uses_automatic_language_detection_without_a_language_picker() -> None:
    widget = WIDGET.read_text(encoding="utf-8")

    assert "Language is detected automatically." in widget
    assert "nd-webchat-voice-select" not in widget
    assert "var langCode = 'auto';" in widget
    assert "var voice = 'auto';" in widget


def test_worklet_is_fixed_rate_pcm16_bounded_and_stoppable() -> None:
    worklet = WORKLET.read_text(encoding="utf-8")

    assert "DEFAULT_OUTPUT_SAMPLE_RATE = 16000" in worklet
    assert "DEFAULT_FRAME_SAMPLES = 320" in worklet
    assert "MAX_FRAME_SAMPLES = 2048" in worklet
    assert "Number.isFinite(requestedFrameSamples)" in worklet
    assert "new Int16Array(this.frameSamples)" in worklet
    assert "postMessage({ type: 'pcm16', buffer: packet }, [packet])" in worklet
    assert "event.data.type === 'stop'" in worklet
    assert "if (!this.running) return false" in worklet


def test_browser_and_runtime_smokes_cover_durable_resource_lifecycle() -> None:
    browser = BROWSER_SPEC.read_text(encoding="utf-8")
    runtime = RUNTIME_TEST.read_text(encoding="utf-8")

    assert "synchronous double activation cancels before allocating browser resources" in browser
    assert "explicit start streams bounded PCM and hidden cleanup is deterministic" in browser
    assert "explicit stop releases every live voice resource" in browser
    assert "socket failure fails closed before microphone permission" in browser
    assert "permission denial fails closed and releases the socket" in browser
    assert "module failure and unsupported browsers never request microphone permission" in browser
    assert "socketBoundary.host" in browser
    assert "expect(events).not.toContain('getUserMedia')" in browser
    assert "requested frame size is bounded" in runtime

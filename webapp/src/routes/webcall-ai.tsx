import { useEffect, useRef, useState } from 'react'
import { createRoute } from '@tanstack/react-router'
import { Route as RootRoute } from './root'

type RuntimeConfig = {
  enabled: boolean
  status: string
  voice_provider: string
  livekit_url?: string | null
  max_session_seconds: number
  record_raw_audio: boolean
}

type CreatedSession = {
  conversation_id: string
  visitor_token: string
  session: { public_id: string; status: string; room_name: string; provider: string }
  join: { participant_token: string; participant_identity: string; room_name: string; expires_in_seconds: number }
}

type WebCallEvent = {
  id: number
  event_type: string
  payload: Record<string, any>
  created_at: string | null
}

type CallState = 'loading' | 'disabled' | 'ready' | 'requesting_mic' | 'connecting' | 'connected' | 'ai_joined' | 'listening' | 'thinking' | 'speaking' | 'handoff' | 'ended' | 'error'
type ClientAudioTelemetryStage = 'session_created' | 'get_user_media_success' | 'get_user_media_failure' | 'local_track_state' | 'livekit_publish_success' | 'livekit_publish_failure'

type ClientAudioTelemetry = {
  stage: ClientAudioTelemetryStage
  status: 'success' | 'failure' | 'info'
  selected_audio_input_label?: string | null
  selected_audio_input_device_id_hash?: string | null
  local_track_ready_state?: string | null
  local_track_enabled?: boolean | null
  local_track_muted?: boolean | null
  livekit_track_sid?: string | null
  error_name?: string | null
  error_message?: string | null
}

type TrackDiagnostics = {
  selected_audio_input_label: string | null
  selected_audio_input_device_id_hash: string | null
  local_track_ready_state: string | null
  local_track_enabled: boolean | null
  local_track_muted: boolean | null
}

async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  headers.set('Content-Type', 'application/json')
  const response = await fetch(path, { ...init, headers })
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`
    try {
      const payload = await response.json()
      message = typeof payload.detail === 'string' ? payload.detail : message
    } catch {
      // Use the HTTP status fallback.
    }
    throw new Error(message)
  }
  return response.json() as Promise<T>
}

function resolveMediaStreamTrack(audioTrack: any): MediaStreamTrack | null {
  return audioTrack?.mediaStreamTrack || audioTrack?.track || null
}

async function readTrackDiagnostics(audioTrack: any): Promise<TrackDiagnostics> {
  const mediaTrack = resolveMediaStreamTrack(audioTrack)
  const settings = mediaTrack?.getSettings?.() || {}
  const deviceId = typeof settings.deviceId === 'string' ? settings.deviceId : ''
  return {
    selected_audio_input_label: safeTelemetryText(mediaTrack?.label || null),
    selected_audio_input_device_id_hash: deviceId ? await hashDeviceId(deviceId) : null,
    local_track_ready_state: safeTelemetryText(mediaTrack?.readyState || null),
    local_track_enabled: typeof mediaTrack?.enabled === 'boolean' ? mediaTrack.enabled : null,
    local_track_muted: typeof mediaTrack?.muted === 'boolean' ? mediaTrack.muted : null,
  }
}

async function hashDeviceId(value: string): Promise<string> {
  if (crypto?.subtle) {
    const bytes = new TextEncoder().encode(value)
    const digest = await crypto.subtle.digest('SHA-256', bytes)
    return Array.from(new Uint8Array(digest)).map((item) => item.toString(16).padStart(2, '0')).join('').slice(0, 32)
  }
  let hash = 0
  for (let index = 0; index < value.length; index += 1) hash = Math.imul(31, hash) + value.charCodeAt(index) | 0
  return `fallback-${Math.abs(hash).toString(16)}`
}

function safeTrackSid(publication: any): string | null {
  const value = publication?.trackSid || publication?.sid || null
  return safeTelemetryText(value)
}

function safeTelemetryText(value: string | null | undefined): string | null {
  const trimmed = String(value || '').trim()
  return trimmed ? trimmed.slice(0, 160) : null
}

function WebCallAIProductionPage() {
  const [runtime, setRuntime] = useState<RuntimeConfig | null>(null)
  const [created, setCreated] = useState<CreatedSession | null>(null)
  const [state, setState] = useState<CallState>('loading')
  const [message, setMessage] = useState('Checking voice runtime...')
  const [trackingNumber, setTrackingNumber] = useState('')
  const [events, setEvents] = useState<WebCallEvent[]>([])
  const [muted, setMuted] = useState(false)
  const [micLevel, setMicLevel] = useState(0)
  const [micDiagnostics, setMicDiagnostics] = useState<TrackDiagnostics | null>(null)
  const roomRef = useRef<any | null>(null)
  const localAudioRef = useRef<any>(null)
  const remoteAudioRef = useRef<HTMLDivElement | null>(null)
  const meterCleanupRef = useRef<(() => void) | null>(null)

  async function disconnectRoom() {
    meterCleanupRef.current?.()
    meterCleanupRef.current = null
    setMicLevel(0)
    localAudioRef.current?.stop?.()
    localAudioRef.current = null
    roomRef.current?.disconnect()
    roomRef.current = null
    if (remoteAudioRef.current) remoteAudioRef.current.innerHTML = ''
  }

  function startMicMeter(audioTrack: any) {
    meterCleanupRef.current?.()
    const mediaTrack = resolveMediaStreamTrack(audioTrack)
    if (!mediaTrack || typeof AudioContext === 'undefined') return
    try {
      const audioContext = new AudioContext()
      const stream = new MediaStream([mediaTrack])
      const source = audioContext.createMediaStreamSource(stream)
      const analyser = audioContext.createAnalyser()
      analyser.fftSize = 1024
      source.connect(analyser)
      const data = new Uint8Array(analyser.fftSize)
      let frame = 0
      let lastUpdate = 0
      const tick = (now: number) => {
        analyser.getByteTimeDomainData(data)
        let total = 0
        for (let index = 0; index < data.length; index += 1) {
          const centered = data[index] - 128
          total += centered * centered
        }
        const level = Math.min(1, Math.sqrt(total / data.length) / 64)
        if (now - lastUpdate > 80) {
          lastUpdate = now
          setMicLevel(level)
        }
        frame = window.requestAnimationFrame(tick)
      }
      frame = window.requestAnimationFrame(tick)
      meterCleanupRef.current = () => {
        window.cancelAnimationFrame(frame)
        try {
          source.disconnect()
        } catch {
          // Source may already be disconnected during page unload.
        }
        void audioContext.close()
      }
    } catch {
      setMicLevel(0)
    }
  }

  async function reportClientAudioTelemetry(activeSession: CreatedSession, telemetry: ClientAudioTelemetry) {
    const safeTelemetry = {
      ...telemetry,
      error_message: telemetry.error_message ? telemetry.error_message.slice(0, 180) : null,
    }
    console.info('[webcall-ai-audio]', safeTelemetry)
    try {
      await apiRequest(`/api/webcall-ai/sessions/${activeSession.session.public_id}/client-audio-telemetry`, {
        method: 'POST',
        body: JSON.stringify({ visitor_token: activeSession.visitor_token, ...safeTelemetry }),
      })
    } catch {
      // Client telemetry must not break the call path.
    }
  }

  useEffect(() => {
    let cancelled = false
    apiRequest<RuntimeConfig>('/api/webcall-ai/runtime-config')
      .then((payload) => {
        if (cancelled) return
        setRuntime(payload)
        if (!payload.enabled || payload.status !== 'ready' || !payload.livekit_url) {
          setState('disabled')
          setMessage('WebCall AI voice is not enabled for production traffic.')
          return
        }
        setState('ready')
        setMessage('Ready. Start the call when you are ready to use your microphone.')
      })
      .catch((error: Error) => {
        if (cancelled) return
        setState('error')
        setMessage(error.message)
      })
    return () => {
      cancelled = true
      void disconnectRoom()
    }
  }, [])

  useEffect(() => {
    if (!created || state === 'ended') return
    let cancelled = false
    const activeSession = created
    async function pollEvents() {
      try {
        const payload = await apiRequest<{ events: WebCallEvent[] }>(`/api/webcall-ai/sessions/${activeSession.session.public_id}/events`, {
          headers: { 'X-WebCall-AI-Visitor-Token': activeSession.visitor_token },
        })
        if (!cancelled) {
          setEvents(payload.events)
          const latest = payload.events[payload.events.length - 1]
          if (latest?.event_type === 'webcall_ai.agent.joined') setState('ai_joined')
          if (latest?.event_type === 'webcall_ai.agent.listening') setState('listening')
          if (latest?.event_type === 'webcall_ai.agent.speaking') setState('speaking')
          if (latest?.event_type === 'webcall_ai.transcript.final') setState('thinking')
          if (latest?.event_type === 'webcall_ai.response.spoken') setState('speaking')
          if (latest?.event_type === 'webcall_ai.handoff.requested') setState('handoff')
          if (latest?.event_type === 'webcall_ai.session.ended') setState('ended')
          if (latest?.event_type === 'webcall_ai.agent.failed') setState('error')
        }
      } catch {
        // Event polling is best-effort; the call room stays authoritative for media.
      }
    }
    void pollEvents()
    const timer = window.setInterval(() => void pollEvents(), 2500)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [created, state])

  async function startCall() {
    if (!runtime?.livekit_url || state !== 'ready') return
    let activeSession: CreatedSession | null = null
    try {
      const session = await apiRequest<CreatedSession>('/api/webcall-ai/sessions', {
        method: 'POST',
        headers: { 'Idempotency-Key': `webcall-ai-ui-${Date.now()}` },
        body: JSON.stringify({
          visitor_name: 'WebCall AI Visitor',
          page_url: window.location.href,
          locale: navigator.language || 'en',
        }),
      })
      activeSession = session
      setCreated(session)
      await reportClientAudioTelemetry(session, { stage: 'session_created', status: 'success' })

      setState('requesting_mic')
      setMessage('Requesting microphone permission...')
      const { Room, RoomEvent, Track, createLocalAudioTrack } = await import('livekit-client')
      let audioTrack: any
      try {
        audioTrack = await createLocalAudioTrack({ echoCancellation: true, noiseSuppression: true, autoGainControl: true })
      } catch (error: any) {
        await reportClientAudioTelemetry(session, {
          stage: 'get_user_media_failure',
          status: 'failure',
          error_name: error?.name || 'getUserMediaError',
          error_message: error?.message || 'getUserMedia failed',
        })
        throw error
      }
      localAudioRef.current = audioTrack
      const diagnostics = await readTrackDiagnostics(audioTrack)
      setMicDiagnostics(diagnostics)
      startMicMeter(audioTrack)
      await reportClientAudioTelemetry(session, { stage: 'get_user_media_success', status: 'success', ...diagnostics })
      await reportClientAudioTelemetry(session, { stage: 'local_track_state', status: 'info', ...diagnostics })

      setState('connecting')
      setMessage('Connecting to the AI voice room...')
      const room = new Room({ adaptiveStream: true, dynacast: true })
      roomRef.current = room
      room.on(RoomEvent.TrackSubscribed, (track: any) => {
        if (track.kind === Track.Kind.Audio && remoteAudioRef.current) {
          const element = track.attach()
          element.autoplay = true
          remoteAudioRef.current.appendChild(element)
        }
      })
      room.on(RoomEvent.TrackUnsubscribed, (track: any) => {
        track.detach?.().forEach((element: HTMLElement) => element.remove())
      })
      room.on(RoomEvent.Disconnected, () => {
        setState((current) => (current === 'ended' ? 'ended' : 'error'))
        setMessage('Voice room disconnected.')
      })
      await room.connect(runtime.livekit_url, session.join.participant_token)
      try {
        const publication = await room.localParticipant.publishTrack(audioTrack)
        await reportClientAudioTelemetry(session, {
          stage: 'livekit_publish_success',
          status: 'success',
          livekit_track_sid: safeTrackSid(publication),
          ...(await readTrackDiagnostics(audioTrack)),
        })
      } catch (error: any) {
        await reportClientAudioTelemetry(session, {
          stage: 'livekit_publish_failure',
          status: 'failure',
          error_name: error?.name || 'publishTrackError',
          error_message: error?.message || 'LiveKit publishTrack failed',
          ...(await readTrackDiagnostics(audioTrack)),
        })
        throw error
      }
      setState('connected')
      setMessage('Connected. The AI participant will greet you when the worker joins.')
    } catch (error: any) {
      await disconnectRoom()
      setState('error')
      setMessage(error?.message || (activeSession ? 'Unable to publish microphone audio for WebCall AI.' : 'Unable to start WebCall AI.'))
    }
  }

  async function toggleMute() {
    const track = localAudioRef.current
    if (!track || state !== 'connected') return
    if (muted) {
      await track.unmute?.()
      setMuted(false)
      setMessage('Microphone unmuted.')
    } else {
      await track.mute?.()
      setMuted(true)
      setMessage('Microphone muted.')
    }
  }

  async function requestHandoff() {
    if (!created) return
    await apiRequest(`/api/webcall-ai/sessions/${created.session.public_id}/handoff`, {
      method: 'POST',
      body: JSON.stringify({ visitor_token: created.visitor_token, reason: 'visitor_requested_human' }),
    })
    setState('handoff')
    setMessage('Human handoff requested. The session evidence has been updated.')
  }

  async function saveTrackingFallback() {
    if (!created || !trackingNumber.trim()) return
    const saved = await apiRequest<{ tracking_number_redacted: string }>(`/api/webcall-ai/sessions/${created.session.public_id}/tracking-fallback`, {
      method: 'POST',
      body: JSON.stringify({ visitor_token: created.visitor_token, tracking_number: trackingNumber.trim() }),
    })
    setMessage(`Tracking fallback saved: ${saved.tracking_number_redacted}`)
    setTrackingNumber('')
  }

  async function endCall() {
    if (!created) {
      await disconnectRoom()
      setState('ended')
      return
    }
    try {
      await apiRequest(`/api/webcall-ai/sessions/${created.session.public_id}/end`, {
        method: 'POST',
        body: JSON.stringify({ visitor_token: created.visitor_token }),
      })
    } finally {
      await disconnectRoom()
      setState('ended')
      setMessage('Call ended. The timeline keeps redacted call evidence only.')
    }
  }

  const canStart = state === 'ready'
  const connectedState = ['connected', 'ai_joined', 'listening', 'thinking', 'speaking'].includes(state)

  return (
    <main className="webcall-ai-page">
      <section className="webcall-ai-hero">
        <div className="webcall-ai-main">
          <p className="eyebrow">NexusDesk</p>
          <h1>WebCall AI</h1>
          <p className="lead">Speak with the AI support agent for shipment questions. Raw audio is not stored by default.</p>
          <div className={`call-status ${state}`} role="status">{message}</div>
          <div className="mic-meter" aria-label="Microphone input level" data-testid="webcall-ai-mic-meter">
            <div className="mic-meter-head">
              <span>Mic input</span>
              <strong>{Math.round(micLevel * 100)}%</strong>
            </div>
            <div className="mic-meter-track">
              <div className="mic-level-bar" data-testid="webcall-ai-mic-level-bar" style={{ width: `${Math.max(2, Math.round(micLevel * 100))}%` }} />
            </div>
            <small>{micDiagnostics?.local_track_ready_state || 'not started'} · {micDiagnostics?.local_track_muted ? 'muted' : 'not muted'}</small>
          </div>
          <div className="call-actions">
            <button type="button" className="primary-action" disabled={!canStart} onClick={() => void startCall()}>{state === 'requesting_mic' || state === 'connecting' ? 'Connecting' : 'Start call'}</button>
            <button type="button" disabled={!connectedState} onClick={() => void toggleMute()}>{muted ? 'Unmute' : 'Mute'}</button>
            <button type="button" disabled={!created || state === 'ended'} onClick={() => void requestHandoff()}>Human handoff</button>
            <button type="button" disabled={state === 'ended'} onClick={() => void endCall()}>End</button>
          </div>
        </div>
        <aside className="webcall-ai-side">
          <div><span>Runtime</span><strong>{runtime?.status || state}</strong></div>
          <div><span>Provider</span><strong>{runtime?.voice_provider || '-'}</strong></div>
          <div><span>Room</span><strong>{created?.session.room_name || '-'}</strong></div>
          <div><span>Session</span><strong>{created?.session.public_id || '-'}</strong></div>
        </aside>
      </section>

      <section className="webcall-ai-band">
        <div>
          <h2>Tracking fallback</h2>
          <p>If speech recognition misses the tracking number, type it here during the call so the operator timeline has the correct reference.</p>
        </div>
        <div className="tracking-input-row">
          <input value={trackingNumber} onChange={(event) => setTrackingNumber(event.target.value)} placeholder="Tracking number" />
          <button type="button" disabled={!trackingNumber.trim() || !created} onClick={() => void saveTrackingFallback()}>Save</button>
        </div>
      </section>
      <section className="webcall-ai-band">
        <div>
          <h2>Call timeline</h2>
          <p>Redacted transcript, AI replies, handoff, and tool events appear here when the worker writes evidence.</p>
        </div>
        <div className="webcall-ai-events">
          {events.length === 0 ? (
            <p>No persisted AI events yet.</p>
          ) : (
            events.slice(-6).map((event) => (
              <article key={event.id}>
                <strong>{event.event_type.replace('webcall_ai.', '').replaceAll('.', ' ')}</strong>
                <span>{event.payload.text_redacted || event.payload.reason || event.payload.tool || event.payload.tts_provider || event.payload.status || 'Recorded'}</span>
              </article>
            ))
          )}
        </div>
      </section>
      <div ref={remoteAudioRef} aria-hidden="true" />
    </main>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/webcall-ai',
  component: WebCallAIProductionPage,
})

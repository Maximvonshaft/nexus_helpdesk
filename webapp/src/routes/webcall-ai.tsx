import { useEffect, useRef, useState } from 'react'
import { createRoute } from '@tanstack/react-router'
import { Room, RoomEvent, Track, createLocalAudioTrack } from 'livekit-client'
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

type CallState = 'loading' | 'disabled' | 'ready' | 'requesting_mic' | 'connecting' | 'connected' | 'handoff' | 'ended' | 'error'

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

function WebCallAIProductionPage() {
  const [runtime, setRuntime] = useState<RuntimeConfig | null>(null)
  const [created, setCreated] = useState<CreatedSession | null>(null)
  const [state, setState] = useState<CallState>('loading')
  const [message, setMessage] = useState('Checking voice runtime...')
  const [trackingNumber, setTrackingNumber] = useState('')
  const [muted, setMuted] = useState(false)
  const roomRef = useRef<Room | null>(null)
  const localAudioRef = useRef<any>(null)
  const remoteAudioRef = useRef<HTMLDivElement | null>(null)

  async function disconnectRoom() {
    localAudioRef.current?.stop?.()
    localAudioRef.current = null
    roomRef.current?.disconnect()
    roomRef.current = null
    if (remoteAudioRef.current) remoteAudioRef.current.innerHTML = ''
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

  async function startCall() {
    if (!runtime?.livekit_url || state !== 'ready') return
    try {
      setState('requesting_mic')
      setMessage('Requesting microphone permission...')
      const audioTrack = await createLocalAudioTrack({ echoCancellation: true, noiseSuppression: true, autoGainControl: true })
      localAudioRef.current = audioTrack

      const session = await apiRequest<CreatedSession>('/api/webcall-ai/sessions', {
        method: 'POST',
        headers: { 'Idempotency-Key': `webcall-ai-ui-${Date.now()}` },
        body: JSON.stringify({
          visitor_name: 'WebCall AI Visitor',
          page_url: window.location.href,
          locale: navigator.language || 'en',
        }),
      })
      setCreated(session)

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
      await room.localParticipant.publishTrack(audioTrack)
      setState('connected')
      setMessage('Connected. The AI participant will greet you when the worker joins.')
    } catch (error: any) {
      await disconnectRoom()
      setState('error')
      setMessage(error?.message || 'Unable to start WebCall AI.')
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
  const connectedState = state === 'connected'

  return (
    <main className="webcall-ai-page">
      <section className="webcall-ai-hero">
        <div className="webcall-ai-main">
          <p className="eyebrow">NexusDesk</p>
          <h1>WebCall AI</h1>
          <p className="lead">Speak with the AI support agent for shipment questions. Raw audio is not stored by default.</p>
          <div className={`call-status ${state}`} role="status">{message}</div>
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
          <button type="button" disabled={!trackingNumber.trim() || !created} onClick={() => setMessage(`Tracking fallback captured: ${trackingNumber.trim().slice(0, 4)}...`)}>Save</button>
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

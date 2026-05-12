import { useEffect, useMemo, useRef, useState } from 'react'
import { createRoute } from '@tanstack/react-router'
import { Room, RoomEvent, Track, createLocalAudioTrack } from 'livekit-client'
import { Route as RootRoute } from './root'

type WebCallContext = {
  apiBase: string
  conversationId: string
  visitorToken: string
  livekitUrl: string
  participantToken: string
  roomName: string
  participantIdentity: string
  provider: string
}

type CallState = 'ready' | 'requesting_mic' | 'connecting' | 'connected' | 'ended' | 'error'

function safeText(value: string | null | undefined) {
  return (value || '').slice(0, 240)
}

function readContextFromHash(): WebCallContext | null {
  const rawHash = window.location.hash.replace(/^#/, '')
  if (!rawHash) return null
  const params = new URLSearchParams(rawHash)
  const context: WebCallContext = {
    apiBase: params.get('api_base') || window.location.origin,
    conversationId: params.get('conversation_id') || '',
    visitorToken: params.get('visitor_token') || '',
    livekitUrl: params.get('livekit_url') || '',
    participantToken: params.get('participant_token') || '',
    roomName: params.get('room_name') || '',
    participantIdentity: params.get('participant_identity') || '',
    provider: params.get('provider') || '',
  }
  window.history.replaceState(null, document.title, window.location.pathname + window.location.search)
  if (!context.conversationId || !context.visitorToken || !context.participantToken || !context.livekitUrl) return null
  return context
}

function WebCallVisitorRoomPage() {
  const { voice_session_id: voiceSessionId } = Route.useParams()
  const [context, setContext] = useState<WebCallContext | null>(null)
  const [callState, setCallState] = useState<CallState>('ready')
  const [message, setMessage] = useState('Ready to join WebCall. Microphone permission will be requested only after you click Join.')
  const [muted, setMuted] = useState(false)
  const [ended, setEnded] = useState(false)
  const roomRef = useRef<Room | null>(null)
  const localAudioRef = useRef<any>(null)
  const remoteAudioRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const parsed = readContextFromHash()
    setContext(parsed)
    if (!parsed) {
      setCallState('error')
      setMessage('This WebCall session is missing secure join context. Please start the call again from the WebCall button.')
    }
    return () => {
      void disconnectRoom()
    }
  }, [])

  const canJoin = useMemo(() => Boolean(context && callState === 'ready' && !ended), [context, callState, ended])
  const connected = callState === 'connected'

  async function disconnectRoom() {
    try {
      if (localAudioRef.current) {
        localAudioRef.current.stop?.()
        localAudioRef.current = null
      }
      if (roomRef.current) {
        roomRef.current.disconnect()
        roomRef.current = null
      }
      if (remoteAudioRef.current) remoteAudioRef.current.innerHTML = ''
    } catch {
      // best effort cleanup only
    }
  }

  async function joinCall() {
    if (!context || !canJoin) return
    try {
      setCallState('requesting_mic')
      setMessage('Requesting microphone permission...')
      const audioTrack = await createLocalAudioTrack({
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      })
      localAudioRef.current = audioTrack

      setCallState('connecting')
      setMessage('Connecting to WebCall room...')
      const room = new Room({ adaptiveStream: true, dynacast: true })
      roomRef.current = room
      room.on(RoomEvent.Disconnected, () => {
        if (!ended) {
          setCallState('ended')
          setMessage('WebCall disconnected.')
        }
      })
      room.on(RoomEvent.Reconnecting, () => {
        setMessage('Network interrupted. Reconnecting...')
      })
      room.on(RoomEvent.Reconnected, () => {
        setMessage('WebCall reconnected.')
      })
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
      await room.connect(context.livekitUrl, context.participantToken)
      await room.localParticipant.publishTrack(audioTrack)
      setMuted(false)
      setCallState('connected')
      setMessage('Connected. Waiting for support agent if not joined yet.')
    } catch (error: any) {
      await disconnectRoom()
      setCallState('error')
      if (error?.name === 'NotAllowedError' || error?.name === 'PermissionDeniedError') {
        setMessage('Microphone permission was denied. Please allow microphone access and try again.')
      } else {
        setMessage(error?.message || 'Unable to connect WebCall. Please check your network and try again.')
      }
    }
  }

  async function toggleMute() {
    const audioTrack = localAudioRef.current
    if (!audioTrack || !connected) return
    if (muted) {
      await audioTrack.unmute?.()
      setMuted(false)
      setMessage('Microphone unmuted.')
    } else {
      await audioTrack.mute?.()
      setMuted(true)
      setMessage('Microphone muted.')
    }
  }

  async function endCall() {
    if (!context || ended) return
    setEnded(true)
    try {
      await fetch(`${context.apiBase.replace(/\/$/, '')}/api/webchat/conversations/${encodeURIComponent(context.conversationId)}/voice/${encodeURIComponent(voiceSessionId)}/end`, {
        method: 'POST',
        headers: { 'X-Webchat-Visitor-Token': context.visitorToken },
      })
    } catch {
      // local disconnect still happens; backend end can be retried by page reload or agent side
    }
    await disconnectRoom()
    setCallState('ended')
    setMessage('WebCall ended. The ticket timeline will receive the call evidence from NexusDesk.')
  }

  return (
    <main style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', background: '#f8fafc', color: '#101828', padding: 24 }}>
      <section style={{ width: 'min(680px, 100%)', background: '#fff', border: '1px solid #e5e7eb', borderRadius: 24, padding: 28, boxShadow: '0 20px 60px rgba(15,23,42,.08)' }}>
        <p style={{ margin: 0, color: '#f97316', fontWeight: 800, letterSpacing: '.08em', textTransform: 'uppercase' }}>NexusDesk WebCall</p>
        <h1 style={{ margin: '8px 0 6px', fontSize: 30 }}>Browser voice support</h1>
        <p style={{ marginTop: 0, color: '#475467' }}>Session: {safeText(voiceSessionId)}</p>
        <div role="status" style={{ border: '1px solid #fed7aa', background: '#fff7ed', color: '#9a3412', padding: 14, borderRadius: 14, margin: '18px 0' }}>{message}</div>
        <div style={{ display: 'grid', gap: 8, marginBottom: 18, color: '#475467' }}>
          <div><strong>Microphone:</strong> requested only after clicking Join.</div>
          <div><strong>Recording:</strong> no recording in this phase.</div>
          <div><strong>Provider:</strong> {context?.provider || 'not ready'}</div>
          <div><strong>Room:</strong> {context?.roomName || '-'}</div>
        </div>
        <div ref={remoteAudioRef} aria-hidden="true" />
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
          <button type="button" disabled={!canJoin} onClick={() => void joinCall()} style={{ border: 0, borderRadius: 999, padding: '12px 18px', background: canJoin ? '#f97316' : '#cbd5e1', color: '#fff', fontWeight: 800, cursor: canJoin ? 'pointer' : 'not-allowed' }}>Join WebCall</button>
          <button type="button" disabled={!connected} onClick={() => void toggleMute()} style={{ border: '1px solid #cbd5e1', borderRadius: 999, padding: '12px 18px', background: '#fff', color: '#101828', fontWeight: 700, cursor: connected ? 'pointer' : 'not-allowed' }}>{muted ? 'Unmute' : 'Mute'}</button>
          <button type="button" disabled={!context || ended} onClick={() => void endCall()} style={{ border: '1px solid #fecaca', borderRadius: 999, padding: '12px 18px', background: '#fff1f2', color: '#be123c', fontWeight: 800, cursor: context && !ended ? 'pointer' : 'not-allowed' }}>End call</button>
        </div>
      </section>
    </main>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/webcall/$voice_session_id',
  component: WebCallVisitorRoomPage,
})

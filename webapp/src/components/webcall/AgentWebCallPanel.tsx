import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Room, RoomEvent, Track, createLocalAudioTrack } from 'livekit-client'
import { webchatVoiceApi } from '@/lib/webchatVoiceApi'
import type { WebchatVoiceSession } from '@/lib/webchatVoiceTypes'
import { formatDateTime, sanitizeDisplayText } from '@/lib/format'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { EmptyState } from '@/components/ui/EmptyState'

const ACTIVE_STATUSES = new Set(['created', 'ringing', 'accepted', 'active'])
const TERMINAL_STATUSES = new Set(['ended', 'failed', 'cancelled', 'missed'])

type AgentCallState = 'idle' | 'accepting' | 'requesting_mic' | 'connecting' | 'connected' | 'ended' | 'error'

type AgentWebCallPanelProps = {
  ticketId: number | null
  onActivity?: () => void
}

type LocalAudioTrack = Awaited<ReturnType<typeof createLocalAudioTrack>>

function activeVoiceSession(items?: WebchatVoiceSession[]) {
  return (items ?? []).find((item) => ACTIVE_STATUSES.has(String(item.status))) ?? items?.[0] ?? null
}

function valueOrDash(value?: string | number | null) {
  if (value === null || value === undefined || value === '') return '-'
  return sanitizeDisplayText(String(value))
}

function roomNameFor(session: WebchatVoiceSession | null) {
  return session?.provider_room_name || session?.room_name || '-'
}

export function AgentWebCallPanel({ ticketId, onActivity }: AgentWebCallPanelProps) {
  const client = useQueryClient()
  const [callState, setCallState] = useState<AgentCallState>('idle')
  const [message, setMessage] = useState('Waiting for an incoming WebCall on this ticket.')
  const [muted, setMuted] = useState(false)
  const [joinedVoiceSessionId, setJoinedVoiceSessionId] = useState<string | null>(null)
  const roomRef = useRef<Room | null>(null)
  const localAudioRef = useRef<LocalAudioTrack | null>(null)
  const remoteAudioRef = useRef<HTMLDivElement | null>(null)

  const runtimeConfig = useQuery({
    queryKey: ['webchatVoiceRuntimeConfig'],
    queryFn: ({ signal }) => webchatVoiceApi.runtimeConfig({ signal }),
    refetchInterval: 30000,
    retry: false,
  })

  const sessions = useQuery({
    queryKey: ['webchatVoiceSessions', ticketId],
    queryFn: ({ signal }) => webchatVoiceApi.listSessions(ticketId as number, { signal }),
    enabled: !!ticketId,
    refetchInterval: callState === 'connected' ? 8000 : 4000,
    retry: false,
  })

  const currentSession = useMemo(() => activeVoiceSession(sessions.data?.items), [sessions.data?.items])
  const terminal = currentSession ? TERMINAL_STATUSES.has(String(currentSession.status)) : false
  const hasLiveCall = Boolean(currentSession && !terminal)
  const connected = callState === 'connected'
  const canAccept = Boolean(ticketId && currentSession && !terminal && !connected && callState !== 'accepting' && callState !== 'requesting_mic' && callState !== 'connecting')

  async function cleanupRoom() {
    try {
      if (localAudioRef.current) {
        localAudioRef.current.stop()
        localAudioRef.current = null
      }
      if (roomRef.current) {
        roomRef.current.disconnect()
        roomRef.current = null
      }
      if (remoteAudioRef.current) remoteAudioRef.current.innerHTML = ''
    } catch {
      // best-effort browser media cleanup only
    }
  }

  useEffect(() => {
    return () => {
      void cleanupRoom()
    }
  }, [])

  useEffect(() => {
    if (!currentSession) {
      if (!connected) setMessage('Waiting for an incoming WebCall on this ticket.')
      return
    }
    if (terminal && !connected) setMessage(`WebCall is ${currentSession.status}.`)
  }, [connected, currentSession, terminal])

  async function invalidateVoiceViews() {
    if (ticketId) {
      await client.invalidateQueries({ queryKey: ['webchatVoiceSessions', ticketId] })
      await client.invalidateQueries({ queryKey: ['webchatThread', ticketId] })
    }
    await client.invalidateQueries({ queryKey: ['webchatConversations'] })
    onActivity?.()
  }

  const acceptMutation = useMutation({
    mutationFn: async (session: WebchatVoiceSession) => {
      if (!ticketId) throw new Error('No ticket selected')
      setCallState('accepting')
      setMessage('Accepting WebCall...')
      const accepted = await webchatVoiceApi.acceptSession(ticketId, session.voice_session_id)
      if (accepted.provider !== 'livekit') {
        setMessage(`WebCall accepted with provider ${accepted.provider}. Real browser audio requires provider=livekit.`)
        setCallState('idle')
        return accepted
      }
      const livekitUrl = runtimeConfig.data?.livekit_url
      if (!livekitUrl) throw new Error('LiveKit URL is missing from runtime config')
      if (!accepted.participant_token) throw new Error('Agent participant token missing from accept response')

      setCallState('requesting_mic')
      setMessage('Requesting microphone permission...')
      const audioTrack = await createLocalAudioTrack({
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      })
      localAudioRef.current = audioTrack

      setCallState('connecting')
      setMessage('Joining WebCall room...')
      const room = new Room({ adaptiveStream: true, dynacast: true })
      roomRef.current = room
      room.on(RoomEvent.Disconnected, () => {
        setCallState((state) => (state === 'ended' ? state : 'idle'))
        setMessage('WebCall disconnected.')
      })
      room.on(RoomEvent.Reconnecting, () => setMessage('Network interrupted. Reconnecting WebCall...'))
      room.on(RoomEvent.Reconnected, () => setMessage('WebCall reconnected.'))
      room.on(RoomEvent.TrackSubscribed, (track) => {
        if (track.kind === Track.Kind.Audio && remoteAudioRef.current) {
          const element = track.attach()
          element.autoplay = true
          remoteAudioRef.current.appendChild(element)
        }
      })
      room.on(RoomEvent.TrackUnsubscribed, (track) => {
        track.detach().forEach((element) => element.remove())
      })
      await room.connect(livekitUrl, accepted.participant_token)
      await room.localParticipant.publishTrack(audioTrack)
      setMuted(false)
      setJoinedVoiceSessionId(accepted.voice_session_id)
      setCallState('connected')
      setMessage('Connected to WebCall. Speak with the visitor now.')
      return accepted
    },
    onSuccess: async () => {
      await invalidateVoiceViews()
    },
    onError: async (err: Error) => {
      await cleanupRoom()
      setCallState('error')
      if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError') {
        setMessage('Microphone permission was denied. Allow microphone access and accept the WebCall again.')
      } else {
        setMessage(err.message || 'Unable to accept WebCall')
      }
    },
  })

  const endMutation = useMutation({
    mutationFn: async () => {
      if (!ticketId || !currentSession) return null
      const targetVoiceSessionId = joinedVoiceSessionId || currentSession.voice_session_id
      setMessage('Ending WebCall...')
      const response = await webchatVoiceApi.endSession(ticketId, targetVoiceSessionId)
      await cleanupRoom()
      setCallState('ended')
      setJoinedVoiceSessionId(null)
      setMuted(false)
      setMessage('WebCall ended. Ticket timeline evidence will refresh shortly.')
      return response
    },
    onSuccess: async () => {
      await invalidateVoiceViews()
    },
    onError: (err: Error) => setMessage(err.message || 'Unable to end WebCall'),
  })

  async function toggleMute() {
    const audioTrack = localAudioRef.current
    if (!audioTrack || !connected) return
    if (muted) {
      await audioTrack.unmute()
      setMuted(false)
      setMessage('Microphone unmuted.')
    } else {
      await audioTrack.mute()
      setMuted(true)
      setMessage('Microphone muted.')
    }
  }

  return (
    <Card>
      <CardHeader title="Agent WebCall" subtitle="Accept browser voice calls from the selected WebChat ticket. Microphone access is requested only after Accept WebCall." />
      <CardBody>
        {!ticketId ? <EmptyState text="Select a WebChat ticket to monitor WebCall sessions." /> : null}
        {ticketId && sessions.isLoading ? <div className="section-subtitle">Loading WebCall sessions...</div> : null}
        {ticketId && !sessions.isLoading && !currentSession ? <EmptyState text="No WebCall session exists for this ticket yet." /> : null}
        {currentSession ? (
          <div className="stack compact" data-testid="agent-webcall-panel">
            <div className="badges">
              <Badge tone={hasLiveCall ? 'warning' : 'default'}>{hasLiveCall ? 'Incoming WebCall' : 'WebCall history'}</Badge>
              <Badge tone={connected ? 'success' : callState === 'error' ? 'danger' : 'default'}>{callState}</Badge>
              <Badge>{sanitizeDisplayText(currentSession.provider)}</Badge>
              <Badge>{sanitizeDisplayText(currentSession.status)}</Badge>
            </div>
            <div className="kv-grid">
              <div className="kv"><label>Session</label><div>{valueOrDash(currentSession.voice_session_id)}</div></div>
              <div className="kv"><label>Room</label><div>{valueOrDash(roomNameFor(currentSession))}</div></div>
              <div className="kv"><label>Accepted by</label><div>{valueOrDash(currentSession.accepted_by_user_id)}</div></div>
              <div className="kv"><label>Started</label><div>{valueOrDash(formatDateTime(currentSession.started_at || undefined))}</div></div>
              <div className="kv"><label>Ringing</label><div>{valueOrDash(formatDateTime(currentSession.ringing_at || undefined))}</div></div>
              <div className="kv"><label>Accepted</label><div>{valueOrDash(formatDateTime(currentSession.accepted_at || undefined))}</div></div>
              <div className="kv"><label>Ended</label><div>{valueOrDash(formatDateTime(currentSession.ended_at || undefined))}</div></div>
              <div className="kv"><label>LiveKit</label><div>{valueOrDash(runtimeConfig.data?.livekit_url || (runtimeConfig.isError ? 'runtime config unavailable' : 'loading'))}</div></div>
            </div>
            <div role="status" className="section-subtitle">{sanitizeDisplayText(message)}</div>
            <div ref={remoteAudioRef} aria-hidden="true" />
            <div className="inline-actions">
              <Button variant="primary" disabled={!canAccept} onClick={() => currentSession && acceptMutation.mutate(currentSession)}>
                {acceptMutation.isPending ? 'Accepting...' : 'Accept WebCall'}
              </Button>
              <Button variant="secondary" disabled={!connected} onClick={() => void toggleMute()}>{muted ? 'Unmute' : 'Mute'}</Button>
              <Button variant="secondary" disabled={!currentSession || terminal || endMutation.isPending} onClick={() => endMutation.mutate()}>
                {endMutation.isPending ? 'Ending...' : 'End WebCall'}
              </Button>
            </div>
          </div>
        ) : null}
      </CardBody>
    </Card>
  )
}

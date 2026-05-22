import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Room, RoomEvent, Track, createLocalAudioTrack } from 'livekit-client'
import { ApiError } from '@/lib/api'
import { webchatVoiceApi } from '@/lib/webchatVoiceApi'
import type { WebchatVoiceSession } from '@/lib/webchatVoiceTypes'
import { formatDateTime, sanitizeDisplayText } from '@/lib/format'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { EmptyState } from '@/components/ui/EmptyState'

const ACTIVE_STATUSES = new Set(['created', 'ringing', 'accepted', 'active'])
const REJECT_READY_STATUSES = new Set(['created', 'ringing'])
const TERMINAL_STATUSES = new Set(['ended', 'failed', 'cancelled', 'missed'])
const QUEUE_TABS = [
  { key: 'incoming', label: 'Incoming' },
  { key: 'my_active', label: 'My Active' },
  { key: 'all_active', label: 'All Active' },
  { key: 'missed', label: 'Missed' },
  { key: 'closed_recent', label: 'Closed Recent' },
] as const
const STATUS_LABELS: Record<string, string> = {
  created: 'Created',
  ringing: 'Ringing — waiting for agent',
  accepted: 'Accepted — joining room',
  active: 'Active — in call',
  ended: 'Ended',
  missed: 'Missed',
  failed: 'Failed',
  cancelled: 'Cancelled',
}
const STATUS_TONES: Record<string, 'default' | 'warning' | 'success' | 'danger'> = {
  created: 'warning',
  ringing: 'warning',
  accepted: 'success',
  active: 'success',
  ended: 'default',
  missed: 'danger',
  failed: 'danger',
  cancelled: 'danger',
}
const ACCEPT_ERROR_MESSAGES: Array<[string, string]> = [
  ['already accepted by another agent', '该通话已被其他客服接起。请刷新来电队列。'],
  ['expired', '该来电已超时，请等待客户重新发起。'],
  ['missed', '该来电已超时，请等待客户重新发起。'],
  ['ended', '该通话已结束。'],
  ['cancelled', '该通话已取消。'],
  ['failed', '该通话已失败，请重新发起。'],
]

type AgentCallState = 'idle' | 'accepting' | 'requesting_mic' | 'connecting' | 'connected' | 'ended' | 'error'

type AgentWebCallPanelProps = {
  ticketId: number | null
  conversationId?: string | null
  ticketNo?: string | null
  visitorLabel?: string | null
  onActivity?: () => void
  onSelectTicket?: (ticketId: number) => void
}

type LocalAudioTrack = Awaited<ReturnType<typeof createLocalAudioTrack>>
type SessionWithOptionalOpsFields = WebchatVoiceSession & {
  recording_status?: string | null
  transcript_status?: string | null
  summary_status?: string | null
}

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

function readableStatus(status?: string | null) {
  const raw = String(status || '').toLowerCase()
  if (STATUS_LABELS[raw]) return STATUS_LABELS[raw]
  if (!raw) return 'Unknown / 未知状态'
  return `Unknown / 未知状态 (${valueOrDash(status)})`
}

function statusTone(status?: string | null) {
  const raw = String(status || '').toLowerCase()
  return STATUS_TONES[raw] || 'default'
}

function readableAcceptError(detail: string) {
  const lowered = detail.toLowerCase()
  for (const [needle, message] of ACCEPT_ERROR_MESSAGES) {
    if (lowered.includes(needle)) return message
  }
  return '当前来电状态已变化，无法接起。请刷新来电队列后重试。'
}

function safeErrorMessage(err: unknown) {
  if (err instanceof ApiError) {
    if (err.status === 409) return readableAcceptError(err.message)
    return 'WebCall request failed. Please refresh and try again.'
  }
  if (err instanceof Error) {
    if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError') return 'Microphone permission was denied. Allow microphone access and accept the WebCall again.'
    return 'WebCall request failed. Please refresh and try again.'
  }
  return 'WebCall request failed. Please refresh and try again.'
}

function DetailField({ label, value }: { label: string; value?: string | number | null }) {
  return <div className="kv"><label>{label}</label><div>{valueOrDash(value)}</div></div>
}

export function AgentWebCallPanel({ ticketId, conversationId, ticketNo, visitorLabel, onActivity, onSelectTicket }: AgentWebCallPanelProps) {
  const client = useQueryClient()
  const [callState, setCallState] = useState<AgentCallState>('idle')
  const [message, setMessage] = useState('Waiting for an incoming WebCall on this ticket.')
  const [muted, setMuted] = useState(false)
  const [joinedVoiceSessionId, setJoinedVoiceSessionId] = useState<string | null>(null)
  const [queueTab, setQueueTab] = useState<(typeof QUEUE_TABS)[number]['key']>('incoming')
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

  const operationalQueue = useQuery({
    queryKey: ['webchatVoiceOperationalQueue', queueTab],
    queryFn: ({ signal }) => webchatVoiceApi.incomingSessions({ status: queueTab, limit: 50 }, { signal }),
    refetchInterval: queueTab === 'incoming' ? 4000 : 8000,
    retry: false,
  })

  const allSessions = sessions.data?.items ?? []
  const currentSession = useMemo(() => activeVoiceSession(sessions.data?.items), [sessions.data?.items]) as SessionWithOptionalOpsFields | null
  const terminal = currentSession ? TERMINAL_STATUSES.has(String(currentSession.status)) : false
  const hasLiveCall = Boolean(currentSession && !terminal)
  const connected = callState === 'connected'
  const busyAccepting = callState === 'accepting' || callState === 'requesting_mic' || callState === 'connecting'
  const canAccept = Boolean(ticketId && currentSession && !terminal && !connected && !busyAccepting)
  const canReject = Boolean(ticketId && currentSession && REJECT_READY_STATUSES.has(String(currentSession.status)) && !connected && !busyAccepting)

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
    if (terminal && !connected) setMessage(`WebCall status: ${readableStatus(currentSession.status)}.`)
  }, [connected, currentSession, terminal])

  async function invalidateVoiceViews() {
    if (ticketId) {
      await client.invalidateQueries({ queryKey: ['webchatVoiceSessions', ticketId] })
      await client.invalidateQueries({ queryKey: ['webchatThread', ticketId] })
    }
    await client.invalidateQueries({ queryKey: ['webchatConversations'] })
    await client.invalidateQueries({ queryKey: ['webchatVoiceOperationalQueue'] })
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
      if (!accepted.participant_token) throw new Error('Agent participant credential missing from accept response')

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
    onError: async (err: unknown) => {
      await cleanupRoom()
      setCallState('error')
      setMessage(safeErrorMessage(err))
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
    onError: (err: unknown) => setMessage(safeErrorMessage(err)),
  })

  const rejectMutation = useMutation({
    mutationFn: async () => {
      if (!ticketId || !currentSession) return null
      setMessage('Rejecting WebCall...')
      const response = await webchatVoiceApi.rejectSession(ticketId, currentSession.voice_session_id, 'agent_rejected')
      await cleanupRoom()
      setCallState('ended')
      setJoinedVoiceSessionId(null)
      setMuted(false)
      setMessage('WebCall rejected. The visitor can continue in WebChat text support.')
      return response
    },
    onSuccess: async () => {
      await invalidateVoiceViews()
      await client.invalidateQueries({ queryKey: ['webchatVoiceIncomingSessions'] })
    },
    onError: (err: unknown) => setMessage(safeErrorMessage(err)),
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
      <CardHeader title="Agent WebCall" subtitle="Incoming-call queue for the selected WebChat ticket. Microphone access is requested only after Accept WebCall." />
      <CardBody>
        {!ticketId ? <EmptyState text="Select a WebChat ticket to monitor WebCall sessions." /> : null}
        {ticketId && sessions.isLoading ? <div className="section-subtitle" data-testid="webcall-loading-state">Loading WebCall sessions...</div> : null}
        {ticketId && sessions.isError ? (
          <div className="message" data-testid="webcall-error-state" data-role="agent">
            Unable to load WebCall sessions: {safeErrorMessage(sessions.error)}
            <div className="inline-actions" style={{ marginTop: 8 }}><Button variant="secondary" onClick={() => void sessions.refetch()}>Retry</Button></div>
          </div>
        ) : null}
        {ticketId && !sessions.isLoading && !sessions.isError && !currentSession ? <EmptyState text="No incoming WebCall sessions for this ticket." /> : null}
        {!currentSession ? (
          <div className="stack compact" data-testid="webcall-operational-queue">
            <strong>WebCall Operational Queue</strong>
            <div className="inline-actions" role="tablist" aria-label="WebCall Operational Queue tabs">
              {QUEUE_TABS.map((tab) => (
                <Button key={tab.key} variant={queueTab === tab.key ? 'primary' : 'secondary'} onClick={() => setQueueTab(tab.key)}>
                  {tab.label}
                </Button>
              ))}
            </div>
            {operationalQueue.isLoading ? <div className="section-subtitle">Loading WebCall queue...</div> : null}
            {operationalQueue.isError ? <div className="section-subtitle">Unable to load WebCall queue.</div> : null}
            {!operationalQueue.isLoading && !operationalQueue.isError && !(operationalQueue.data?.items ?? []).length ? <EmptyState text="No WebCall sessions in this queue." /> : null}
            {(operationalQueue.data?.items ?? []).map((item) => (
              <button
                key={`${queueTab}-${item.voice_session_id}`}
                className={`queue-card ${ticketId === item.ticket_id ? 'selected' : ''}`}
                onClick={() => onSelectTicket?.(item.ticket_id)}
              >
                <div className="badges">
                  <Badge tone={statusTone(item.status)}>{readableStatus(item.status)}</Badge>
                  <Badge>{sanitizeDisplayText(item.provider)}</Badge>
                  {item.ticket_no ? <Badge>{sanitizeDisplayText(item.ticket_no)}</Badge> : null}
                </div>
                <div className="queue-card-title">{valueOrDash(item.visitor_label)} · {valueOrDash(item.voice_session_id)}</div>
                <div className="queue-card-meta">Ticket {valueOrDash(item.ticket_id)} · Ringing {valueOrDash(formatDateTime(item.ringing_at || undefined))} · Ended {valueOrDash(formatDateTime(item.ended_at || undefined))}</div>
              </button>
            ))}
          </div>
        ) : null}
        {currentSession ? (
          <div className="stack compact" data-testid="agent-webcall-panel">
            <div className="badges">
              <Badge tone={hasLiveCall ? 'warning' : 'default'}>{hasLiveCall ? 'Incoming WebCall' : 'WebCall history'}</Badge>
              <Badge tone={statusTone(currentSession.status)}>{readableStatus(currentSession.status)}</Badge>
              <Badge tone={connected ? 'success' : callState === 'error' ? 'danger' : 'default'}>{callState}</Badge>
              <Badge>{sanitizeDisplayText(currentSession.provider)}</Badge>
            </div>

            <div className="kv-grid" data-testid="webcall-current-card">
              <DetailField label="Ticket ID" value={ticketId} />
              <DetailField label="Ticket" value={ticketNo} />
              <DetailField label="Conversation ID" value={conversationId} />
              <DetailField label="Visitor" value={visitorLabel} />
              <DetailField label="Voice session ID" value={currentSession.voice_session_id} />
              <DetailField label="Room" value={roomNameFor(currentSession)} />
              <DetailField label="Provider" value={currentSession.provider} />
              <DetailField label="Status" value={readableStatus(currentSession.status)} />
              <DetailField label="Accepted by user ID" value={currentSession.accepted_by_user_id} />
              <DetailField label="Ended by user ID" value={currentSession.ended_by_user_id} />
              <DetailField label="Started" value={formatDateTime(currentSession.started_at || undefined)} />
              <DetailField label="Ringing" value={formatDateTime(currentSession.ringing_at || undefined)} />
              <DetailField label="Accepted" value={formatDateTime(currentSession.accepted_at || undefined)} />
              <DetailField label="Active" value={formatDateTime(currentSession.active_at || undefined)} />
              <DetailField label="Ended" value={formatDateTime(currentSession.ended_at || undefined)} />
              <DetailField label="Expires" value={formatDateTime(currentSession.expires_at || undefined)} />
              <DetailField label="LiveKit URL" value={runtimeConfig.data?.livekit_url || (runtimeConfig.isError ? 'runtime config unavailable' : 'loading')} />
              <DetailField label="Ringing duration seconds" value={currentSession.ringing_duration_seconds} />
              <DetailField label="Talk duration seconds" value={currentSession.talk_duration_seconds} />
              <DetailField label="Total duration seconds" value={currentSession.total_duration_seconds} />
              <DetailField label="Recording status" value={currentSession.recording_status} />
              <DetailField label="Transcript status" value={currentSession.transcript_status} />
              <DetailField label="Summary status" value={currentSession.summary_status} />
            </div>

            {runtimeConfig.isError ? <div className="section-subtitle">Runtime config is unavailable. Accept may fail until the page can refresh the LiveKit URL.</div> : null}
            <div role="status" className="section-subtitle">{sanitizeDisplayText(message)}</div>
            <div ref={remoteAudioRef} aria-hidden="true" />
            <div className="inline-actions">
              <Button variant="primary" disabled={!canAccept} onClick={() => currentSession && acceptMutation.mutate(currentSession)}>
                {acceptMutation.isPending ? 'Accepting...' : 'Accept WebCall'}
              </Button>
              <Button variant="secondary" disabled={!canReject || rejectMutation.isPending} onClick={() => rejectMutation.mutate()}>
                {rejectMutation.isPending ? 'Rejecting...' : 'Reject WebCall'}
              </Button>
              <Button variant="secondary" disabled={!connected} onClick={() => void toggleMute()}>{muted ? 'Unmute' : 'Mute'}</Button>
              <Button variant="secondary" disabled={!currentSession || terminal || endMutation.isPending} onClick={() => endMutation.mutate()}>
                {endMutation.isPending ? 'Ending...' : 'End WebCall'}
              </Button>
              <Button variant="secondary" disabled={sessions.isFetching} onClick={() => void sessions.refetch()}>{sessions.isFetching ? 'Refreshing...' : 'Refresh sessions'}</Button>
            </div>

            <div className="stack compact" data-testid="webcall-session-queue">
              <strong>Session queue · {allSessions.length}</strong>
              {allSessions.map((session) => (
                <div key={session.voice_session_id} className="queue-card">
                  <div className="badges">
                    <Badge tone={statusTone(session.status)}>{readableStatus(session.status)}</Badge>
                    <Badge>{sanitizeDisplayText(session.provider)}</Badge>
                  </div>
                  <div className="queue-card-title">{valueOrDash(session.voice_session_id)}</div>
                  <div className="queue-card-meta">Room {valueOrDash(roomNameFor(session))} · Ringing {valueOrDash(formatDateTime(session.ringing_at || undefined))} · Ended {valueOrDash(formatDateTime(session.ended_at || undefined))}</div>
                </div>
              ))}
            </div>

            <div className="stack compact" data-testid="webcall-operational-queue">
              <strong>WebCall Operational Queue</strong>
              <div className="inline-actions" role="tablist" aria-label="WebCall Operational Queue tabs">
                {QUEUE_TABS.map((tab) => (
                  <Button key={tab.key} variant={queueTab === tab.key ? 'primary' : 'secondary'} onClick={() => setQueueTab(tab.key)}>
                    {tab.label}
                  </Button>
                ))}
              </div>
              {operationalQueue.isLoading ? <div className="section-subtitle">Loading WebCall queue...</div> : null}
              {operationalQueue.isError ? <div className="section-subtitle">Unable to load WebCall queue.</div> : null}
              {!operationalQueue.isLoading && !operationalQueue.isError && !(operationalQueue.data?.items ?? []).length ? <EmptyState text="No WebCall sessions in this queue." /> : null}
              {(operationalQueue.data?.items ?? []).map((item) => (
                <button
                  key={`${queueTab}-${item.voice_session_id}`}
                  className={`queue-card ${ticketId === item.ticket_id ? 'selected' : ''}`}
                  onClick={() => onSelectTicket?.(item.ticket_id)}
                >
                  <div className="badges">
                    <Badge tone={statusTone(item.status)}>{readableStatus(item.status)}</Badge>
                    <Badge>{sanitizeDisplayText(item.provider)}</Badge>
                    {item.ticket_no ? <Badge>{sanitizeDisplayText(item.ticket_no)}</Badge> : null}
                  </div>
                  <div className="queue-card-title">{valueOrDash(item.visitor_label)} · {valueOrDash(item.voice_session_id)}</div>
                  <div className="queue-card-meta">Ticket {valueOrDash(item.ticket_id)} · Ringing {valueOrDash(formatDateTime(item.ringing_at || undefined))} · Ended {valueOrDash(formatDateTime(item.ended_at || undefined))}</div>
                </button>
              ))}
            </div>
          </div>
        ) : null}
      </CardBody>
    </Card>
  )
}

import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError } from '@/lib/api'
import { webchatVoiceApi } from '@/lib/webchatVoiceApi'
import type { WebchatVoiceActionType, WebchatVoiceSession } from '@/lib/webchatVoiceTypes'
import { formatDateTime, sanitizeDisplayText } from '@/lib/format'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { EmptyState } from '@/components/ui/EmptyState'
import { WebCallQueueFilters } from '@/components/webcall/WebCallQueueFilters'
import { Field, Input, Textarea } from '@/components/ui/Field'
import { useSession } from '@/hooks/useAuth'
import {
  canAcceptWebcallVoice,
  canControlWebcallVoice,
  canEndWebcallVoice,
  canReadWebcallVoice,
  canRejectWebcallVoice,
  canViewWebcallVoiceQueue,
  canViewWebchatDebug,
} from '@/lib/access'

const ACTIVE_STATUSES = new Set(['created', 'ringing', 'accepted', 'active'])
const CALL_CONTROL_READY_STATUSES = new Set(['accepted', 'active'])
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
const ACTION_LABELS: Record<string, string> = {
  hold: 'Hold',
  resume: 'Resume',
  mute: 'Mute',
  unmute: 'Unmute',
  keypad: 'Keypad',
  transfer: 'Transfer',
  add_participant: 'Add participant',
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

type LocalAudioTrack = any
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

function formatOffsetMs(value?: number | null) {
  if (value === null || value === undefined) return '-'
  if (value < 1000) return `${value}ms`
  return `${Math.round(value / 100) / 10}s`
}

function actionLabel(actionType?: string | null) {
  const raw = String(actionType || '').toLowerCase()
  return ACTION_LABELS[raw] || valueOrDash(actionType)
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
    return err.message
  }
  if (err instanceof Error) {
    if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError') return 'Microphone permission was denied. Allow microphone access and accept the WebCall again.'
    if (err.name === 'SecurityError') return 'Microphone is blocked by system or browser security policy. Please enable microphone access for this site.'
    if (err.name === 'NotReadableError' || err.name === 'TrackStartError') return 'Microphone is currently in use by another application. Close other apps and retry.'
    if (err.name === 'NotFoundError' || err.name === 'DevicesNotFoundError') return 'No microphone device was found. Connect a microphone and retry.'
    if (err.name === 'NotSupportedError' || err.name === 'TypeError') return 'This browser does not support required microphone APIs for WebCall.'
    return 'WebCall request failed. Please refresh and try again.'
  }
  return 'WebCall request failed. Please refresh and try again.'
}

function DetailField({ label, value }: { label: string; value?: string | number | null }) {
  return <div className="kv"><label>{label}</label><div>{valueOrDash(value)}</div></div>
}

export function AgentWebCallPanel({ ticketId, conversationId, ticketNo, visitorLabel, onActivity, onSelectTicket }: AgentWebCallPanelProps) {
  const client = useQueryClient()
  const session = useSession()
  const [callState, setCallState] = useState<AgentCallState>('idle')
  const [message, setMessage] = useState('Waiting for an incoming WebCall on this ticket.')
  const [muted, setMuted] = useState(false)
  const [joinedVoiceSessionId, setJoinedVoiceSessionId] = useState<string | null>(null)
  const [queueTab, setQueueTab] = useState<(typeof QUEUE_TABS)[number]['key']>('incoming')
  const [callNote, setCallNote] = useState('')
  const [keypadDigits, setKeypadDigits] = useState('')
  const [transferTarget, setTransferTarget] = useState('')
  const [participantTarget, setParticipantTarget] = useState('')
  const roomRef = useRef<any | null>(null)
  const localAudioRef = useRef<LocalAudioTrack | null>(null)
  const remoteAudioRef = useRef<HTMLDivElement | null>(null)
  const canReadVoice = canReadWebcallVoice(session.data)
  const canViewQueue = canViewWebcallVoiceQueue(session.data)
  const canAcceptVoice = canAcceptWebcallVoice(session.data)
  const canRejectVoice = canRejectWebcallVoice(session.data)
  const canEndVoice = canEndWebcallVoice(session.data)
  const canControlVoice = canControlWebcallVoice(session.data)
  const canViewDebug = canViewWebchatDebug(session.data)

  const runtimeConfig = useQuery({
    queryKey: ['webchatVoiceRuntimeConfig'],
    queryFn: ({ signal }) => webchatVoiceApi.runtimeConfig({ signal }),
    enabled: canAcceptVoice,
    refetchInterval: 30000,
    retry: false,
  })

  const sessions = useQuery({
    queryKey: ['webchatVoiceSessions', ticketId],
    queryFn: ({ signal }) => webchatVoiceApi.listSessions(ticketId as number, { signal }),
    enabled: !!ticketId && canReadVoice,
    refetchInterval: callState === 'connected' ? 8000 : 4000,
    retry: false,
  })

  const operationalQueue = useQuery({
    queryKey: ['webchatVoiceOperationalQueue', queueTab],
    queryFn: ({ signal }) => webchatVoiceApi.incomingSessions({ status: queueTab, limit: 50 }, { signal }),
    enabled: canViewQueue,
    refetchInterval: queueTab === 'incoming' ? 4000 : 8000,
    retry: false,
  })

  const allSessions = sessions.data?.items ?? []
  const currentSession = useMemo(() => activeVoiceSession(sessions.data?.items), [sessions.data?.items]) as SessionWithOptionalOpsFields | null
  const terminal = currentSession ? TERMINAL_STATUSES.has(String(currentSession.status)) : false
  const hasLiveCall = Boolean(currentSession && !terminal)
  const connected = callState === 'connected'
  const busyAccepting = callState === 'accepting' || callState === 'requesting_mic' || callState === 'connecting'
  const canAccept = Boolean(canAcceptVoice && ticketId && currentSession && !terminal && !connected && !busyAccepting)
  const canReject = Boolean(canRejectVoice && ticketId && currentSession && REJECT_READY_STATUSES.has(String(currentSession.status)) && !connected && !busyAccepting)
  const canEnd = Boolean(canEndVoice && currentSession && !terminal)
  const canUseSessionActions = Boolean(canControlVoice && currentSession && CALL_CONTROL_READY_STATUSES.has(String(currentSession.status)))

  const evidence = useQuery({
    queryKey: ['webchatVoiceEvidence', ticketId, currentSession?.voice_session_id],
    queryFn: ({ signal }) => webchatVoiceApi.evidence(ticketId as number, currentSession?.voice_session_id as string, { limit: 50 }, { signal }),
    enabled: !!ticketId && !!currentSession && canReadVoice,
    refetchInterval: hasLiveCall ? 4000 : 15000,
    retry: false,
  })

  const actionHistory = useQuery({
    queryKey: ['webchatVoiceActions', ticketId, currentSession?.voice_session_id],
    queryFn: ({ signal }) => webchatVoiceApi.actions(ticketId as number, currentSession?.voice_session_id as string, { limit: 8 }, { signal }),
    enabled: !!ticketId && !!currentSession && canReadVoice,
    refetchInterval: hasLiveCall ? 8000 : 20000,
    retry: false,
  })

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
      await client.invalidateQueries({ queryKey: ['webchatVoiceEvidence', ticketId] })
      await client.invalidateQueries({ queryKey: ['webchatVoiceActions', ticketId] })
    }
    await client.invalidateQueries({ queryKey: ['webchatConversations'] })
    await client.invalidateQueries({ queryKey: ['webchatVoiceOperationalQueue'] })
    onActivity?.()
  }

  const acceptMutation = useMutation({
    mutationFn: async (session: WebchatVoiceSession) => {
      if (!ticketId) throw new Error('No ticket selected')
      setCallState('requesting_mic')
      setMessage('Requesting microphone permission...')
      const { Room, RoomEvent, Track, createLocalAudioTrack } = await import('livekit-client')
      const audioTrack = await createLocalAudioTrack({
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      })
      localAudioRef.current = audioTrack

      setCallState('accepting')
      setMessage('Accepting WebCall...')
      const accepted = await webchatVoiceApi.acceptSession(ticketId, session.voice_session_id)
      if (accepted.provider !== 'livekit') {
        await cleanupRoom()
        setMessage(`WebCall accepted with provider ${accepted.provider}. Real browser audio requires provider=livekit.`)
        setCallState('idle')
        return accepted
      }
      const livekitUrl = runtimeConfig.data?.livekit_url
      if (!livekitUrl) throw new Error('LiveKit URL is missing from runtime config')
      if (!accepted.participant_token) throw new Error('Agent participant credential missing from accept response')

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

  const saveNoteMutation = useMutation({
    mutationFn: async () => {
      if (!ticketId || !currentSession) throw new Error('No WebCall session selected')
      return webchatVoiceApi.saveNote(ticketId, currentSession.voice_session_id, {
        body: callNote,
        source: 'operator_workbench',
      })
    },
    onSuccess: async (result) => {
      setCallNote('')
      setMessage(`Call note saved to ticket timeline (#${result.note_id}).`)
      await invalidateVoiceViews()
      if (ticketId) await client.invalidateQueries({ queryKey: ['ticketTimeline', ticketId] })
    },
    onError: (err: unknown) => setMessage(safeErrorMessage(err)),
  })

  const actionMutation = useMutation({
    mutationFn: async ({ actionType, target, digits, note }: { actionType: WebchatVoiceActionType; target?: string; digits?: string; note?: string }) => {
      if (!ticketId || !currentSession) throw new Error('No WebCall session selected')
      return webchatVoiceApi.createAction(ticketId, currentSession.voice_session_id, actionType, {
        target: target?.trim() || undefined,
        digits: digits?.trim() || undefined,
        note: note?.trim() || undefined,
      })
    },
    onSuccess: async (result) => {
      setMessage(`${actionLabel(result.action.action_type)} recorded. ${result.action.provider_reason === 'provider_adapter_pending' ? 'Provider adapter execution is still pending.' : result.action.provider_status}`)
      if (result.action.action_type === 'keypad') setKeypadDigits('')
      await invalidateVoiceViews()
      if (ticketId) await client.invalidateQueries({ queryKey: ['ticketTimeline', ticketId] })
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
      if (canControlVoice && ticketId && currentSession) actionMutation.mutate({ actionType: 'unmute' })
    } else {
      await audioTrack.mute()
      setMuted(true)
      setMessage('Microphone muted.')
      if (canControlVoice && ticketId && currentSession) actionMutation.mutate({ actionType: 'mute' })
    }
  }

  return (
    <Card>
      <CardHeader title="Agent WebCall" subtitle="Incoming-call queue for the selected WebChat ticket. Microphone access is requested only after Accept WebCall." />
      <CardBody>
        {!ticketId ? <EmptyState text="Select a WebChat ticket to monitor WebCall sessions." /> : null}
        {ticketId && !canReadVoice ? <EmptyState title="WebCall 只读受限" description="当前账号缺少 webcall.voice.read，不能查看或操作语音会话。" reason="需要处理 WebCall 时，请联系主管授权。" /> : null}
        {ticketId && sessions.isLoading ? <div className="section-subtitle" data-testid="webcall-loading-state">Loading WebCall sessions...</div> : null}
        {ticketId && sessions.isError ? (
          <div className="message" data-testid="webcall-error-state" data-role="agent">
            Unable to load WebCall sessions: {safeErrorMessage(sessions.error)}
            <div className="inline-actions" style={{ marginTop: 8 }}><Button variant="secondary" onClick={() => void sessions.refetch()}>Retry</Button></div>
          </div>
        ) : null}
        {ticketId && !sessions.isLoading && !sessions.isError && !currentSession ? <EmptyState text="No incoming WebCall sessions for this ticket." /> : null}
        {!currentSession && canViewQueue ? (
          <div className="stack compact" data-testid="webcall-operational-queue">
            <strong>WebCall Operational Queue</strong>
            <WebCallQueueFilters
              tabs={QUEUE_TABS}
              activeKey={queueTab}
              onSelect={setQueueTab}
            />
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
              <DetailField label="Visitor" value={visitorLabel} />
              <DetailField label="Status" value={readableStatus(currentSession.status)} />
              <DetailField label="Started" value={formatDateTime(currentSession.started_at || undefined)} />
              <DetailField label="Ringing" value={formatDateTime(currentSession.ringing_at || undefined)} />
              <DetailField label="Accepted" value={formatDateTime(currentSession.accepted_at || undefined)} />
              <DetailField label="Active" value={formatDateTime(currentSession.active_at || undefined)} />
              <DetailField label="Ended" value={formatDateTime(currentSession.ended_at || undefined)} />
              <DetailField label="Ringing duration seconds" value={currentSession.ringing_duration_seconds} />
              <DetailField label="Talk duration seconds" value={currentSession.talk_duration_seconds} />
              <DetailField label="Total duration seconds" value={currentSession.total_duration_seconds} />
              <DetailField label="Recording status" value={currentSession.recording_status} />
              <DetailField label="Transcript status" value={currentSession.transcript_status} />
              <DetailField label="Summary status" value={currentSession.summary_status} />
              {canViewDebug ? (
                <>
                  <DetailField label="Conversation ID" value={conversationId} />
                  <DetailField label="Voice session ID" value={currentSession.voice_session_id} />
                  <DetailField label="Room" value={roomNameFor(currentSession)} />
                  <DetailField label="Provider" value={currentSession.provider} />
                  <DetailField label="Accepted by user ID" value={currentSession.accepted_by_user_id} />
                  <DetailField label="Ended by user ID" value={currentSession.ended_by_user_id} />
                  <DetailField label="Expires" value={formatDateTime(currentSession.expires_at || undefined)} />
                  <DetailField label="LiveKit URL" value={runtimeConfig.data?.livekit_url || (runtimeConfig.isError ? 'runtime config unavailable' : 'loading')} />
                </>
              ) : null}
            </div>

            {canAcceptVoice && runtimeConfig.isError ? <div className="section-subtitle">Runtime config is unavailable. Accept may fail until the page can refresh the LiveKit URL.</div> : null}
            <div role="status" className="section-subtitle">{sanitizeDisplayText(message)}</div>
            <div ref={remoteAudioRef} aria-hidden="true" />
            <div className="stack compact" data-testid="webcall-live-transcript-evidence">
              <div className="button-row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
                <strong>Live Transcript / AI Evidence</strong>
                <Button variant="secondary" disabled={evidence.isFetching} onClick={() => void evidence.refetch()}>{evidence.isFetching ? 'Refreshing...' : 'Refresh evidence'}</Button>
              </div>
              <div className="badges">
                <Badge>{evidence.data?.transcript_status || currentSession.transcript_status || 'transcript unknown'}</Badge>
                <Badge>{evidence.data?.summary_status || currentSession.summary_status || 'summary unknown'}</Badge>
                {evidence.data?.ai_agent_status ? <Badge tone="warning">AI {sanitizeDisplayText(evidence.data.ai_agent_status)}</Badge> : null}
                {typeof evidence.data?.ai_turn_count === 'number' ? <Badge>{evidence.data.ai_turn_count} AI turns</Badge> : null}
              </div>
              {evidence.isLoading ? <div className="section-subtitle">Loading transcript evidence...</div> : null}
              {evidence.isError ? <div className="section-subtitle">Unable to load transcript evidence: {safeErrorMessage(evidence.error)}</div> : null}
              {!evidence.isLoading && !evidence.isError && !(evidence.data?.transcript_segments ?? []).length ? <EmptyState text="No redacted transcript segments for this WebCall yet." /> : null}
              {(evidence.data?.transcript_segments ?? []).map((segment) => (
                <div className="message" data-role={segment.speaker_type === 'agent' ? 'agent' : 'customer'} key={segment.id}>
                  <div className="message-head">
                    <strong>{sanitizeDisplayText(segment.speaker_label || segment.speaker_type)}</strong>
                    <span>{formatOffsetMs(segment.start_ms)} - {formatOffsetMs(segment.end_ms)}</span>
                  </div>
                  <div>{sanitizeDisplayText(segment.text)}</div>
                </div>
              ))}
              {(evidence.data?.ai_turns ?? []).length ? (
                <div className="stack compact" data-testid="webcall-ai-turn-evidence">
                  <strong>AI turn evidence</strong>
                  {(evidence.data?.ai_turns ?? []).map((turn) => (
                    <div className="message" data-role="agent" key={turn.id}>
                      <div className="message-head">
                        <strong>Turn {turn.turn_index} · {sanitizeDisplayText(turn.intent || 'unknown intent')}</strong>
                        <span>{formatDateTime(turn.created_at || undefined)}</span>
                      </div>
                      {turn.customer_text_redacted ? <div>Customer: {sanitizeDisplayText(turn.customer_text_redacted)}</div> : null}
                      {turn.ai_response_text_redacted ? <div>AI: {sanitizeDisplayText(turn.ai_response_text_redacted)}</div> : null}
                      <div className="badges" style={{ marginTop: 8 }}>
                        <Badge tone={turn.handoff_required ? 'warning' : 'success'}>{turn.handoff_required ? 'handoff required' : 'safe reply'}</Badge>
                        {turn.action ? <Badge>{sanitizeDisplayText(turn.action)}</Badge> : null}
                        {typeof turn.confidence === 'number' ? <Badge>{turn.confidence}% confidence</Badge> : null}
                      </div>
                    </div>
                  ))}
                </div>
              ) : null}
              {(evidence.data?.ai_actions ?? []).length ? (
                <div className="stack compact" data-testid="webcall-ai-action-evidence">
                  <strong>AI action decisions</strong>
                  {(evidence.data?.ai_actions ?? []).map((action) => (
                    <div className="message" data-role="agent" key={action.id}>
                      <div className="message-head"><strong>{sanitizeDisplayText(action.model_action)}</strong><span>{formatDateTime(action.created_at || undefined)}</span></div>
                      <div>{sanitizeDisplayText(action.nexus_decision)} · {sanitizeDisplayText(action.decision_reason || action.result_status || 'no decision reason')}</div>
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
            <div className="stack compact" data-testid="webcall-call-notes">
              <strong>Call Notes</strong>
              <Field label="通话备注" hint="保存后写入 TicketInternalNote、ticket timeline、WebChat event 和 admin audit。">
                <Textarea
                  rows={4}
                  value={callNote}
                  onChange={(event) => setCallNote(event.target.value)}
                  placeholder="记录身份核验、客户承诺、后续动作或需要交接的信息"
                />
              </Field>
              <div className="inline-actions">
                <Button
                  variant="secondary"
                  disabled={!ticketId || !currentSession || !callNote.trim() || saveNoteMutation.isPending}
                  onClick={() => saveNoteMutation.mutate()}
                >
                  {saveNoteMutation.isPending ? 'Saving note...' : 'Save call note'}
                </Button>
              </div>
            </div>
            <div className="stack compact" data-testid="webcall-session-actions">
              <div className="button-row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
                <strong>Session Actions</strong>
                <Badge tone={canControlVoice ? 'warning' : 'default'}>{canControlVoice ? 'audited command path' : 'control restricted'}</Badge>
              </div>
              <div className="button-row">
                <Button variant="secondary" disabled={!canUseSessionActions || actionMutation.isPending} onClick={() => actionMutation.mutate({ actionType: 'hold' })}>Hold</Button>
                <Button variant="secondary" disabled={!canUseSessionActions || actionMutation.isPending} onClick={() => actionMutation.mutate({ actionType: 'resume' })}>Resume</Button>
              </div>
              <div className="kv-grid">
                <Field label="Keypad digits" hint="Digits are redacted in timeline and audit evidence.">
                  <Input value={keypadDigits} onChange={(event) => setKeypadDigits(event.target.value.replace(/[^0-9*#]/g, ''))} placeholder="123#" inputMode="tel" />
                </Field>
                <Field label="Transfer target">
                  <Input value={transferTarget} onChange={(event) => setTransferTarget(event.target.value)} placeholder="queue or agent" />
                </Field>
                <Field label="Participant">
                  <Input value={participantTarget} onChange={(event) => setParticipantTarget(event.target.value)} placeholder="agent, queue, or verified contact" />
                </Field>
              </div>
              <div className="button-row">
                <Button variant="secondary" disabled={!canUseSessionActions || !keypadDigits || actionMutation.isPending} onClick={() => actionMutation.mutate({ actionType: 'keypad', digits: keypadDigits })}>Send keypad</Button>
                <Button variant="secondary" disabled={!canUseSessionActions || !transferTarget.trim() || actionMutation.isPending} onClick={() => actionMutation.mutate({ actionType: 'transfer', target: transferTarget, note: 'Requested from WebCall operator workbench' })}>Transfer</Button>
                <Button variant="secondary" disabled={!canUseSessionActions || !participantTarget.trim() || actionMutation.isPending} onClick={() => actionMutation.mutate({ actionType: 'add_participant', target: participantTarget, note: 'Requested from WebCall operator workbench' })}>Add participant</Button>
              </div>
              {actionHistory.isLoading ? <div className="section-subtitle">Loading session actions...</div> : null}
              {actionHistory.isError ? <div className="section-subtitle">Unable to load session actions: {safeErrorMessage(actionHistory.error)}</div> : null}
              {!actionHistory.isLoading && !actionHistory.isError && !(actionHistory.data?.items ?? []).length ? <EmptyState text="No audited session actions for this WebCall yet." /> : null}
              {(actionHistory.data?.items ?? []).map((action) => (
                <div className="queue-card" key={action.id}>
                  <div className="badges">
                    <Badge tone={action.provider_reason === 'provider_adapter_pending' ? 'warning' : 'success'}>{sanitizeDisplayText(action.provider_status)}</Badge>
                    <Badge>{sanitizeDisplayText(action.provider_reason)}</Badge>
                  </div>
                  <div className="queue-card-title">{actionLabel(action.action_type)} · {formatDateTime(action.created_at || undefined)}</div>
                  <div className="queue-card-meta">Actor {valueOrDash(action.actor_user_id)} · Ticket event {valueOrDash(action.ticket_event_id)} · Audit {valueOrDash(action.audit_id)}</div>
                </div>
              ))}
            </div>
            <div className="inline-actions">
              {canAcceptVoice ? (
                <Button variant="primary" disabled={!canAccept} onClick={() => currentSession && acceptMutation.mutate(currentSession)}>
                  {acceptMutation.isPending ? 'Accepting...' : 'Accept WebCall'}
                </Button>
              ) : null}
              {canRejectVoice ? (
                <Button variant="secondary" disabled={!canReject || rejectMutation.isPending} onClick={() => rejectMutation.mutate()}>
                  {rejectMutation.isPending ? 'Rejecting...' : 'Reject WebCall'}
                </Button>
              ) : null}
              <Button variant="secondary" disabled={!connected} onClick={() => void toggleMute()}>{muted ? 'Unmute' : 'Mute'}</Button>
              {canEndVoice ? (
                <Button variant="secondary" disabled={!canEnd || endMutation.isPending} onClick={() => endMutation.mutate()}>
                  {endMutation.isPending ? 'Ending...' : 'End WebCall'}
                </Button>
              ) : null}
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

            {canViewQueue ? <div className="stack compact" data-testid="webcall-operational-queue">
              <strong>WebCall Operational Queue</strong>
              <WebCallQueueFilters
                tabs={QUEUE_TABS}
                activeKey={queueTab}
                onSelect={setQueueTab}
              />
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
            </div> : null}
          </div>
        ) : null}
      </CardBody>
    </Card>
  )
}

import { useEffect, useMemo, useState } from 'react'
import { createRoute, redirect } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import { webchatVoiceApi } from '@/lib/webchatVoiceApi'
import type { WebchatConversation } from '@/lib/types'
import type { WebchatVoiceSession } from '@/lib/webchatVoiceTypes'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { EmptyState } from '@/components/ui/EmptyState'
import { PageHeader } from '@/components/ui/PageHeader'
import { Skeleton } from '@/components/ui/Skeleton'
import { Toast } from '@/components/ui/Toast'
import { formatDateTime, sanitizeDisplayText, statusTone } from '@/lib/format'

function voiceTone(status?: string | null): 'default' | 'warning' | 'success' | 'danger' {
  if (!status) return 'default'
  if (status === 'ringing' || status === 'created' || status === 'accepted') return 'warning'
  if (status === 'active' || status === 'ended') return 'success'
  if (status === 'failed' || status === 'cancelled' || status === 'missed') return 'danger'
  return 'default'
}

function activeVoiceSession(items?: WebchatVoiceSession[]) {
  return (items ?? []).find((item) => ['created', 'ringing', 'accepted', 'active'].includes(item.status)) ?? null
}

function VoiceSessionPanel({ ticketId, session }: { ticketId: number; session: WebchatVoiceSession }) {
  const client = useQueryClient()
  const [toast, setToast] = useState<{ message: string; tone?: 'default' | 'danger' | 'success' } | null>(null)

  const acceptMutation = useMutation({
    mutationFn: () => webchatVoiceApi.acceptSession(ticketId, session.voice_session_id),
    onSuccess: async () => {
      setToast({ message: 'Mock voice call accepted. No real media is connected in this phase.', tone: 'success' })
      await client.invalidateQueries({ queryKey: ['webchatVoiceSessions', ticketId] })
      await client.invalidateQueries({ queryKey: ['webchatThread', ticketId] })
      await client.invalidateQueries({ queryKey: ['webchatConversations'] })
    },
    onError: (err: Error) => setToast({ message: err.message || 'Failed to accept voice call', tone: 'danger' }),
  })

  const endMutation = useMutation({
    mutationFn: () => webchatVoiceApi.endSession(ticketId, session.voice_session_id),
    onSuccess: async () => {
      setToast({ message: 'Mock voice call ended and ticket evidence was written.', tone: 'success' })
      await client.invalidateQueries({ queryKey: ['webchatVoiceSessions', ticketId] })
      await client.invalidateQueries({ queryKey: ['webchatThread', ticketId] })
      await client.invalidateQueries({ queryKey: ['webchatConversations'] })
    },
    onError: (err: Error) => setToast({ message: err.message || 'Failed to end voice call', tone: 'danger' }),
  })

  const terminal = ['ended', 'failed', 'cancelled', 'missed'].includes(session.status)
  return (
    <Card className="soft">
      <CardHeader title="Incoming WebChat Voice" subtitle="Mock state machine only. This phase does not connect LiveKit, microphone, recording, or realtime transcription." />
      <CardBody>
        <div className="stack">
          <div className="badges">
            <Badge tone={voiceTone(session.status)}>{sanitizeDisplayText(session.status)}</Badge>
            <Badge>{sanitizeDisplayText(session.provider)}</Badge>
            <Badge>room {sanitizeDisplayText(session.room_name)}</Badge>
            {session.accepted_by_user_id ? <Badge tone="success">accepted by #{session.accepted_by_user_id}</Badge> : <Badge tone="warning">waiting</Badge>}
          </div>
          <div className="kv-grid">
            <div className="kv"><label>Voice session</label><div>{sanitizeDisplayText(session.voice_session_id)}</div></div>
            <div className="kv"><label>Voice page</label><div>{sanitizeDisplayText(session.voice_page_url || '-')}</div></div>
            <div className="kv"><label>Started</label><div>{formatDateTime(session.started_at)}</div></div>
            <div className="kv"><label>Ringing</label><div>{formatDateTime(session.ringing_at)}</div></div>
            <div className="kv"><label>Accepted</label><div>{formatDateTime(session.accepted_at)}</div></div>
            <div className="kv"><label>Ended</label><div>{formatDateTime(session.ended_at)}</div></div>
          </div>
          <div className="toolbar">
            <Button variant="primary" disabled={terminal || session.status === 'active' || acceptMutation.isPending} onClick={() => acceptMutation.mutate()}>
              {acceptMutation.isPending ? 'Accepting…' : 'Accept mock call'}
            </Button>
            <Button variant="secondary" disabled={terminal || endMutation.isPending} onClick={() => endMutation.mutate()}>
              {endMutation.isPending ? 'Ending…' : 'End mock call'}
            </Button>
          </div>
          <div className="section-subtitle">
            Acceptance locks the session to the first accepting admin user. Ending the call writes one final <code>voice_call</code> WebchatMessage into the ticket thread.
          </div>
        </div>
      </CardBody>
      {toast ? <Toast message={toast.message} tone={toast.tone} onClose={() => setToast(null)} /> : null}
    </Card>
  )
}

function WebchatVoiceMockPage() {
  const client = useQueryClient()
  const [selectedTicketId, setSelectedTicketId] = useState<number | null>(null)
  const conversations = useQuery({
    queryKey: ['webchatConversations'],
    queryFn: ({ signal }) => api.webchatConversations({ signal }),
    refetchInterval: 10000,
    retry: false,
  })

  useEffect(() => {
    if (!selectedTicketId && conversations.data?.length) setSelectedTicketId(conversations.data[0].ticket_id)
  }, [conversations.data, selectedTicketId])

  const selectedConversation = useMemo<WebchatConversation | undefined>(
    () => (conversations.data ?? []).find((item) => item.ticket_id === selectedTicketId),
    [conversations.data, selectedTicketId],
  )

  const sessions = useQuery({
    queryKey: ['webchatVoiceSessions', selectedTicketId],
    queryFn: ({ signal }) => webchatVoiceApi.listSessions(selectedTicketId as number, { signal }),
    enabled: !!selectedTicketId,
    refetchInterval: 4000,
    retry: false,
  })

  const currentSession = activeVoiceSession(sessions.data?.items) ?? sessions.data?.items?.[0] ?? null

  return (
    <AppShell>
      <PageHeader
        eyebrow="WebChat Voice"
        title="WebChat Voice Mock Console"
        description="PR 3 mock control surface: receive, accept, and end WebChat Voice sessions. No LiveKit, microphone, recording, or transcription is connected."
        actions={<Button variant="secondary" onClick={() => { void client.invalidateQueries({ queryKey: ['webchatConversations'] }); if (selectedTicketId) void client.invalidateQueries({ queryKey: ['webchatVoiceSessions', selectedTicketId] }) }}>刷新</Button>}
      />

      <div className="page-grid workspace">
        <Card>
          <CardHeader title="WebChat conversations" subtitle="Create a mock voice session from /webchat/demo.html using the orange Voice call button." />
          <CardBody>
            {conversations.isLoading ? <Skeleton lines={8} /> : null}
            <div className="list">
              {(conversations.data ?? []).map((item) => (
                <button key={item.conversation_id} className={`queue-card ${selectedTicketId === item.ticket_id ? 'selected' : ''}`} onClick={() => setSelectedTicketId(item.ticket_id)}>
                  <div className="queue-card-top"><div className="badges"><Badge tone={statusTone(item.status)}>{sanitizeDisplayText(item.status)}</Badge><Badge>WebChat</Badge>{item.needs_human ? <Badge tone="warning">Needs human</Badge> : null}</div></div>
                  <div className="queue-card-title">{sanitizeDisplayText(item.ticket_no)} · {sanitizeDisplayText(item.title)}</div>
                  <div className="queue-card-meta">{sanitizeDisplayText(item.visitor_name || item.visitor_email || item.visitor_phone || 'Anonymous visitor')}</div>
                  <div className="queue-card-meta">{formatDateTime(item.updated_at)}</div>
                </button>
              ))}
              {!conversations.isLoading && !(conversations.data?.length) ? <EmptyState text="No WebChat conversations yet. Open /webchat/demo.html, send a message, then start a mock voice session." /> : null}
            </div>
          </CardBody>
        </Card>

        <div className="stack">
          <Card>
            <CardHeader title="Selected ticket" subtitle="Voice sessions are bound to the existing WebChat conversation and ticket." />
            <CardBody>
              {selectedConversation ? (
                <div className="kv-grid">
                  <div className="kv"><label>Ticket</label><div>{sanitizeDisplayText(selectedConversation.ticket_no)}</div></div>
                  <div className="kv"><label>Ticket ID</label><div>{selectedConversation.ticket_id}</div></div>
                  <div className="kv"><label>Conversation</label><div>{sanitizeDisplayText(selectedConversation.conversation_id)}</div></div>
                  <div className="kv"><label>Visitor</label><div>{sanitizeDisplayText(selectedConversation.visitor_name || 'Anonymous')}</div></div>
                  <div className="kv"><label>Origin</label><div>{sanitizeDisplayText(selectedConversation.origin || '-')}</div></div>
                  <div className="kv"><label>Page</label><div>{sanitizeDisplayText(selectedConversation.page_url || '-')}</div></div>
                </div>
              ) : <EmptyState text="请选择一个 WebChat conversation。" />}
            </CardBody>
          </Card>

          {sessions.isLoading && selectedTicketId ? <Card><CardBody><Skeleton lines={5} /></CardBody></Card> : null}
          {selectedTicketId && currentSession ? <VoiceSessionPanel ticketId={selectedTicketId} session={currentSession} /> : null}
          {selectedTicketId && !sessions.isLoading && !currentSession ? <Card><CardBody><EmptyState text="No voice session for this ticket yet. Start one from the demo page voice button." /></CardBody></Card> : null}
        </div>
      </div>
    </AppShell>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/webchat-voice',
  beforeLoad: () => { if (!getToken()) throw redirect({ to: '/login' }) },
  component: WebchatVoiceMockPage,
})

import { useEffect, useMemo, useState } from 'react'
import { createRoute, redirect } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { api, getToken } from '@/lib/api'
import { webchatVoiceApi } from '@/lib/webchatVoiceApi'
import type { WebchatConversation } from '@/lib/types'
import type { WebchatVoiceSession } from '@/lib/webchatVoiceTypes'

function activeVoiceSession(items?: WebchatVoiceSession[]) {
  return (items ?? []).find((item) => ['created', 'ringing', 'accepted', 'active'].includes(item.status)) ?? null
}

function formatValue(value?: string | number | null) {
  if (value === null || value === undefined || value === '') return '-'
  return String(value)
}

function VoiceSessionPanel({ ticketId, session }: { ticketId: number; session: WebchatVoiceSession }) {
  const client = useQueryClient()
  const [message, setMessage] = useState<string | null>(null)
  const terminal = ['ended', 'failed', 'cancelled', 'missed'].includes(session.status)

  const acceptMutation = useMutation({
    mutationFn: () => webchatVoiceApi.acceptSession(ticketId, session.voice_session_id),
    onSuccess: async () => {
      setMessage('Mock call accepted.')
      await client.invalidateQueries({ queryKey: ['webchatVoiceSessions', ticketId] })
      await client.invalidateQueries({ queryKey: ['webchatConversations'] })
    },
    onError: (err: Error) => setMessage(err.message || 'Accept failed'),
  })

  const endMutation = useMutation({
    mutationFn: () => webchatVoiceApi.endSession(ticketId, session.voice_session_id),
    onSuccess: async () => {
      setMessage('Mock call ended. Ticket evidence was written by the backend.')
      await client.invalidateQueries({ queryKey: ['webchatVoiceSessions', ticketId] })
      await client.invalidateQueries({ queryKey: ['webchatConversations'] })
    },
    onError: (err: Error) => setMessage(err.message || 'End failed'),
  })

  return (
    <section style={{ border: '1px solid #e5e7eb', borderRadius: 16, padding: 18, background: '#fff' }}>
      <h2 style={{ marginTop: 0 }}>Mock voice session</h2>
      <p style={{ color: '#475467' }}>This page controls the mock state machine only.</p>
      <dl style={{ display: 'grid', gridTemplateColumns: '160px 1fr', gap: 8 }}>
        <dt>Session</dt><dd>{formatValue(session.voice_session_id)}</dd>
        <dt>Status</dt><dd>{formatValue(session.status)}</dd>
        <dt>Provider</dt><dd>{formatValue(session.provider)}</dd>
        <dt>Room</dt><dd>{formatValue(session.room_name)}</dd>
        <dt>Accepted by</dt><dd>{formatValue(session.accepted_by_user_id)}</dd>
        <dt>Started</dt><dd>{formatValue(session.started_at)}</dd>
        <dt>Ringing</dt><dd>{formatValue(session.ringing_at)}</dd>
        <dt>Accepted</dt><dd>{formatValue(session.accepted_at)}</dd>
        <dt>Ended</dt><dd>{formatValue(session.ended_at)}</dd>
      </dl>
      <div style={{ display: 'flex', gap: 10, marginTop: 16 }}>
        <button disabled={terminal || session.status === 'active' || acceptMutation.isPending} onClick={() => acceptMutation.mutate()}>
          {acceptMutation.isPending ? 'Accepting…' : 'Accept mock call'}
        </button>
        <button disabled={terminal || endMutation.isPending} onClick={() => endMutation.mutate()}>
          {endMutation.isPending ? 'Ending…' : 'End mock call'}
        </button>
      </div>
      {message ? <p role="status" style={{ marginBottom: 0 }}>{message}</p> : null}
    </section>
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
    <main style={{ padding: 28, display: 'grid', gridTemplateColumns: 'minmax(280px, 420px) 1fr', gap: 20 }}>
      <section style={{ border: '1px solid #e5e7eb', borderRadius: 16, padding: 18, background: '#fff' }}>
        <h1 style={{ marginTop: 0 }}>WebChat Voice Mock Console</h1>
        <p style={{ color: '#475467' }}>Select a WebChat ticket and operate its mock voice session.</p>
        <button onClick={() => { void client.invalidateQueries({ queryKey: ['webchatConversations'] }); if (selectedTicketId) void client.invalidateQueries({ queryKey: ['webchatVoiceSessions', selectedTicketId] }) }}>
          Refresh
        </button>
        {conversations.isLoading ? <p>Loading conversations…</p> : null}
        <div style={{ display: 'grid', gap: 8, marginTop: 14 }}>
          {(conversations.data ?? []).map((item) => (
            <button key={item.conversation_id} style={{ textAlign: 'left', padding: 10, borderRadius: 10, border: selectedTicketId === item.ticket_id ? '2px solid #f97316' : '1px solid #e5e7eb', background: '#fff' }} onClick={() => setSelectedTicketId(item.ticket_id)}>
              <strong>{formatValue(item.ticket_no)}</strong><br />
              <span>{formatValue(item.title)}</span><br />
              <small>{formatValue(item.visitor_name || item.visitor_email || item.visitor_phone || 'Anonymous visitor')}</small>
            </button>
          ))}
        </div>
      </section>

      <div style={{ display: 'grid', gap: 20 }}>
        <section style={{ border: '1px solid #e5e7eb', borderRadius: 16, padding: 18, background: '#fff' }}>
          <h2 style={{ marginTop: 0 }}>Selected ticket</h2>
          {selectedConversation ? (
            <dl style={{ display: 'grid', gridTemplateColumns: '140px 1fr', gap: 8 }}>
              <dt>Ticket</dt><dd>{formatValue(selectedConversation.ticket_no)}</dd>
              <dt>Ticket ID</dt><dd>{selectedConversation.ticket_id}</dd>
              <dt>Conversation</dt><dd>{formatValue(selectedConversation.conversation_id)}</dd>
              <dt>Visitor</dt><dd>{formatValue(selectedConversation.visitor_name || 'Anonymous')}</dd>
              <dt>Origin</dt><dd>{formatValue(selectedConversation.origin)}</dd>
              <dt>Page</dt><dd>{formatValue(selectedConversation.page_url)}</dd>
            </dl>
          ) : <p>No WebChat conversation selected.</p>}
        </section>

        {sessions.isLoading && selectedTicketId ? <p>Loading voice sessions…</p> : null}
        {selectedTicketId && currentSession ? <VoiceSessionPanel ticketId={selectedTicketId} session={currentSession} /> : null}
        {selectedTicketId && !sessions.isLoading && !currentSession ? <p>No mock voice session exists for this ticket yet.</p> : null}
      </div>
    </main>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/webchat-voice',
  beforeLoad: () => { if (!getToken()) throw redirect({ to: '/login' }) },
  component: WebchatVoiceMockPage,
})

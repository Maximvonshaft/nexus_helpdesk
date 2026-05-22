import { useEffect, useMemo, useState } from 'react'
import { createRoute, redirect } from '@tanstack/react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { api, getToken } from '@/lib/api'
import type { WebchatConversation } from '@/lib/types'
import { webchatVoiceApi } from '@/lib/webchatVoiceApi'
import type { WebchatVoiceIncomingSession } from '@/lib/webchatVoiceTypes'
import { sanitizeDisplayText } from '@/lib/format'
import { AgentWebCallPanel } from '@/components/webcall/AgentWebCallPanel'
import { Button } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/EmptyState'

function formatValue(value?: string | number | null) {
  if (value === null || value === undefined || value === '') return '-'
  return sanitizeDisplayText(String(value))
}

function visitorLabel(item?: WebchatConversation | null) {
  if (!item) return 'Anonymous visitor'
  return item.visitor_name || item.visitor_email || item.visitor_phone || 'Anonymous visitor'
}

function incomingVisitorLabel(item?: WebchatVoiceIncomingSession | null) {
  if (!item) return 'Anonymous visitor'
  return item.visitor_label || 'Anonymous visitor'
}

function WebCallAgentConsolePage() {
  const client = useQueryClient()
  const [selectedTicketId, setSelectedTicketId] = useState<number | null>(null)

  const conversations = useQuery({
    queryKey: ['webchatConversations'],
    queryFn: ({ signal }) => api.webchatConversations({ signal }),
    refetchInterval: 10000,
    retry: false,
  })

  const incomingSessions = useQuery({
    queryKey: ['webchatVoiceIncomingSessions'],
    queryFn: ({ signal }) => webchatVoiceApi.incomingSessions({ status: 'ringing', limit: 50 }, { signal }),
    refetchInterval: 4000,
    retry: false,
  })

  useEffect(() => {
    if (!selectedTicketId && incomingSessions.data?.items?.length) setSelectedTicketId(incomingSessions.data.items[0].ticket_id)
    else if (!selectedTicketId && conversations.data?.length) setSelectedTicketId(conversations.data[0].ticket_id)
  }, [conversations.data, incomingSessions.data, selectedTicketId])

  const selectedConversation = useMemo<WebchatConversation | undefined>(
    () => (conversations.data ?? []).find((item) => item.ticket_id === selectedTicketId),
    [conversations.data, selectedTicketId],
  )
  const selectedIncomingSession = useMemo<WebchatVoiceIncomingSession | undefined>(
    () => (incomingSessions.data?.items ?? []).find((item) => item.ticket_id === selectedTicketId),
    [incomingSessions.data?.items, selectedTicketId],
  )

  async function refreshAll() {
    await client.invalidateQueries({ queryKey: ['webchatConversations'] })
    await client.invalidateQueries({ queryKey: ['webchatVoiceIncomingSessions'] })
    if (selectedTicketId) {
      await client.invalidateQueries({ queryKey: ['webchatVoiceSessions', selectedTicketId] })
      await client.invalidateQueries({ queryKey: ['webchatThread', selectedTicketId] })
    }
    await client.invalidateQueries({ queryKey: ['webchatVoiceRuntimeConfig'] })
  }

  return (
    <main style={{ padding: 28, display: 'grid', gridTemplateColumns: 'minmax(280px, 420px) 1fr', gap: 20, background: '#f8fafc', minHeight: '100vh' }}>
      <section style={{ border: '1px solid #e5e7eb', borderRadius: 16, padding: 18, background: '#fff' }}>
        <h1 style={{ marginTop: 0 }}>WebCall Agent Console</h1>
        <p style={{ color: '#475467' }}>Select a WebChat ticket, monitor the operator incoming-call queue, and accept real browser voice calls through LiveKit.</p>
        <Button variant="secondary" disabled={conversations.isFetching} onClick={() => void refreshAll()}>{conversations.isFetching ? 'Refreshing...' : 'Refresh'}</Button>
        <section style={{ marginTop: 16, borderTop: '1px solid #e5e7eb', paddingTop: 14 }}>
          <h2 style={{ margin: '0 0 8px', fontSize: 16 }}>Incoming WebCall Queue</h2>
          {incomingSessions.isLoading ? <p data-testid="webcall-incoming-loading">Loading incoming WebCalls…</p> : null}
          {incomingSessions.isError ? <p data-testid="webcall-incoming-error">Unable to load incoming WebCalls.</p> : null}
          {!incomingSessions.isLoading && !incomingSessions.isError && !(incomingSessions.data?.items ?? []).length ? <p style={{ color: '#667085' }}>No ringing WebCalls.</p> : null}
          <div style={{ display: 'grid', gap: 8 }}>
            {(incomingSessions.data?.items ?? []).map((item) => (
              <button key={item.voice_session_id} style={{ textAlign: 'left', padding: 10, borderRadius: 10, border: selectedTicketId === item.ticket_id ? '2px solid #f97316' : '1px solid #fed7aa', background: '#fff7ed' }} onClick={() => setSelectedTicketId(item.ticket_id)}>
                <strong>{formatValue(item.ticket_no)} · Incoming WebCall</strong><br />
                <span>{formatValue(item.ticket_title)}</span><br />
                <small>{formatValue(incomingVisitorLabel(item))} · {formatValue(item.voice_session_id)}</small>
              </button>
            ))}
          </div>
        </section>
        {conversations.isLoading ? <p data-testid="webcall-console-loading">Loading conversations…</p> : null}
        {conversations.isError ? <p data-testid="webcall-console-error">Unable to load WebChat conversations. Refresh the page or sign in again.</p> : null}
        {!conversations.isLoading && !conversations.isError && !(conversations.data ?? []).length ? <EmptyState text="No WebChat conversations are available for WebCall monitoring." /> : null}
        <div style={{ display: 'grid', gap: 8, marginTop: 14 }}>
          {(conversations.data ?? []).map((item) => (
            <button key={item.conversation_id} style={{ textAlign: 'left', padding: 10, borderRadius: 10, border: selectedTicketId === item.ticket_id ? '2px solid #f97316' : '1px solid #e5e7eb', background: '#fff' }} onClick={() => setSelectedTicketId(item.ticket_id)}>
              <strong>{formatValue(item.ticket_no)}</strong><br />
              <span>{formatValue(item.title)}</span><br />
              <small>{formatValue(visitorLabel(item))}</small>
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
              <dt>Visitor</dt><dd>{formatValue(visitorLabel(selectedConversation))}</dd>
              <dt>Origin</dt><dd>{formatValue(selectedConversation.origin)}</dd>
              <dt>Page</dt><dd>{formatValue(selectedConversation.page_url)}</dd>
            </dl>
          ) : selectedIncomingSession ? (
            <dl style={{ display: 'grid', gridTemplateColumns: '140px 1fr', gap: 8 }}>
              <dt>Ticket</dt><dd>{formatValue(selectedIncomingSession.ticket_no)}</dd>
              <dt>Ticket ID</dt><dd>{selectedIncomingSession.ticket_id}</dd>
              <dt>Conversation</dt><dd>{formatValue(selectedIncomingSession.conversation_id)}</dd>
              <dt>Visitor</dt><dd>{formatValue(incomingVisitorLabel(selectedIncomingSession))}</dd>
              <dt>Origin</dt><dd>{formatValue(selectedIncomingSession.origin)}</dd>
              <dt>Page</dt><dd>{formatValue(selectedIncomingSession.page_url)}</dd>
            </dl>
          ) : <p>No WebChat conversation selected.</p>}
        </section>

        <AgentWebCallPanel
          ticketId={selectedTicketId}
          ticketNo={selectedConversation?.ticket_no || selectedIncomingSession?.ticket_no}
          conversationId={selectedConversation?.conversation_id || selectedIncomingSession?.conversation_id}
          visitorLabel={selectedConversation ? visitorLabel(selectedConversation) : incomingVisitorLabel(selectedIncomingSession)}
          onSelectTicket={setSelectedTicketId}
          onActivity={() => void refreshAll()}
        />
      </div>
    </main>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/webchat-voice',
  beforeLoad: () => { if (!getToken()) throw redirect({ to: '/login' }) },
  component: WebCallAgentConsolePage,
})

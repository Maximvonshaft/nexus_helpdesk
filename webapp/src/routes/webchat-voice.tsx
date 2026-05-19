import { useEffect, useMemo, useState } from 'react'
import { createRoute, redirect } from '@tanstack/react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { api, getToken } from '@/lib/api'
import type { WebchatConversation } from '@/lib/types'
import { AgentWebCallPanel } from '@/components/webcall/AgentWebCallPanel'

function formatValue(value?: string | number | null) {
  if (value === null || value === undefined || value === '') return '-'
  return String(value)
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

  useEffect(() => {
    if (!selectedTicketId && conversations.data?.length) setSelectedTicketId(conversations.data[0].ticket_id)
  }, [conversations.data, selectedTicketId])

  const selectedConversation = useMemo<WebchatConversation | undefined>(
    () => (conversations.data ?? []).find((item) => item.ticket_id === selectedTicketId),
    [conversations.data, selectedTicketId],
  )

  async function refreshAll() {
    await client.invalidateQueries({ queryKey: ['webchatConversations'] })
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
        <p style={{ color: '#475467' }}>Select a WebChat ticket, monitor incoming WebCall sessions, and accept real browser voice calls through LiveKit.</p>
        <button onClick={() => void refreshAll()}>Refresh</button>
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

        <AgentWebCallPanel ticketId={selectedTicketId} onActivity={() => void refreshAll()} />
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

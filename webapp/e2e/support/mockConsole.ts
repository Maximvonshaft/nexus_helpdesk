import type { Page, Route } from '@playwright/test'

export const TOKEN_KEY = 'helpdesk-webapp-token'

export type MockRole = 'agent' | 'lead' | 'ops' | 'admin'

export function authUser(kind: MockRole) {
  const base = {
    id: kind === 'admin' ? 1 : kind === 'ops' ? 3 : kind === 'lead' ? 4 : 2,
    username: kind,
    display_name: `${kind.toUpperCase()} User`,
    email: `${kind}@example.test`,
    role: kind === 'ops' ? 'manager' : kind,
  }
  const roleCaps: Record<MockRole, string[]> = {
    agent: ['ticket.read', 'customer_profile.read', 'outbound.draft.save', 'outbound.send', 'webchat.handoff.accept'],
    lead: ['ticket.read', 'ticket.assign', 'ticket.escalate', 'customer_profile.read', 'outbound.draft.save', 'outbound.send', 'webchat.handoff.accept', 'webchat.handoff.force_takeover'],
    ops: ['ticket.read', 'ticket.assign', 'customer_profile.read', 'outbound.draft.save', 'outbound.send', 'webcall.voice.queue.view', 'runtime.manage', 'channel_account.manage', 'ai_config.read', 'qa.manage'],
    admin: ['ticket.read', 'ticket.assign', 'ticket.escalate', 'customer_profile.read', 'outbound.draft.save', 'outbound.send', 'webchat.handoff.accept', 'webchat.handoff.force_takeover', 'webcall.voice.queue.view', 'webcall.voice.accept', 'webcall.voice.reject', 'webcall.voice.end', 'runtime.manage', 'channel_account.manage', 'ai_config.read', 'ai_config.manage', 'qa.manage', 'user.manage', 'security.read', 'audit.read'],
  }
  return { ...base, capabilities: roleCaps[kind] }
}

const ticket = {
  id: 101,
  ticket_no: 'NX-101',
  title: 'Customer asks for delivery status',
  status: 'open',
  priority: 'high',
  source_channel: 'webchat',
  category: 'delivery',
  sub_category: 'tracking',
  tracking_number: 'WB123456789CH',
  customer_name: 'Jane Cooper',
  assignee_name: 'Agent User',
  team_name: 'CH Support',
  market_id: 11,
  market_code: 'CH',
  country_code: 'CH',
  conversation_state: 'handoff_requested',
  updated_at: '2026-06-02T08:00:00Z',
  resolution_due_at: '2026-06-02T12:00:00Z',
  overdue: false,
}

export async function fulfillApi(route: Route, kind: MockRole) {
  const url = new URL(route.request().url())
  const path = url.pathname
  const method = route.request().method().toUpperCase()
  const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json; charset=utf-8', body: JSON.stringify(body) })

  if (path === '/api/auth/me') return json(authUser(kind))
  if (path === '/api/lookups/bulletins') return json([])
  if (path === '/api/lookups/markets') return json([{ id: 11, code: 'CH', name: 'Switzerland', country_code: 'CH', is_active: true }])
  if (path === '/api/lookups/teams') return json([{ id: 1, name: 'CH Support', team_type: 'support' }])
  if (path === '/api/admin/capabilities/catalog') return json(authUser('admin').capabilities)
  if (path === '/api/lite/meta') return json({ users: [authUser('agent'), authUser('lead')], teams: [{ id: 1, name: 'CH Support', team_type: 'support' }], statuses: ['open', 'pending', 'closed'], priorities: ['low', 'normal', 'high'] })
  if (path === '/api/lite/today-workbench') return json({ generated_at: '2026-06-02T08:00:00Z', role: authUser(kind).role, user_id: authUser(kind).id, capabilities: authUser(kind).capabilities, tasks: [], metrics: [], sla_priorities: [], interaction_states: [], command_center: [] })
  if (path === '/api/lite/cases') return json({ items: [ticket], next_cursor: null, has_more: false })
  if (path === '/api/email/queue') return json({ generated_at: '2026-06-02T08:00:00Z', source: 'mailbox_projection', items: [{ ...ticket, ticket_id: 101, queue_source: 'inbound_email', queue_reason: 'new inbound', direction: 'inbound', last_message_subject: 'Where is my parcel?', last_message_preview: 'Please check WB123456789CH' }], total: 1, filters: {} })
  if (path === '/api/email/mailbox-sync/status') return json({ generated_at: '2026-06-02T08:00:00Z', daemon_enabled: true, interval_seconds: 60, enabled_accounts: 1, configured_accounts: 1, pending_jobs: 0, dead_jobs: 0, accounts: [] })
  if (path === '/api/email/mailbox-sync/enqueue' && method === 'POST') return json({ ok: true, enqueued: 1, job_ids: [9] })
  if (path === '/api/admin/queues/summary') return json({ pending_outbound: 0, dead_outbound: 0, pending_jobs: 0, dead_jobs: 0, openclaw_links: 0 })
  if (path === '/api/admin/openclaw/runtime-health') return json({ stale_link_count: 0, pending_sync_jobs: 0, dead_sync_jobs: 0, pending_attachment_jobs: 0, dead_attachment_jobs: 0, warnings: [] })
  if (path === '/api/admin/production-readiness') return json({ app_env: 'development', database_url_scheme: 'sqlite', is_postgres: false, storage_backend: 'local', openclaw_transport: 'mcp', metrics_enabled: false, openclaw_sync_enabled: true, warnings: [] })
  if (path === '/api/admin/signoff-checklist') return json({ status: 'not_ready', checks: {}, warnings: [] })
  if (path === '/api/admin/channel-accounts') return json([])
  if (path === '/api/admin/outbound-email/accounts') return json([{ id: 7, display_name: 'Pilot SMTP', host: 'smtp.example.test', port: 587, username: 'support@example.test', from_address: 'support@example.test', reply_to: 'replies@example.test', security_mode: 'starttls', inbound_enabled: true, imap_password_configured: true, imap_password_mask: '********', market_id: null, is_active: true, priority: 10, health_status: 'ok', last_test_status: 'success', last_test_error: null, last_test_at: '2026-06-02T08:00:00Z', password_configured: true, password_mask: '********', created_at: '2026-06-02T07:00:00Z', updated_at: '2026-06-02T08:00:00Z' }])
  if (path === '/api/admin/outbound-email/accounts/7/test-send' && method === 'POST') return json({ ok: true, account_id: 7, provider_status: 'accepted', health_status: 'ok', sent_at: '2026-06-02T08:01:00Z' })
  if (path === '/api/tickets/101/summary') return json({ ...ticket, customer_request: 'Where is my parcel?', preferred_reply_channel: 'email', preferred_reply_contact: 'jane@example.test', destination_country: 'CH', ai_summary: 'Customer requests delivery status.', required_action: 'Check tracking and reply.', missing_fields: null, customer_update: 'Waiting for delivery scan.', customer: { name: 'Jane Cooper', phone: '+41790000000', email: 'jane@example.test' } })
  if (path === '/api/tickets/101/timeline') return json({ items: [{ id: 1, created_at: '2026-06-02T08:00:00Z', event_type: 'created' }], next_cursor: null, has_more: false })
  if (path === '/api/tickets/101/outbound/draft' && method === 'POST') return json({ ok: true, id: 501, status: 'draft' })
  if (path === '/api/tickets/101/outbound/send' && method === 'POST') return json({ ok: true, id: 502, status: 'sent' })
  if (path === '/api/webchat/admin/conversations') return json([{ conversation_id: 'conv-101', ticket_id: 101, ticket_no: 'NX-101', title: ticket.title, visitor_name: 'Jane Cooper', visitor_email: 'jane@example.test', visitor_phone: '+41790000000', origin: 'website', page_url: 'https://example.test', needs_human: true, ai_pending: false, ai_status: 'handoff_requested', ai_suspended: true, handoff_status: 'requested', current_handoff_request_id: 9001, status: 'open', unread_count: 1, updated_at: '2026-06-02T08:00:00Z' }])
  if (path === '/api/webchat/admin/handoff/queue') return json({ items: [{ id: 9001, ticket_id: 101, ticket_no: 'NX-101', title: ticket.title, visitor_name: 'Jane Cooper', visitor_email: 'jane@example.test', visitor_phone: '+41790000000', origin: 'website', status: 'requested', ai_status: 'handoff_requested', can_accept: true, can_decline: true, can_release: false, can_resume_ai: false, can_force_takeover: true }], total: 1 })
  if (path === '/api/webchat/admin/handoff/9001/accept' && method === 'POST') return json({ id: 9001, ticket_id: 101, status: 'accepted' })
  if (path === '/api/webchat/admin/handoff/9001/decline' && method === 'POST') return json({ id: 9001, ticket_id: 101, status: 'declined' })
  if (path === '/api/webchat/admin/tickets/101/thread') return json({ conversation_id: 'conv-101', visitor: { name: 'Jane Cooper', email: 'jane@example.test', phone: '+41790000000' }, handoff: { id: 9001, ticket_id: 101, status: 'requested', can_accept: true, can_decline: true, can_force_takeover: true }, messages: [{ id: 1, role: 'customer', body: 'Where is my parcel?', created_at: '2026-06-02T08:00:00Z' }], ai_turns: [] })
  if (path === '/api/webchat/admin/tickets/101/events') return json({ events: [{ id: 1, event_type: 'message.created' }], last_event_id: 1 })
  if (path === '/api/webchat/admin/tickets/101/reply' && method === 'POST') return json({ ok: true, message_id: 7001, ticket_id: 101, status: 'sent' })
  if (path === '/api/webchat/admin/voice/sessions') return json({ items: [{ voice_session_id: 'voice-101', ticket_id: 101, ticket_no: 'NX-101', ticket_title: ticket.title, visitor_label: 'Jane Cooper', origin: 'website', page_url: 'https://example.test', status: 'incoming' }] })
  if (path === '/api/webchat/admin/tickets/101/voice/sessions') return json({ items: [{ voice_session_id: 'voice-101', ticket_id: 101, status: 'incoming' }] })
  if (path === '/api/webchat/admin/tickets/101/voice/voice-101/evidence') return json({ items: [], transcript: [], summary: null })
  if (path === '/api/webchat/admin/tickets/101/voice/voice-101/actions') return json({ items: [] })
  if (path === '/api/webchat/admin/tickets/101/voice/voice-101/accept' && method === 'POST') return json({ voice_session_id: 'voice-101', ticket_id: 101, status: 'connected' })
  if (path === '/api/webchat/admin/tickets/101/voice/voice-101/reject' && method === 'POST') return json({ ok: true, status: 'rejected', voice_session_id: 'voice-101' })
  if (path === '/api/webchat/admin/tickets/101/voice/voice-101/end' && method === 'POST') return json({ ok: true, status: 'ended', voice_session_id: 'voice-101' })
  if (path === '/api/admin/security-audit') return json({ capability_catalog: authUser('admin').capabilities, users: [], recent_audit: [], summary: { total_users: 0, active_users: 0, inactive_users: 0, admin_users: 0, auditor_users: 0, high_risk_overrides: 0, recent_audit_24h: 0, catalog_size: authUser('admin').capabilities.length, read_only: true } })
  if (path === '/api/admin/users') return json([])
  if (path === '/api/admin/webcall-ai-demo/status') return json({ ok: true, status: 'ready', enabled: true, kill_switch: false, internal_only: true, public_customer_entry_enabled: false, recording_enabled: false, transcription_enabled: true, ai_agent_enabled: true, demo_mode: 'internal', allow_browser_speech: false, allow_real_media: false, active_demo_sessions: 0, max_active_sessions: 1, max_turns_per_session: 5, blockers: [], warnings: [] })

  return route.fulfill({ status: 404, contentType: 'application/json; charset=utf-8', body: JSON.stringify({ detail: `Unhandled mock for ${method} ${path}` }) })
}

export async function mockAuthenticatedConsole(page: Page, kind: MockRole) {
  await page.addInitScript(([storageKey, token]) => {
    window.sessionStorage.setItem(storageKey, token)
  }, [TOKEN_KEY, `${kind}-token`])
  await page.route('**/api/**', (route) => fulfillApi(route, kind))
}

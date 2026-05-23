import { createRoute, redirect, useNavigate } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken, type WebCallAIDemoEvent, type WebCallAIDemoSession, type WebCallAIDemoTurn } from '@/lib/api'
import { canViewOps } from '@/lib/access'
import { sanitizeDisplayText, formatDateTime } from '@/lib/format'
import { useSession } from '@/hooks/useAuth'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { Field } from '@/components/ui/Field'
import { PageHeader } from '@/components/ui/PageHeader'
import { Toast } from '@/components/ui/Toast'

type SpeechRecognitionCtor = new () => {
  lang: string
  interimResults: boolean
  maxAlternatives: number
  start: () => void
  onresult: ((event: { results: ArrayLike<{ 0: { transcript: string } }> }) => void) | null
  onerror: (() => void) | null
}

declare global {
  interface Window {
    SpeechRecognition?: SpeechRecognitionCtor
    webkitSpeechRecognition?: SpeechRecognitionCtor
  }
}

function statusTone(status?: string) {
  if (status === 'ready' || status === 'active') return 'success'
  if (status === 'blocked' || status === 'ended') return 'warning'
  if (status === 'disabled') return 'default'
  return 'default'
}

function eventLabel(event: WebCallAIDemoEvent) {
  return `${sanitizeDisplayText(event.type)} · ${formatDateTime(event.created_at)}`
}

function WebCallAIDemoPage() {
  const session = useSession()
  const navigate = useNavigate()
  const client = useQueryClient()
  const permitted = canViewOps(session.data)
  const [toast, setToast] = useState<{ message: string; tone?: 'default' | 'danger' | 'success' } | null>(null)
  const [demoSession, setDemoSession] = useState<WebCallAIDemoSession | null>(null)
  const [text, setText] = useState('Where is my parcel?')
  const [events, setEvents] = useState<WebCallAIDemoEvent[]>([])
  const [turns, setTurns] = useState<Array<WebCallAIDemoTurn | { turn_index: number; customer_text_redacted: string; ai_response_text_redacted: string; handoff_required: boolean; created_at?: string | null }>>([])
  const speechSupported = useMemo(() => typeof window !== 'undefined' && Boolean(window.SpeechRecognition || window.webkitSpeechRecognition), [])
  const ttsSupported = useMemo(() => typeof window !== 'undefined' && 'speechSynthesis' in window, [])

  const status = useQuery({ queryKey: ['webcallAIDemoStatus'], queryFn: api.webcallAIDemoStatus, enabled: permitted })
  const controlsEnabled = Boolean(permitted && status.data?.status === 'ready')

  const createSession = useMutation({
    mutationFn: () => api.webcallAIDemoCreateSession({ locale: 'en', display_name: 'Internal Demo', scenario: 'tracking_question' }),
    onSuccess: (data) => {
      setDemoSession(data.session)
      setEvents(data.events)
      setTurns([])
      setToast({ message: 'Demo session created', tone: 'success' })
      client.invalidateQueries({ queryKey: ['webcallAIDemoStatus'] })
    },
    onError: (err: Error) => setToast({ message: err.message, tone: 'danger' }),
  })

  const sendTurn = useMutation({
    mutationFn: async (inputMode: 'typed' | 'browser_speech') => {
      if (!demoSession) throw new Error('Create a demo session first')
      return api.webcallAIDemoTurn(demoSession.public_id, {
        client_turn_id: `ui-${Date.now()}`,
        input_mode: inputMode,
        locale: demoSession.locale || 'en',
        text,
        browser_speech_supported: speechSupported,
      })
    },
    onSuccess: (data) => {
      setTurns((prev) => [...prev, data.turn])
      setEvents((prev) => [...prev, ...data.events])
      if (data.turn.ai_response_text_redacted && ttsSupported && status.data?.allow_browser_speech) {
        window.speechSynthesis.cancel()
        window.speechSynthesis.speak(new SpeechSynthesisUtterance(data.turn.ai_response_text_redacted))
      }
    },
    onError: (err: Error) => setToast({ message: err.message, tone: 'danger' }),
  })

  const endSession = useMutation({
    mutationFn: async () => {
      if (!demoSession) throw new Error('No active demo session')
      return api.webcallAIDemoEndSession(demoSession.public_id)
    },
    onSuccess: (data) => {
      setDemoSession(data.session)
      setEvents((prev) => [...prev, { type: 'ended', summary: 'operator_end', created_at: data.session.ended_at }])
      setToast({ message: 'Demo session ended', tone: 'success' })
      client.invalidateQueries({ queryKey: ['webcallAIDemoStatus'] })
    },
    onError: (err: Error) => setToast({ message: err.message, tone: 'danger' }),
  })

  const refreshEvents = useMutation({
    mutationFn: async () => {
      if (!demoSession) throw new Error('No demo session selected')
      return api.webcallAIDemoEvents(demoSession.public_id)
    },
    onSuccess: (data) => {
      setEvents(data.events)
      setTurns(data.turns)
    },
    onError: (err: Error) => setToast({ message: err.message, tone: 'danger' }),
  })

  if (session.data && !permitted) navigate({ to: '/' })

  return (
    <AppShell>
      <PageHeader
        eyebrow="Internal Demo"
        title="WebCall AI Demo Sandbox"
        description="Admin-only text-first demo surface for WebCall AI readiness, safe turns, browser speech fallback, and durable evidence."
      />

      {!permitted ? <Card><CardHeader title="No access" subtitle="This internal demo is limited to runtime/admin operators." /></Card> : null}

      {permitted ? (
        <>
          <div className="metrics-grid">
            <Card className="metric"><div className="metric-label">Demo Status</div><div className="metric-value"><Badge tone={statusTone(status.data?.status)}>{status.data?.status || 'loading'}</Badge></div></Card>
            <Card className="metric"><div className="metric-label">Kill Switch</div><div className="metric-value">{status.data?.kill_switch ? 'on' : 'off'}</div></Card>
            <Card className="metric"><div className="metric-label">Active Demo Sessions</div><div className="metric-value">{status.data?.active_demo_sessions ?? '—'} / {status.data?.max_active_sessions ?? '—'}</div></Card>
            <Card className="metric"><div className="metric-label">Browser Speech</div><div className="metric-value">{speechSupported ? 'available' : 'typed fallback'}</div></Card>
          </div>

          <div className="page-grid split-grid">
            <Card>
              <CardHeader title="Runtime Status" subtitle="Controls stay disabled unless the backend reports ready." />
              <CardBody>
                <div className="detail-grid">
                  <div><span>Enabled</span><strong>{String(status.data?.enabled ?? false)}</strong></div>
                  <div><span>Internal Only</span><strong>{String(status.data?.internal_only ?? true)}</strong></div>
                  <div><span>Public Voice Entry</span><strong>{String(status.data?.public_customer_entry_enabled ?? false)}</strong></div>
                  <div><span>Recording</span><strong>{String(status.data?.recording_enabled ?? false)}</strong></div>
                  <div><span>Transcription</span><strong>{String(status.data?.transcription_enabled ?? false)}</strong></div>
                  <div><span>AI Agent Flag</span><strong>{String(status.data?.ai_agent_enabled ?? false)}</strong></div>
                  <div><span>Mode</span><strong>{sanitizeDisplayText(status.data?.demo_mode)}</strong></div>
                  <div><span>Real Media</span><strong>{String(status.data?.allow_real_media ?? false)}</strong></div>
                </div>
                {[...(status.data?.blockers ?? []), ...(status.data?.warnings ?? [])].map((item) => <div key={item} className="message warning">{sanitizeDisplayText(item)}</div>)}
              </CardBody>
            </Card>

            <Card>
              <CardHeader title="Demo Controls" subtitle="Typed input is always available; microphone and playback use browser APIs only." />
              <CardBody>
                <div className="button-row">
                  <Button disabled={!controlsEnabled || createSession.isPending} onClick={() => createSession.mutate()}>{createSession.isPending ? 'Creating…' : 'Create demo session'}</Button>
                  <Button variant="secondary" disabled={!controlsEnabled || !demoSession || demoSession.status === 'ended' || sendTurn.isPending} onClick={() => sendTurn.mutate('typed')}>{sendTurn.isPending ? 'Thinking…' : 'Send typed turn'}</Button>
                  <Button variant="secondary" disabled={!controlsEnabled || !demoSession || !speechSupported || !status.data?.allow_browser_speech} onClick={() => {
                    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition
                    if (!Recognition) return
                    const recognition = new Recognition()
                    recognition.lang = demoSession?.locale || 'en-US'
                    recognition.interimResults = false
                    recognition.maxAlternatives = 1
                    recognition.onresult = (event) => {
                      const transcript = event.results[0]?.[0]?.transcript || ''
                      setText(transcript)
                    }
                    recognition.onerror = () => setToast({ message: 'Browser speech recognition unavailable; typed fallback remains available.', tone: 'danger' })
                    recognition.start()
                  }}>Mic</Button>
                  <Button variant="danger" disabled={!demoSession || demoSession.status === 'ended' || endSession.isPending} onClick={() => endSession.mutate()}>{endSession.isPending ? 'Ending…' : 'End session'}</Button>
                </div>
                <Field label="Demo input">
                  <textarea className="textarea" value={text} onChange={(event) => setText(event.target.value)} rows={4} />
                </Field>
                {!speechSupported ? <div className="section-subtitle">Browser speech recognition is not supported here; typed fallback is active.</div> : null}
                {!ttsSupported ? <div className="section-subtitle">Browser speech synthesis is not supported here; text reply fallback is active.</div> : null}
              </CardBody>
            </Card>

            <Card>
              <CardHeader title="Session" subtitle={demoSession ? demoSession.public_id : 'No demo session selected'} />
              <CardBody>
                <div className="detail-grid">
                  <div><span>Status</span><strong>{demoSession?.status || '—'}</strong></div>
                  <div><span>Mode</span><strong>{demoSession?.mode || '—'}</strong></div>
                  <div><span>Turns</span><strong>{demoSession?.ai_turn_count ?? turns.length}</strong></div>
                  <div><span>Agent</span><strong>{demoSession?.ai_agent_status || '—'}</strong></div>
                </div>
                <Button variant="secondary" disabled={!demoSession || refreshEvents.isPending} onClick={() => refreshEvents.mutate()}>{refreshEvents.isPending ? 'Refreshing…' : 'Refresh evidence timeline'}</Button>
              </CardBody>
            </Card>

            <Card>
              <CardHeader title="Event Timeline" subtitle="Safe redacted events and AI turns only." />
              <CardBody>
                <div className="timeline-list">
                  {events.map((event, index) => <div key={`${event.type}-${event.created_at}-${index}`}><Badge>{sanitizeDisplayText(event.type)}</Badge><span>{eventLabel(event)}</span></div>)}
                  {events.length === 0 ? <div className="section-subtitle">No events yet.</div> : null}
                </div>
                {turns.map((turn) => (
                  <div key={`${turn.turn_index}-${turn.created_at}`} className="message">
                    <strong>Turn {turn.turn_index}</strong>
                    <div>{sanitizeDisplayText(turn.customer_text_redacted)}</div>
                    <div>{sanitizeDisplayText(turn.ai_response_text_redacted)}</div>
                    {turn.handoff_required ? <Badge tone="warning">handoff required</Badge> : null}
                  </div>
                ))}
              </CardBody>
            </Card>
          </div>
        </>
      ) : null}
      {toast ? <Toast message={toast.message} tone={toast.tone} onClose={() => setToast(null)} /> : null}
    </AppShell>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/webcall-ai-demo',
  beforeLoad: () => { if (!getToken()) throw redirect({ to: '/login' }) },
  component: WebCallAIDemoPage,
})

import { useEffect, useRef, useState } from 'react'
import { getToken, normalizeApiBaseUrl } from '@/lib/api'
import type { WebchatHandoffQueue, WebchatHandoffRequest, WebchatMessage } from '@/lib/types'

export type WebchatRealtimeStatus = 'disabled' | 'connecting' | 'connected' | 'fallback'

export type WebchatRealtimeEvent = {
  type: string
  event_id?: number
  conversation_id?: string | null
  ticket_id?: number | null
  view?: string
  message?: WebchatMessage
  handoff?: WebchatHandoffRequest
  data?: WebchatHandoffQueue
  payload?: Record<string, unknown>
}

type UseWebchatRealtimeOptions = {
  enabled: boolean
  selectedTicketId?: number | null
  handoffView: 'requested' | 'ai_active' | 'mine'
  onEvent: (event: WebchatRealtimeEvent) => void
}

function realtimeEnabledByEnv() {
  return String(import.meta.env.VITE_WEBCHAT_WS_ENABLED ?? 'true').toLowerCase() !== 'false'
}

function websocketUrl() {
  const configured = normalizeApiBaseUrl(import.meta.env.VITE_API_BASE_URL)
  const base = configured || (typeof window !== 'undefined' ? window.location.origin : 'http://localhost')
  const url = new URL('/api/webchat/ws', base)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  return url.toString()
}

export function useWebchatRealtime({ enabled, selectedTicketId, handoffView, onEvent }: UseWebchatRealtimeOptions) {
  const [status, setStatus] = useState<WebchatRealtimeStatus>('disabled')
  const [lastEventId, setLastEventId] = useState(0)
  const lastEventIdRef = useRef(0)
  const onEventRef = useRef(onEvent)
  const reconnectFailures = useRef(0)
  const stopped = useRef(false)

  useEffect(() => {
    onEventRef.current = onEvent
  }, [onEvent])

  useEffect(() => {
    stopped.current = false
    const token = getToken()
    if (!enabled || !realtimeEnabledByEnv() || !token || typeof WebSocket === 'undefined') {
      setStatus('disabled')
      return () => { stopped.current = true }
    }

    let ws: WebSocket | null = null
    let reconnectTimer: number | null = null
    let heartbeatTimer: number | null = null

    const cleanupTimers = () => {
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer)
      if (heartbeatTimer !== null) window.clearInterval(heartbeatTimer)
      reconnectTimer = null
      heartbeatTimer = null
    }

    const send = (payload: Record<string, unknown>) => {
      if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(payload))
    }

    const connect = () => {
      cleanupTimers()
      setStatus('connecting')
      ws = new WebSocket(websocketUrl())
      ws.onopen = () => {
        send({ type: 'connection.hello', client_type: 'agent', access_token: token })
        send({ type: 'subscribe.handoff_queue', view: handoffView, last_event_id: lastEventIdRef.current })
        if (selectedTicketId) send({ type: 'subscribe.conversation', ticket_id: selectedTicketId, last_event_id: lastEventIdRef.current })
        heartbeatTimer = window.setInterval(() => send({ type: 'ping' }), 25000)
      }
      ws.onmessage = (message) => {
        let event: WebchatRealtimeEvent
        try {
          event = JSON.parse(String(message.data || '{}')) as WebchatRealtimeEvent
        } catch {
          return
        }
        if (event.type === 'connection.ready' || event.type === 'subscription.ready' || event.type === 'pong') {
          if (event.type === 'connection.ready') {
            reconnectFailures.current = 0
            setStatus('connected')
          }
          return
        }
        if (event.type === 'error') {
          setStatus('fallback')
          return
        }
        if (typeof event.event_id === 'number') {
          const nextEventId = Math.max(lastEventIdRef.current, event.event_id || 0)
          lastEventIdRef.current = nextEventId
          setLastEventId(nextEventId)
        }
        onEventRef.current(event)
      }
      ws.onerror = () => setStatus('fallback')
      ws.onclose = () => {
        cleanupTimers()
        if (stopped.current) return
        setStatus('fallback')
        reconnectFailures.current = Math.min(reconnectFailures.current + 1, 5)
        reconnectTimer = window.setTimeout(connect, Math.min(30000, 1000 * 2 ** reconnectFailures.current))
      }
    }

    connect()
    return () => {
      stopped.current = true
      cleanupTimers()
      if (ws && ws.readyState < WebSocket.CLOSING) ws.close(1000, 'component_unmounted')
    }
  }, [enabled, handoffView, selectedTicketId])

  return {
    status,
    connected: status === 'connected',
    fallback: status === 'fallback',
    lastEventId,
  }
}

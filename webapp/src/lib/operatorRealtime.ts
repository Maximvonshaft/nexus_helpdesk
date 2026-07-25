import { useEffect, useRef, useState } from 'react'
import { buildApiWebSocketUrl, getSupportToken } from '@/lib/apiClient'

export type OperatorRealtimeStatus = 'disabled' | 'connecting' | 'live' | 'fallback'

export function useOperatorRealtime({
  enabled,
  ticketId,
  conversationId,
  lastEventId,
  onEvent,
}: {
  enabled: boolean
  ticketId?: number | null
  conversationId?: string | null
  lastEventId: number
  onEvent: () => void | Promise<void>
}) {
  const [status, setStatus] = useState<OperatorRealtimeStatus>(enabled ? 'connecting' : 'disabled')
  const callbackRef = useRef(onEvent)
  const lastEventIdRef = useRef(lastEventId)
  callbackRef.current = onEvent
  lastEventIdRef.current = lastEventId

  useEffect(() => {
    if (!enabled || (!ticketId && !conversationId)) {
      setStatus('disabled')
      return undefined
    }
    const token = getSupportToken()
    if (!token || typeof WebSocket === 'undefined') {
      setStatus('fallback')
      return undefined
    }

    let stopped = false
    let socket: WebSocket | null = null
    let reconnectTimer: number | undefined
    let heartbeatTimer: number | undefined
    let failureCount = 0

    const stopTimers = () => {
      if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer)
      if (heartbeatTimer !== undefined) window.clearInterval(heartbeatTimer)
      reconnectTimer = undefined
      heartbeatTimer = undefined
    }

    const scheduleReconnect = () => {
      if (stopped) return
      setStatus('fallback')
      failureCount += 1
      const delay = Math.min(30_000, 1_000 * (2 ** Math.min(failureCount - 1, 5)))
      reconnectTimer = window.setTimeout(connect, delay)
    }

    const connect = () => {
      if (stopped) return
      stopTimers()
      setStatus(failureCount ? 'fallback' : 'connecting')
      try {
        socket = new WebSocket(buildApiWebSocketUrl('/api/webchat/ws'))
      } catch {
        scheduleReconnect()
        return
      }

      socket.addEventListener('open', () => {
        if (!socket || stopped) return
        socket.send(JSON.stringify({
          type: 'connection.hello',
          client_type: 'agent',
          access_token: token,
          ticket_id: ticketId || undefined,
          conversation_id: conversationId || undefined,
          last_event_id: Math.max(0, Number(lastEventIdRef.current || 0)),
        }))
        heartbeatTimer = window.setInterval(() => {
          if (socket?.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({ type: 'ping', request_id: `operator-${Date.now().toString(36)}` }))
          }
        }, 30_000)
      })

      socket.addEventListener('message', (event) => {
        let payload: Record<string, unknown>
        try {
          payload = JSON.parse(String(event.data)) as Record<string, unknown>
        } catch {
          return
        }
        const type = String(payload.type || '')
        if (type === 'connection.ready' || type === 'subscription.ready') {
          failureCount = 0
          setStatus('live')
          return
        }
        if (type === 'pong') return
        if (type === 'error') {
          if (payload.retryable === false) socket?.close()
          return
        }
        if (type) void callbackRef.current()
      })

      socket.addEventListener('error', () => {
        socket?.close()
      })
      socket.addEventListener('close', scheduleReconnect)
    }

    connect()
    return () => {
      stopped = true
      stopTimers()
      socket?.close()
    }
  }, [conversationId, enabled, ticketId])

  return status
}

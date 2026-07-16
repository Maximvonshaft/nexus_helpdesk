import type { OperatorWorkspaceThread } from '@/lib/operatorWorkspaceApi'
import type { WebchatMessage } from '@/lib/types'

export function initialWorkspaceQueueId() {
  if (typeof window === 'undefined') return null
  return new URLSearchParams(window.location.search).get('queue')
}

export function initialWorkspaceSessionKey() {
  if (typeof window === 'undefined') return null
  return new URLSearchParams(window.location.search).get('session')
}

function mergeMessages(...groups: WebchatMessage[][]) {
  const byId = new Map<string, WebchatMessage>()
  groups.flat().forEach((message) => byId.set(String(message.id), message))
  return [...byId.values()].sort((left, right) => Number(left.id) - Number(right.id))
}

export function mergeLatestWorkspaceThread(current: OperatorWorkspaceThread | undefined, latest: OperatorWorkspaceThread) {
  if (!current?.history_expanded) return { ...latest, history_expanded: false }
  return {
    ...latest,
    messages: mergeMessages(current.messages, latest.messages),
    message_page: current.message_page,
    history_expanded: true,
  }
}

export function mergeOlderWorkspaceThread(current: OperatorWorkspaceThread | undefined, older: OperatorWorkspaceThread) {
  if (!current) return { ...older, history_expanded: true }
  return {
    ...current,
    messages: mergeMessages(older.messages, current.messages),
    message_page: older.message_page,
    history_expanded: true,
  }
}

export function cancelPreviewFingerprint(ticketId: number | null, waybill: string, caller: string, reasonCode: string) {
  return JSON.stringify({
    ticketId,
    waybill: waybill.trim().toUpperCase(),
    caller: caller.trim(),
    reasonCode: reasonCode.trim(),
  })
}

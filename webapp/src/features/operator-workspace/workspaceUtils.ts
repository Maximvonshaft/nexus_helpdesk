import type { WebchatMessage, WebchatThread } from '@/lib/types'

export function errorCopy(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback
}

export function hasCapability(capabilities: Set<string>, ...values: string[]) {
  return values.some((value) => capabilities.has(value))
}

export function safeRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? value as Record<string, unknown> : {}
}

export function textValue(value: unknown) {
  return typeof value === 'string' ? value : ''
}

export function latestCustomerMessage(thread?: WebchatThread | null) {
  return [...(thread?.messages ?? [])].reverse().find((message) => message.direction === 'visitor' || message.direction === 'customer')
}

export function messageAuthorLabel(message: WebchatMessage) {
  if (message.direction === 'visitor' || message.direction === 'customer') return '客户'
  if (message.direction === 'agent' || message.direction === 'human') return '客服'
  if (message.direction === 'ai') return '历史自动回复'
  return '系统记录'
}

export function isOutboundMessage(message: WebchatMessage) {
  return message.direction === 'agent' || message.direction === 'ai'
}

export function reducedMotionPreferred() {
  return typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

import type { OutboundChannelCapability } from '@/lib/types'

const HIDDEN_REPLY_STATUSES = new Set(['not_ready', 'experimental_not_ready', 'not_customer_sendable'])

export function isCustomerSendableReplyChannel(channel: OutboundChannelCapability | null | undefined) {
  if (!channel) return false
  if (HIDDEN_REPLY_STATUSES.has(channel.status)) return false
  return Boolean(channel.customer_sendable && channel.enabled && channel.supports_send)
}

export function replyPanelVisibleChannels(channels: OutboundChannelCapability[] | null | undefined) {
  return (channels ?? []).filter(isCustomerSendableReplyChannel)
}

export function findReplyChannelCapability(channels: OutboundChannelCapability[] | null | undefined, channel: string) {
  const normalized = channel.trim().toLowerCase()
  return (channels ?? []).find((item) => item.channel === normalized) ?? null
}

export function outboundChannelMissingText(channel: OutboundChannelCapability | null | undefined) {
  if (!channel?.missing?.length) return ''
  return channel.missing.join(', ')
}

import { Box, Stack, Typography } from '@mui/material'
import type { ReactNode } from 'react'
import { operatorTonePalettePath } from '@/app/OperatorPresentation'
import type { BadgeTone, WebchatMessage } from '@/lib/types'

export type WorkspacePresentation = { label: string; detail?: string; tone: BadgeTone }

export function hasWorkspaceCapability(capabilities: Set<string>, ...values: string[]) {
  return values.some((value) => capabilities.has(value))
}

export function safeWorkspaceRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

export function workspaceText(value: unknown) {
  return typeof value === 'string' ? value : ''
}

export function workspaceNumber(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

export function workspaceDirectionLabel(direction: string) {
  if (direction === 'visitor' || direction === 'customer') return '客户'
  if (direction === 'agent' || direction === 'human') return '客服'
  if (direction === 'ai') return '自动回复'
  return '系统'
}

export function isOutboundWorkspaceMessage(message: WebchatMessage) {
  return message.direction === 'agent' || message.direction === 'ai'
}

export function WorkspaceStatusLine({ presentation, compact = false }: { presentation: WorkspacePresentation; compact?: boolean }) {
  return (
    <Stack direction="row" spacing={0.75} alignItems="flex-start" sx={{ minWidth: 0 }}>
      <Box aria-hidden="true" sx={{ bgcolor: operatorTonePalettePath(presentation.tone), borderRadius: '50%', flex: '0 0 auto', height: 8, mt: '6px', width: 8 }} />
      <Box sx={{ minWidth: 0 }}>
        <Typography variant={compact ? 'caption' : 'body2'} color="text.primary" sx={{ fontWeight: 650 }}>{presentation.label}</Typography>
        {!compact && presentation.detail ? <Typography variant="caption" color="text.secondary" display="block">{presentation.detail}</Typography> : null}
      </Box>
    </Stack>
  )
}

export function WorkspaceSectionHeading({ title, action, id }: { title: string; action?: ReactNode; id?: string }) {
  return (
    <Stack direction="row" spacing={2} alignItems="flex-start" justifyContent="space-between">
      <Typography id={id} component="h2" variant="h3">{title}</Typography>
      {action}
    </Stack>
  )
}

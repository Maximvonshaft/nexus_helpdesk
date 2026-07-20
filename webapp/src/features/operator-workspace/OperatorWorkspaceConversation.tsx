import { Box, Button, CircularProgress, Divider, Stack, TextField, Typography } from '@mui/material'
import type { SxProps, Theme } from '@mui/material/styles'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import {
  OperatorEmptyState,
  OperatorErrorNotice,
  OperatorLoadingState,
  OperatorSectionHeading,
  OperatorStatusLine,
  operatorScrollBehavior,
} from '@/app/OperatorPresentation'
import { agentRoutingApi } from '@/lib/agentRoutingApi'
import { operatorWorkspaceApi } from '@/lib/operatorWorkspaceApi'
import type { OperatorWorkspaceThread } from '@/lib/operatorWorkspaceApi'
import type { UnifiedOperatorQueueItem } from '@/lib/operatorWorkspaceTypes'
import {
  isOutboundWorkspaceMessage,
  messageDeliveryPresentation,
  workspaceDirectionLabel,
} from '@/lib/operatorWorkspacePresentation'
import { formatDateTime, sanitizeDisplayText } from '@/lib/format'
import { hasWorkspaceCapability } from './operatorWorkspaceState'

export function OperatorWorkspaceConversation({
  item,
  thread,
  isLoading,
  isRefreshing,
  error,
  historyError,
  isLoadingOlderMessages,
  capabilities,
  onRefresh,
  onLoadOlderMessages,
  onReplyDirtyChange,
  selectionUnavailable,
  sx,
}: {
  item: UnifiedOperatorQueueItem
  thread: OperatorWorkspaceThread | null
  isLoading: boolean
  isRefreshing: boolean
  error: unknown
  historyError: unknown
  isLoadingOlderMessages: boolean
  capabilities: Set<string>
  onRefresh: () => Promise<void>
  onLoadOlderMessages: () => Promise<void>
  onReplyDirtyChange: (dirty: boolean) => void
  selectionUnavailable: boolean
  sx?: SxProps<Theme>
}) {
  const [reply, setReply] = useState('')
  const [nearBottom, setNearBottom] = useState(true)
  const [newMessageCount, setNewMessageCount] = useState(0)
  const messagesRef = useRef<HTMLDivElement | null>(null)
  const previousLatestMessageIdRef = useRef<string | number | undefined>(thread?.messages.at(-1)?.id)
  const ticketReplyAllowed = Boolean(
    item.ticket_id
    && thread
    && !selectionUnavailable
    && hasWorkspaceCapability(capabilities, 'outbound.send', 'webchat.handoff.accept'),
  )
  const conversationReplyAllowed = Boolean(
    !item.ticket_id
    && thread?.conversation_id
    && thread.handoff?.can_reply
    && !selectionUnavailable
    && hasWorkspaceCapability(capabilities, 'webchat.handoff.accept'),
  )
  const canReply = ticketReplyAllowed || conversationReplyAllowed

  useEffect(() => {
    setReply('')
    setNearBottom(true)
    setNewMessageCount(0)
    previousLatestMessageIdRef.current = undefined
  }, [item.queue_id])

  useLayoutEffect(() => {
    const messages = thread?.messages ?? []
    const currentLatestMessageId = messages.at(-1)?.id
    const previousLatestMessageId = previousLatestMessageIdRef.current
    const previousLatestNumeric = Number(previousLatestMessageId)
    const added = currentLatestMessageId !== previousLatestMessageId
      ? Number.isFinite(previousLatestNumeric)
        ? messages.filter((message) => Number(message.id) > previousLatestNumeric).length
        : messages.length
      : 0
    previousLatestMessageIdRef.current = currentLatestMessageId
    if (!added) return
    const list = messagesRef.current
    if (list && nearBottom) {
      list.scrollTo({ top: list.scrollHeight, behavior: operatorScrollBehavior() })
      setNewMessageCount(0)
    } else {
      setNewMessageCount((count) => count + added)
    }
  }, [nearBottom, thread?.messages])

  useEffect(() => onReplyDirtyChange(Boolean(reply.trim())), [onReplyDirtyChange, reply])
  useEffect(() => () => onReplyDirtyChange(false), [onReplyDirtyChange])

  const replyMutation = useMutation({
    mutationFn: () => {
      if (item.ticket_id) return operatorWorkspaceApi.reply(item.ticket_id, reply.trim())
      if (!thread?.conversation_id) throw new Error('当前会话编号不可用')
      return agentRoutingApi.reply(thread.conversation_id, reply.trim())
    },
    onSuccess: async () => {
      setReply('')
      onReplyDirtyChange(false)
      await onRefresh()
    },
  })

  const loadOlderMessagesPreservingPosition = async () => {
    const list = messagesRef.current
    const previousHeight = list?.scrollHeight ?? 0
    const previousTop = list?.scrollTop ?? 0
    await onLoadOlderMessages()
    window.requestAnimationFrame(() => {
      if (!list || !previousHeight) return
      list.scrollTop = previousTop + Math.max(0, list.scrollHeight - previousHeight)
    })
  }

  const scrollToLatest = () => {
    messagesRef.current?.scrollTo({ top: messagesRef.current.scrollHeight, behavior: operatorScrollBehavior() })
    setNewMessageCount(0)
  }

  return (
    <Box id="workspace-conversation" component="section" aria-labelledby="operator-conversation-title" tabIndex={-1} sx={sx}>
      <OperatorSectionHeading id="operator-conversation-title" title="客户沟通" action={isRefreshing ? <CircularProgress size={18} aria-label="刷新中" /> : null} />
      <Divider sx={{ my: 2 }} />
      {isLoading ? <OperatorLoadingState label="正在读取消息…" /> : null}
      {error ? <OperatorErrorNotice title="无法读取客户沟通" error={error} fallback="仍可查看任务摘要" /> : null}
      {historyError ? <OperatorErrorNotice title="更早消息加载失败" error={historyError} fallback="可稍后重试" /> : null}
      {thread ? (
        <Stack spacing={1.5}>
          <Stack
            ref={messagesRef}
            aria-live="polite"
            spacing={1.25}
            sx={{ maxHeight: 520, overflowY: 'auto', pr: 0.5 }}
            onScroll={(event) => {
              const target = event.currentTarget
              const value = target.scrollHeight - target.scrollTop - target.clientHeight < 80
              setNearBottom(value)
              if (value) setNewMessageCount(0)
            }}
          >
            {thread.message_page?.has_more ? (
              <Button
                color="inherit"
                variant="outlined"
                disabled={isLoadingOlderMessages}
                startIcon={isLoadingOlderMessages ? <CircularProgress color="inherit" size={16} /> : undefined}
                onClick={() => void loadOlderMessagesPreservingPosition()}
                sx={{ alignSelf: 'center' }}
              >
                {isLoadingOlderMessages ? '加载中…' : '加载更早消息'}
              </Button>
            ) : null}
            {thread.messages.map((message) => {
              const delivery = messageDeliveryPresentation(message.delivery_status)
              const outbound = isOutboundWorkspaceMessage(message)
              return (
                <Box
                  component="article"
                  key={message.id}
                  sx={{
                    alignSelf: outbound ? 'flex-end' : 'flex-start',
                    bgcolor: outbound ? 'action.selected' : 'background.default',
                    borderRadius: 1.5,
                    maxWidth: '88%',
                    px: 1.5,
                    py: 1.25,
                  }}
                >
                  <Stack direction="row" spacing={2} sx={{ justifyContent: 'space-between' }}>
                    <Typography variant="subtitle2">{sanitizeDisplayText(message.author_label || workspaceDirectionLabel(message.direction))}</Typography>
                    {message.created_at ? <Typography component="time" variant="caption" color="text.disabled">{formatDateTime(message.created_at)}</Typography> : null}
                  </Stack>
                  <Typography variant="body2" sx={{ mt: 0.75, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{sanitizeDisplayText(message.body_text || message.body)}</Typography>
                  {outbound ? <Stack direction="row" spacing={1} sx={{ alignItems: 'center', mt: 1 }} aria-label="送达状态"><OperatorStatusLine presentation={delivery} compact /></Stack> : null}
                </Box>
              )
            })}
            {!thread.messages.length ? <OperatorEmptyState title="暂无消息" /> : null}
          </Stack>
          {newMessageCount ? <Button color="inherit" variant="outlined" onClick={scrollToLatest}>{newMessageCount} 条新消息，查看最新</Button> : null}
          {replyMutation.isError ? <OperatorErrorNotice title="发送失败" error={replyMutation.error} fallback="请稍后重试" /> : null}
          <Box component="form" onSubmit={(event) => { event.preventDefault(); if (canReply && reply.trim()) replyMutation.mutate() }}>
            <Stack spacing={1.25}>
              <TextField label="回复客户" helperText={canReply ? '当前会话由您接管，回复会直接进入会话记录。' : '接受人工会话后才能回复。'} value={reply} onChange={(event) => setReply(event.target.value)} multiline minRows={4} placeholder="输入回复" autoComplete="off" disabled={!canReply} />
              <Button type="submit" variant="contained" disabled={!canReply || !reply.trim() || replyMutation.isPending} startIcon={replyMutation.isPending ? <CircularProgress color="inherit" size={16} /> : undefined} sx={{ alignSelf: 'flex-end' }}>{replyMutation.isPending ? '发送中…' : '发送回复'}</Button>
            </Stack>
          </Box>
        </Stack>
      ) : !isLoading ? <OperatorEmptyState title="暂无客户沟通" description="回复和接手处理暂不可用" /> : null}
    </Box>
  )
}

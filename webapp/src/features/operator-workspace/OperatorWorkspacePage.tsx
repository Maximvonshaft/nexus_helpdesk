import ExpandMoreRoundedIcon from '@mui/icons-material/ExpandMoreRounded'
import RefreshRoundedIcon from '@mui/icons-material/RefreshRounded'
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  AlertTitle,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Divider,
  List,
  ListItemButton,
  MenuItem,
  Paper,
  Stack,
  Tab,
  Tabs,
  TextField,
  Typography,
} from '@mui/material'
import type { SxProps, Theme } from '@mui/material/styles'
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { supportApi } from '@/lib/supportApi'
import { operatorWorkspaceApi } from '@/lib/operatorWorkspaceApi'
import type {
  UnifiedOperatorQueueItem,
  WorkspaceFilters,
  WorkspaceMobileView,
  WorkspaceScope,
} from '@/lib/operatorWorkspaceTypes'
import {
  evidencePresentation,
  messageDeliveryPresentation,
  outcomePresentation,
  ownerPresentation,
  priorityPresentation,
  queueSourcePresentation,
  retryPresentation,
  slaPresentation,
  sourceStatusPresentation,
} from '@/lib/operatorWorkspacePresentation'
import type {
  BadgeTone,
  SupportMemoryLedger,
  WebchatMessage,
  WebchatThread,
} from '@/lib/types'
import type { SpeedafCancelPreviewResponse } from '@/lib/speedafTypes'
import { useSession } from '@/hooks/useAuth'
import { formatDateTime, sanitizeDisplayText } from '@/lib/format'

type SpeedafActionKind = 'none' | 'waybill_lookup' | 'work_order' | 'address_update' | 'cancel'
type ActionResultEnvelope = { kind: SpeedafActionKind; result: Record<string, unknown> }
type CancelPreviewBinding = {
  fingerprint: string
  result: SpeedafCancelPreviewResponse
}

type Presentation = { label: string; detail?: string; tone: BadgeTone }

const defaultFilters: WorkspaceFilters = {
  state: 'active',
  sourceType: 'all',
  owner: 'any',
  priority: 'all',
  sla: 'any',
  retry: 'any',
  sort: 'oldest',
}

const mobileViews: Array<{ value: WorkspaceMobileView; label: string }> = [
  { value: 'queue', label: '队列' },
  { value: 'case', label: '案例' },
  { value: 'conversation', label: '客户沟通' },
  { value: 'actions', label: '处理' },
]

const toneColor: Record<BadgeTone, string> = {
  default: 'text.secondary',
  warning: 'warning.main',
  success: 'success.main',
  danger: 'error.main',
}

function initialQueueId() {
  if (typeof window === 'undefined') return null
  return new URLSearchParams(window.location.search).get('queue')
}

function initialSessionKey() {
  if (typeof window === 'undefined') return null
  return new URLSearchParams(window.location.search).get('session')
}

function errorCopy(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback
}

function hasCapability(capabilities: Set<string>, ...values: string[]) {
  return values.some((value) => capabilities.has(value))
}

function safeRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? value as Record<string, unknown> : {}
}

function textValue(value: unknown) {
  return typeof value === 'string' ? value : ''
}

function numberValue(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function directionLabel(direction: string) {
  if (direction === 'visitor' || direction === 'customer') return '客户'
  if (direction === 'agent' || direction === 'human') return '客服'
  if (direction === 'ai') return 'AI'
  return '系统'
}

function isOutboundMessage(message: WebchatMessage) {
  return message.direction === 'agent' || message.direction === 'ai'
}

function supportMemoryFromThread(thread?: WebchatThread | null) {
  return thread?.support_memory ?? null
}

function cancelFingerprint(ticketId: number | null, waybill: string, caller: string, reasonCode: string) {
  return JSON.stringify({
    ticketId,
    waybill: waybill.trim().toUpperCase(),
    caller: caller.trim(),
    reasonCode: reasonCode.trim(),
  })
}

function scrollBehavior(): ScrollBehavior {
  if (typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return 'auto'
  return 'smooth'
}

function StatusLine({ presentation, compact = false }: { presentation: Presentation; compact?: boolean }) {
  return (
    <Stack direction="row" spacing={0.75} alignItems="flex-start" sx={{ minWidth: 0 }}>
      <Box
        aria-hidden="true"
        sx={{ bgcolor: toneColor[presentation.tone], borderRadius: '50%', flex: '0 0 auto', height: 8, mt: '6px', width: 8 }}
      />
      <Box sx={{ minWidth: 0 }}>
        <Typography variant={compact ? 'caption' : 'body2'} color="text.primary" sx={{ fontWeight: 650 }}>
          {presentation.label}
        </Typography>
        {!compact && presentation.detail ? (
          <Typography variant="caption" color="text.secondary" display="block">
            {presentation.detail}
          </Typography>
        ) : null}
      </Box>
    </Stack>
  )
}

function SectionHeading({ title, description, action, id }: { title: string; description?: string; action?: React.ReactNode; id?: string }) {
  return (
    <Stack direction="row" spacing={2} alignItems="flex-start" justifyContent="space-between">
      <Box sx={{ minWidth: 0 }}>
        <Typography id={id} component="h2" variant="h3">{title}</Typography>
        {description ? <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>{description}</Typography> : null}
      </Box>
      {action}
    </Stack>
  )
}

function LoadingState({ title, description }: { title: string; description?: string }) {
  return (
    <Stack role="status" alignItems="center" spacing={1.5} sx={{ justifyContent: 'center', minHeight: 150, p: 3 }}>
      <CircularProgress size={28} />
      <Typography variant="subtitle2">{title}</Typography>
      {description ? <Typography variant="body2" color="text.secondary" textAlign="center">{description}</Typography> : null}
    </Stack>
  )
}

function EmptyState({ title, description }: { title: string; description?: string }) {
  return (
    <Stack role="status" alignItems="center" spacing={0.75} sx={{ justifyContent: 'center', minHeight: 140, p: 3, textAlign: 'center' }}>
      <Typography variant="subtitle2">{title}</Typography>
      {description ? <Typography variant="body2" color="text.secondary">{description}</Typography> : null}
    </Stack>
  )
}

function ErrorNotice({ title, error, fallback, action }: { title: string; error: unknown; fallback: string; action?: React.ReactNode }) {
  return (
    <Alert severity="error" variant="outlined" action={action}>
      <AlertTitle>{title}</AlertTitle>
      {errorCopy(error, fallback)}
    </Alert>
  )
}

function QueueFilters({ filters, onChange }: { filters: WorkspaceFilters; onChange: (filters: WorkspaceFilters) => void }) {
  return (
    <Box
      aria-label="队列筛选"
      sx={{ display: 'grid', gap: 1.25, gridTemplateColumns: { xs: '1fr 1fr', md: 'repeat(5, minmax(0, 1fr))', lg: '1fr' } }}
    >
      <TextField select label="状态" value={filters.state} onChange={(event) => onChange({ ...filters, state: event.target.value as WorkspaceFilters['state'] })}>
        <MenuItem value="active">需要处理</MenuItem>
        <MenuItem value="terminal">来源终态</MenuItem>
        <MenuItem value="all">全部</MenuItem>
      </TextField>
      <TextField select label="来源" value={filters.sourceType} onChange={(event) => onChange({ ...filters, sourceType: event.target.value as WorkspaceFilters['sourceType'] })}>
        <MenuItem value="all">全部来源</MenuItem>
        <MenuItem value="handoff">人工接管</MenuItem>
        <MenuItem value="ticket">客服工单</MenuItem>
        <MenuItem value="dispatch">运营派发</MenuItem>
      </TextField>
      <TextField select label="责任人" value={filters.owner} onChange={(event) => onChange({ ...filters, owner: event.target.value as WorkspaceFilters['owner'] })}>
        <MenuItem value="any">全部责任人</MenuItem>
        <MenuItem value="mine">我的</MenuItem>
        <MenuItem value="unassigned">未分配</MenuItem>
        <MenuItem value="team">我的团队</MenuItem>
      </TextField>
      <TextField select label="SLA" value={filters.sla} onChange={(event) => onChange({ ...filters, sla: event.target.value as WorkspaceFilters['sla'] })}>
        <MenuItem value="any">全部 SLA</MenuItem>
        <MenuItem value="breached">已超时</MenuItem>
        <MenuItem value="at_risk">即将超时</MenuItem>
        <MenuItem value="stale">长期未更新</MenuItem>
        <MenuItem value="paused">已暂停</MenuItem>
        <MenuItem value="healthy">正常</MenuItem>
        <MenuItem value="unavailable">不可用</MenuItem>
      </TextField>
      <TextField select label="排序" value={filters.sort} onChange={(event) => onChange({ ...filters, sort: event.target.value as WorkspaceFilters['sort'] })}>
        <MenuItem value="oldest">最早待办优先</MenuItem>
        <MenuItem value="newest">最新更新优先</MenuItem>
      </TextField>
    </Box>
  )
}

function QueueRow({ item, active, currentUserId, onSelect }: {
  item: UnifiedOperatorQueueItem
  active: boolean
  currentUserId?: number
  onSelect: () => void
}) {
  const source = queueSourcePresentation(item.source_type)
  const priority = priorityPresentation(item.priority)
  const owner = ownerPresentation(item.owner, currentUserId)
  const sla = slaPresentation(item.sla)
  const retry = retryPresentation(item.retry)
  const sourceStatus = sourceStatusPresentation(item.source_status)

  return (
    <ListItemButton
      component="button"
      selected={active}
      aria-pressed={active}
      onClick={onSelect}
      sx={{
        alignItems: 'stretch',
        borderBottom: 1,
        borderColor: 'divider',
        display: 'block',
        px: 1.5,
        py: 1.5,
        textAlign: 'left',
        width: '100%',
        '&.Mui-selected': { bgcolor: 'action.selected', boxShadow: 'inset 3px 0 0 #175CD3' },
        '&.Mui-selected:hover': { bgcolor: 'action.selected' },
      }}
    >
      <Stack spacing={0.75}>
        <Stack direction="row" spacing={1} alignItems="flex-start" justifyContent="space-between">
          <Typography variant="subtitle2" sx={{ overflowWrap: 'anywhere' }}>{item.case_key || item.queue_id}</Typography>
          {priority.tone === 'danger' || priority.tone === 'warning' ? (
            <Chip color={priority.tone === 'danger' ? 'error' : 'warning'} label={priority.label} size="small" />
          ) : null}
        </Stack>
        <Typography variant="caption" color="text.secondary">
          {source.label} · {item.country_code} · {item.channel_key}{item.reopened ? ' · 已重新打开' : ''}
        </Typography>
        <Stack direction="row" spacing={2} flexWrap="wrap" useFlexGap>
          <StatusLine presentation={owner} compact />
          <StatusLine presentation={sla} compact />
        </Stack>
        {item.source_type === 'dispatch' ? <Typography variant="caption" color="text.secondary">{retry.label}</Typography> : null}
        <Typography variant="caption" color="text.secondary">{sourceStatus.label}</Typography>
        <Typography component="time" variant="caption" color="text.disabled">{formatDateTime(item.updated_at)}</Typography>
      </Stack>
    </ListItemButton>
  )
}

function QueueRail({ items, selectedQueueId, currentUserId, isLoading, isRefreshing, hasNextPage, isFetchingNextPage, onSelect, onLoadMore }: {
  items: UnifiedOperatorQueueItem[]
  selectedQueueId: string | null
  currentUserId?: number
  isLoading: boolean
  isRefreshing: boolean
  hasNextPage: boolean
  isFetchingNextPage: boolean
  onSelect: (item: UnifiedOperatorQueueItem) => void
  onLoadMore: () => void
}) {
  return (
    <Box component="section" aria-label="待处理任务" aria-busy={isLoading} sx={{ minHeight: 0 }}>
      <SectionHeading
        title="待处理任务"
        description="人工接管、客服工单和运营派发使用同一入口。"
        action={isRefreshing ? <CircularProgress size={18} aria-label="刷新中" /> : null}
      />
      <Divider sx={{ mt: 2 }} />
      {isLoading ? <LoadingState title="正在读取队列" description="正在读取当前授权范围内的任务。" /> : null}
      {!isLoading && !items.length ? <EmptyState title="当前没有待处理任务" description="可以调整筛选条件或稍后刷新。" /> : null}
      <List disablePadding sx={{ maxHeight: { lg: 'calc(100dvh - 360px)' }, overflowY: 'auto' }}>
        {items.map((item) => (
          <QueueRow key={item.queue_id} item={item} active={item.queue_id === selectedQueueId} currentUserId={currentUserId} onSelect={() => onSelect(item)} />
        ))}
      </List>
      {hasNextPage ? (
        <Button
          fullWidth
          color="inherit"
          disabled={isFetchingNextPage}
          startIcon={isFetchingNextPage ? <CircularProgress color="inherit" size={16} /> : undefined}
          onClick={onLoadMore}
          sx={{ mt: 1.5 }}
        >
          {isFetchingNextPage ? '加载中…' : '加载更多任务'}
        </Button>
      ) : null}
    </Box>
  )
}

function CaseHeader({ item, currentUserId }: { item: UnifiedOperatorQueueItem; currentUserId?: number }) {
  const source = queueSourcePresentation(item.source_type)
  const status = sourceStatusPresentation(item.source_status)
  const owner = ownerPresentation(item.owner, currentUserId)
  const sla = slaPresentation(item.sla)
  const retry = retryPresentation(item.retry)

  return (
    <Box component="header" sx={{ pb: 2.5 }}>
      <Typography variant="overline" color="text.secondary">
        {source.label} · {item.country_code} · {item.channel_key}
      </Typography>
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} justifyContent="space-between" alignItems={{ xs: 'stretch', sm: 'flex-start' }}>
        <Box sx={{ minWidth: 0 }}>
          <Typography component="h1" variant="h1" sx={{ overflowWrap: 'anywhere' }}>{item.case_key || item.queue_id}</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.75 }}>
            当前来源与任务状态不会自动代表业务已经完成。
          </Typography>
        </Box>
        <Stack spacing={0.75} sx={{ minWidth: { sm: 220 } }}>
          <StatusLine presentation={status} />
          <StatusLine presentation={owner} />
          <StatusLine presentation={sla} />
          {item.source_type === 'dispatch' ? <StatusLine presentation={retry} /> : null}
          {item.reopened ? <StatusLine presentation={{ label: '已重新打开', tone: 'warning' }} /> : null}
        </Stack>
      </Stack>
      <Accordion disableGutters elevation={0} sx={{ mt: 1.5, '&:before': { display: 'none' }, bgcolor: 'transparent' }}>
        <AccordionSummary expandIcon={<ExpandMoreRoundedIcon />} sx={{ minHeight: 36, px: 0, '& .MuiAccordionSummary-content': { my: 0.5 } }}>
          <Typography variant="caption" color="text.secondary">技术标识</Typography>
        </AccordionSummary>
        <AccordionDetails sx={{ px: 0, pt: 0 }}>
          <Typography component="code" variant="caption" sx={{ overflowWrap: 'anywhere' }}>
            来源 {item.source_type}:{item.source_id}{item.ticket_id ? ` · Ticket #${item.ticket_id}` : ''}
          </Typography>
        </AccordionDetails>
      </Accordion>
    </Box>
  )
}

function CaseSpine({ item, memory }: { item: UnifiedOperatorQueueItem; memory: SupportMemoryLedger | null }) {
  const timeline = memory?.evidence_timeline ?? []
  const latestByClass = (evidenceClass: ReturnType<typeof evidencePresentation>['evidenceClass']) => (
    [...timeline].reverse().find((entry) => evidencePresentation(entry).evidenceClass === evidenceClass)
  )
  const decision = latestByClass('human')
  const result = latestByClass('outcome')
  const notification = latestByClass('notification')
  const nextAction = memory?.required_action || memory?.next_actions?.[0]?.label || ''

  const stages = [
    { label: '工作范围', value: `${item.country_code} · ${item.channel_key}`, available: true },
    { label: '事实证据', value: timeline.length ? `${timeline.length} 条结构化记录` : '未提供', available: timeline.length > 0 },
    { label: '人工决定', value: decision ? sanitizeDisplayText(decision.label || decision.kind) : '未提供', available: Boolean(decision) },
    { label: '下一步', value: nextAction ? sanitizeDisplayText(nextAction) : '未提供', available: Boolean(nextAction) },
    { label: '运营结果', value: result ? sanitizeDisplayText(result.label || result.kind) : '未提供', available: Boolean(result) },
    { label: '客户通知', value: notification ? sanitizeDisplayText(notification.label || notification.kind) : '未提供', available: Boolean(notification) },
    { label: '结案 / 观察', value: '当前接口未提供可信结案事实', available: false },
  ]

  return (
    <Paper variant="outlined" sx={{ mb: 3, overflow: 'hidden' }} aria-label="案例处理链路">
      <Box sx={{ px: 2, py: 1.5, bgcolor: 'background.default', borderBottom: 1, borderColor: 'divider' }}>
        <Typography variant="subtitle2">案例处理链路</Typography>
        <Typography variant="caption" color="text.secondary">只显示当前接口已经提供的事实，缺失阶段不会被推断。</Typography>
      </Box>
      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: 'repeat(2, 1fr)', xl: 'repeat(7, minmax(0, 1fr))' } }}>
        {stages.map((stage, index) => (
          <Box
            key={stage.label}
            sx={{
              borderBottom: { xs: index === stages.length - 1 ? 0 : 1, xl: 0 },
              borderColor: 'divider',
              borderRight: { xl: index === stages.length - 1 ? 0 : 1 },
              minWidth: 0,
              p: 1.5,
            }}
          >
            <Stack direction="row" spacing={0.75} alignItems="center">
              <Box aria-hidden="true" sx={{ bgcolor: stage.available ? 'primary.main' : 'divider', borderRadius: '50%', height: 8, width: 8 }} />
              <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 650 }}>{stage.label}</Typography>
            </Stack>
            <Typography variant="body2" sx={{ mt: 0.75, overflowWrap: 'anywhere' }}>{stage.value}</Typography>
          </Box>
        ))}
      </Box>
    </Paper>
  )
}

function EvidencePanel({ memory, sx }: { memory: SupportMemoryLedger | null; sx?: SxProps<Theme> }) {
  const timeline = memory?.evidence_timeline ?? []
  return (
    <Box component="section" aria-labelledby="operator-evidence-title" sx={sx}>
      <SectionHeading id="operator-evidence-title" title="事实与证据" description="客户主张、知识、AI 建议、人工决定和运营结果明确分开。" />
      <Divider sx={{ my: 2 }} />
      {!timeline.length ? <EmptyState title="暂无结构化证据" description="可以继续查看来源摘要和会话，但不要把缺失证据当成事实。" /> : null}
      <Stack divider={<Divider flexItem />}>
        {timeline.map((entry, index) => {
          const presentation = evidencePresentation(entry)
          return (
            <Box component="article" key={`${entry.kind}-${entry.source_id || index}`} sx={{ py: 1.75 }}>
              <Stack direction="row" spacing={2} justifyContent="space-between" alignItems="flex-start">
                <StatusLine presentation={presentation} />
                {entry.created_at ? <Typography component="time" variant="caption" color="text.disabled">{formatDateTime(entry.created_at)}</Typography> : null}
              </Stack>
              <Typography variant="subtitle2" sx={{ mt: 1 }}>{sanitizeDisplayText(entry.label || entry.kind)}</Typography>
              {presentation.detail ? <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>{presentation.detail}</Typography> : null}
              {entry.summary && Object.keys(entry.summary).length ? (
                <Accordion disableGutters variant="outlined" sx={{ mt: 1.25, '&:before': { display: 'none' } }}>
                  <AccordionSummary expandIcon={<ExpandMoreRoundedIcon />}>
                    <Typography variant="subtitle2">证据摘要</Typography>
                  </AccordionSummary>
                  <AccordionDetails sx={{ borderTop: 1, borderColor: 'divider' }}>
                    <Box component="pre" sx={{ m: 0, maxHeight: 320, overflow: 'auto', whiteSpace: 'pre-wrap', fontSize: 12 }}>
                      {JSON.stringify(entry.summary, null, 2)}
                    </Box>
                  </AccordionDetails>
                </Accordion>
              ) : null}
            </Box>
          )
        })}
      </Stack>
    </Box>
  )
}

function ConversationPanel({ item, thread, isLoading, isRefreshing, error, capabilities, onRefresh, onReplyDirtyChange, selectionUnavailable, sx }: {
  item: UnifiedOperatorQueueItem
  thread: WebchatThread | null
  isLoading: boolean
  isRefreshing: boolean
  error: unknown
  capabilities: Set<string>
  onRefresh: () => Promise<void>
  onReplyDirtyChange: (dirty: boolean) => void
  selectionUnavailable: boolean
  sx?: SxProps<Theme>
}) {
  const [reply, setReply] = useState('')
  const [isNearMessageBottom, setIsNearMessageBottom] = useState(true)
  const [newMessageCount, setNewMessageCount] = useState(0)
  const messagesRef = useRef<HTMLDivElement | null>(null)
  const previousMessageCountRef = useRef(thread?.messages.length ?? 0)
  const canReply = Boolean(item.ticket_id && thread && !selectionUnavailable && hasCapability(capabilities, 'outbound.send', 'webchat.handoff.accept'))

  useEffect(() => {
    setReply('')
    setNewMessageCount(0)
    previousMessageCountRef.current = 0
  }, [item.queue_id])

  useLayoutEffect(() => {
    const currentCount = thread?.messages.length ?? 0
    const added = Math.max(0, currentCount - previousMessageCountRef.current)
    previousMessageCountRef.current = currentCount
    if (!added) return
    const list = messagesRef.current
    if (list && isNearMessageBottom) {
      list.scrollTo({ top: list.scrollHeight, behavior: scrollBehavior() })
      setNewMessageCount(0)
    } else {
      setNewMessageCount((count) => count + added)
    }
  }, [isNearMessageBottom, thread?.messages.length])

  useEffect(() => onReplyDirtyChange(Boolean(reply.trim())), [onReplyDirtyChange, reply])
  useEffect(() => () => onReplyDirtyChange(false), [onReplyDirtyChange])

  const replyMutation = useMutation({
    mutationFn: () => operatorWorkspaceApi.reply(item.ticket_id as number, reply.trim()),
    onSuccess: async () => {
      setReply('')
      onReplyDirtyChange(false)
      await onRefresh()
    },
  })

  const scrollToLatest = () => {
    messagesRef.current?.scrollTo({ top: messagesRef.current.scrollHeight, behavior: scrollBehavior() })
    setNewMessageCount(0)
  }

  return (
    <Box id="workspace-conversation" component="section" aria-labelledby="operator-conversation-title" tabIndex={-1} sx={sx}>
      <SectionHeading
        id="operator-conversation-title"
        title="客户沟通"
        description="回复始终经过服务端权限、事实和安全检查。"
        action={isRefreshing ? <CircularProgress size={18} aria-label="刷新中" /> : null}
      />
      <Divider sx={{ my: 2 }} />
      {isLoading ? <LoadingState title="正在读取会话" description="正在载入客户消息。" /> : null}
      {error ? <ErrorNotice title="会话暂不可用" error={error} fallback="仍可基于案例摘要继续分诊" /> : null}
      {thread ? (
        <Stack spacing={1.5}>
          <Stack
            ref={messagesRef}
            aria-live="polite"
            spacing={1.25}
            sx={{ maxHeight: 520, overflowY: 'auto', pr: 0.5 }}
            onScroll={(event) => {
              const target = event.currentTarget
              const nearBottom = target.scrollHeight - target.scrollTop - target.clientHeight < 80
              setIsNearMessageBottom(nearBottom)
              if (nearBottom) setNewMessageCount(0)
            }}
          >
            {thread.messages.map((message) => {
              const delivery = messageDeliveryPresentation(message.delivery_status)
              const outbound = isOutboundMessage(message)
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
                  <Stack direction="row" spacing={2} justifyContent="space-between">
                    <Typography variant="subtitle2">{sanitizeDisplayText(message.author_label || directionLabel(message.direction))}</Typography>
                    {message.created_at ? <Typography component="time" variant="caption" color="text.disabled">{formatDateTime(message.created_at)}</Typography> : null}
                  </Stack>
                  <Typography variant="body2" sx={{ mt: 0.75, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
                    {sanitizeDisplayText(message.body_text || message.body)}
                  </Typography>
                  {outbound ? (
                    <Stack direction="row" spacing={1} alignItems="center" sx={{ mt: 1 }} aria-label="送达状态">
                      <StatusLine presentation={delivery} compact />
                      {delivery.detail ? <Typography variant="caption" color="text.secondary">{delivery.detail}</Typography> : null}
                    </Stack>
                  ) : null}
                </Box>
              )
            })}
            {!thread.messages.length ? <EmptyState title="暂无消息" description="该会话尚无可显示内容。" /> : null}
          </Stack>
          {newMessageCount ? <Button color="inherit" variant="outlined" onClick={scrollToLatest}>{newMessageCount} 条新消息，查看最新</Button> : null}
          {replyMutation.isError ? <ErrorNotice title="发送失败" error={replyMutation.error} fallback="请稍后重试" /> : null}
          <Box component="form" onSubmit={(event) => { event.preventDefault(); if (canReply && reply.trim()) replyMutation.mutate() }}>
            <Stack spacing={1.25}>
              <TextField
                label="回复客户"
                helperText={canReply ? '技术发送成功不自动等于客户收到或案例结案。' : '当前权限、会话或队列状态不允许回复。'}
                value={reply}
                onChange={(event) => setReply(event.target.value)}
                multiline
                minRows={4}
                placeholder="输入清晰、可验证的客户回复…"
                autoComplete="off"
                disabled={!canReply}
              />
              <Button
                type="submit"
                variant="contained"
                disabled={!canReply || !reply.trim() || replyMutation.isPending}
                startIcon={replyMutation.isPending ? <CircularProgress color="inherit" size={16} /> : undefined}
                sx={{ alignSelf: 'flex-end' }}
              >
                {replyMutation.isPending ? '发送中…' : '发送回复'}
              </Button>
            </Stack>
          </Box>
        </Stack>
      ) : !isLoading ? <EmptyState title="当前案例没有可用会话" description="可以继续查看案例证据，回复和人工接管暂不可用。" /> : null}
    </Box>
  )
}

function actionDisabledReason({ action, item, capabilities, waybill, caller, description, whatsappPhone }: {
  action: SpeedafActionKind
  item: UnifiedOperatorQueueItem
  capabilities: Set<string>
  waybill: string
  caller: string
  description: string
  whatsappPhone: string
}) {
  if (action === 'none') return '请先选择一个与当前任务有关的动作'
  if (!item.ticket_id) return '当前案例没有可执行动作的 Ticket'
  if (action === 'waybill_lookup') return caller.trim() ? '' : '缺少客户电话'
  if (!waybill.trim()) return '缺少运单'
  if (!caller.trim()) return '缺少客户电话'
  if (action === 'work_order' && !hasCapability(capabilities, 'tool:speedaf.work_order.create:write')) return '当前权限不允许创建催派工单'
  if (action === 'address_update' && !hasCapability(capabilities, 'tool:speedaf.order.update_address:write')) return '当前权限不允许更新联系号码'
  if (action === 'cancel' && !hasCapability(capabilities, 'tool:speedaf.order.cancel:write')) return '当前权限不允许请求取消'
  if (action === 'work_order' && !description.trim()) return '缺少催派说明'
  if (action === 'address_update' && !whatsappPhone.trim()) return '缺少确认后的联系号码'
  return ''
}

function ActionPanel({ item, thread, capabilities, onRefresh }: {
  item: UnifiedOperatorQueueItem
  thread: WebchatThread | null
  capabilities: Set<string>
  onRefresh: () => Promise<void>
}) {
  const [action, setAction] = useState<SpeedafActionKind>('none')
  const [waybill, setWaybill] = useState('')
  const [caller, setCaller] = useState('')
  const [countryCode, setCountryCode] = useState(item.country_code || 'CH')
  const [description, setDescription] = useState('')
  const [whatsappPhone, setWhatsappPhone] = useState('')
  const [reasonCode, setReasonCode] = useState('CC01')
  const [cancelPreview, setCancelPreview] = useState<CancelPreviewBinding | null>(null)

  useEffect(() => {
    setAction('none')
    setWaybill('')
    setCaller(thread?.visitor?.phone || '')
    setWhatsappPhone(thread?.visitor?.phone || '')
    setCountryCode(item.country_code || 'CH')
    setDescription('')
    setReasonCode('CC01')
    setCancelPreview(null)
  }, [item.queue_id, item.country_code, thread?.visitor?.phone])

  const invalidateCancelPreview = () => setCancelPreview(null)
  const currentCancelFingerprint = cancelFingerprint(item.ticket_id, waybill, caller, reasonCode)

  const handoffMutation = useMutation({
    mutationFn: async (kind: 'accept' | 'force' | 'release' | 'resume' | 'decline') => {
      const handoff = thread?.handoff
      if (kind === 'accept' && handoff?.id) return supportApi.webchatAcceptHandoff(handoff.id, 'Accepted from Operator Workspace')
      if (kind === 'force' && item.ticket_id) return supportApi.webchatForceTakeover(item.ticket_id, { reason_code: 'operator_takeover', note: 'Operator Workspace takeover' })
      if (kind === 'release' && handoff?.id) return supportApi.webchatReleaseHandoff(handoff.id, 'Released from Operator Workspace')
      if (kind === 'resume' && handoff?.id) return supportApi.webchatResumeAi(handoff.id, 'Resume AI from Operator Workspace')
      if (kind === 'decline' && handoff?.id) return operatorWorkspaceApi.declineHandoff(handoff.id, 'operator_capacity', 'Declined from Operator Workspace')
      throw new Error('当前接管动作不可执行')
    },
    onSuccess: onRefresh,
  })

  const actionMutation = useMutation({
    mutationFn: async (): Promise<ActionResultEnvelope> => {
      if (!item.ticket_id) throw new Error('当前案例没有可执行动作的 Ticket')
      if (action === 'waybill_lookup') {
        const result = await supportApi.querySpeedafWaybills(item.ticket_id, { callerID: caller.trim(), countryCode: countryCode.trim().toUpperCase() })
        return { kind: action, result: result as unknown as Record<string, unknown> }
      }
      if (action === 'work_order') {
        const result = await supportApi.createSpeedafWorkOrder(item.ticket_id, { waybillCode: waybill.trim().toUpperCase(), callerID: caller.trim(), workOrderType: 'WT0103-05', description: description.trim() })
        return { kind: action, result: result as unknown as Record<string, unknown> }
      }
      if (action === 'address_update') {
        const result = await supportApi.submitSpeedafAddressUpdate(item.ticket_id, { waybillCode: waybill.trim().toUpperCase(), callerID: caller.trim(), whatsAppPhone: whatsappPhone.trim() })
        return { kind: action, result: result as unknown as Record<string, unknown> }
      }
      throw new Error('请先选择可执行动作')
    },
    onSuccess: onRefresh,
  })

  const cancelPreviewMutation = useMutation({
    mutationFn: async () => {
      if (!item.ticket_id) throw new Error('当前案例没有可执行动作的 Ticket')
      const fingerprint = currentCancelFingerprint
      const result = await supportApi.previewSpeedafCancel(item.ticket_id, { waybillCode: waybill.trim().toUpperCase(), callerID: caller.trim(), reasonCode })
      return { fingerprint, result }
    },
    onSuccess: setCancelPreview,
  })

  const cancelConfirmMutation = useMutation({
    mutationFn: async (): Promise<ActionResultEnvelope> => {
      if (!item.ticket_id) throw new Error('当前案例没有可执行动作的 Ticket')
      if (!cancelPreview || cancelPreview.fingerprint !== currentCancelFingerprint) throw new Error('取消预检已失效，请基于当前运单、电话和原因重新预检')
      if (!cancelPreview.result.cancelAllowed || !cancelPreview.result.confirmToken) throw new Error('当前预检不允许提交取消请求')
      const result = await supportApi.confirmSpeedafCancel(item.ticket_id, {
        waybillCode: waybill.trim().toUpperCase(),
        callerID: caller.trim(),
        reasonCode,
        confirmToken: cancelPreview.result.confirmToken,
      })
      return { kind: 'cancel', result: result as unknown as Record<string, unknown> }
    },
    onSuccess: async () => { setCancelPreview(null); await onRefresh() },
  })

  const disabledReason = actionDisabledReason({ action, item, capabilities, waybill, caller, description, whatsappPhone })
  const busy = handoffMutation.isPending || actionMutation.isPending || cancelPreviewMutation.isPending || cancelConfirmMutation.isPending
  const actionError = handoffMutation.error || actionMutation.error || cancelPreviewMutation.error || cancelConfirmMutation.error
  const envelope = actionMutation.data || cancelConfirmMutation.data
  const resultRecord = envelope?.result ?? {}
  const resultPresentation = envelope ? outcomePresentation(resultRecord.status, resultRecord.message) : null
  const candidates = Array.isArray(resultRecord.candidates) ? resultRecord.candidates.map(safeRecord) : []
  const handoff = thread?.handoff
  const handoffAllowed = hasCapability(capabilities, 'webchat.handoff.accept', 'webchat.handoff.force_takeover', 'webchat.handoff.release', 'webchat.handoff.resume_ai')

  return (
    <Box id="workspace-actions" component="section" aria-labelledby="operator-actions-title" tabIndex={-1}>
      <SectionHeading id="operator-actions-title" title="下一步" description="不可执行原因直接说明，所有操作仍由服务端最终授权。" />
      <Divider sx={{ my: 2 }} />
      <Stack spacing={2.5}>
        {(handoff?.can_accept || handoff?.can_force_takeover || handoff?.can_decline || handoff?.can_release || handoff?.can_resume_ai) ? (
          <Box>
            <Typography component="h3" variant="subtitle1">案例接管</Typography>
            <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ mt: 1 }}>
              {handoff?.can_accept || handoff?.can_force_takeover ? (
                <Button
                  variant="contained"
                  disabled={!handoffAllowed || handoffMutation.isPending}
                  startIcon={handoffMutation.isPending ? <CircularProgress color="inherit" size={16} /> : undefined}
                  onClick={() => handoffMutation.mutate(handoff?.can_accept ? 'accept' : 'force')}
                >接管案例</Button>
              ) : null}
              {handoff?.can_decline ? <Button color="inherit" variant="outlined" onClick={() => handoffMutation.mutate('decline')}>暂不接管</Button> : null}
              {handoff?.can_release ? <Button color="inherit" onClick={() => handoffMutation.mutate('release')}>释放案例</Button> : null}
              {handoff?.can_resume_ai ? <Button color="inherit" onClick={() => handoffMutation.mutate('resume')}>恢复 AI</Button> : null}
            </Stack>
            {handoff?.reason_text ? <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>接管原因：{sanitizeDisplayText(handoff.reason_text)}</Typography> : null}
          </Box>
        ) : null}

        <Box>
          <Typography component="h3" variant="subtitle1">物流操作</Typography>
          <Stack spacing={1.5} sx={{ mt: 1.25 }}>
            <TextField
              select
              label="选择操作"
              value={action}
              onChange={(event) => {
                setAction(event.target.value as SpeedafActionKind)
                invalidateCancelPreview()
                actionMutation.reset()
                cancelConfirmMutation.reset()
              }}
            >
              <MenuItem value="none">请选择操作</MenuItem>
              <MenuItem value="waybill_lookup">电话查单（只读）</MenuItem>
              <MenuItem value="work_order">创建催派工单</MenuItem>
              <MenuItem value="address_update">提交联系号码更新</MenuItem>
              <MenuItem value="cancel">取消预检与确认</MenuItem>
            </TextField>
            {action !== 'none' ? (
              <>
                {action !== 'waybill_lookup' ? (
                  <TextField label="运单" required value={waybill} onChange={(event) => { setWaybill(event.target.value.toUpperCase()); invalidateCancelPreview() }} autoComplete="off" />
                ) : null}
                <TextField label="客户电话" required type="tel" value={caller} onChange={(event) => { setCaller(event.target.value); invalidateCancelPreview() }} autoComplete="off" />
                {action === 'waybill_lookup' ? <TextField label="国家代码" required value={countryCode} onChange={(event) => setCountryCode(event.target.value.toUpperCase())} /> : null}
                {action === 'work_order' ? <TextField label="催派说明" required value={description} onChange={(event) => setDescription(event.target.value)} multiline minRows={3} /> : null}
                {action === 'address_update' ? <TextField label="确认后的联系号码" required type="tel" value={whatsappPhone} onChange={(event) => setWhatsappPhone(event.target.value)} /> : null}
                {action === 'cancel' ? (
                  <TextField select label="取消原因" required value={reasonCode} onChange={(event) => { setReasonCode(event.target.value); invalidateCancelPreview() }}>
                    <MenuItem value="CC01">派送太慢</MenuItem>
                    <MenuItem value="CC02">快递员服务问题</MenuItem>
                    <MenuItem value="CC03">不支持验货</MenuItem>
                    <MenuItem value="CC04">不支持部分签收</MenuItem>
                    <MenuItem value="CC05">其他原因</MenuItem>
                  </TextField>
                ) : null}
              </>
            ) : null}
            {disabledReason ? <Alert severity="info" variant="outlined">当前不可执行：{disabledReason}</Alert> : null}
            {actionError ? <ErrorNotice title="操作未完成" error={actionError} fallback="请稍后重试" /> : null}
            {candidates.length ? (
              <Paper variant="outlined" sx={{ p: 1.5 }}>
                <Typography variant="subtitle2">候选运单</Typography>
                <Stack divider={<Divider flexItem />} sx={{ mt: 1 }}>
                  {candidates.map((candidate) => (
                    <Stack key={textValue(candidate.waybillCode)} direction="row" spacing={1} alignItems="center" justifyContent="space-between" sx={{ py: 1 }}>
                      <Typography component="code" variant="body2">{sanitizeDisplayText(textValue(candidate.waybillCode))}</Typography>
                      <Button size="small" color="inherit" variant="outlined" onClick={() => { setWaybill(textValue(candidate.waybillCode)); setAction('work_order'); invalidateCancelPreview() }}>填入催派</Button>
                    </Stack>
                  ))}
                </Stack>
              </Paper>
            ) : null}
            {cancelPreview ? (
              <Alert severity={cancelPreview.result.cancelAllowed ? 'info' : 'warning'} variant="outlined" role="status">
                <AlertTitle>{cancelPreview.result.cancelAllowed ? '预检允许提交取消请求' : '当前状态不允许取消'}</AlertTitle>
                {sanitizeDisplayText(cancelPreview.result.currentStatusLabel || cancelPreview.result.reasonLabel || '未返回原因')}
                <Typography variant="caption" display="block" sx={{ mt: 0.75 }}>
                  预检不是取消完成；预检绑定当前案例、运单、电话和原因，任一输入变化后必须重新预检。
                </Typography>
              </Alert>
            ) : null}
            {resultPresentation ? (
              <Alert
                severity={resultPresentation.tone === 'danger' ? 'error' : resultPresentation.tone === 'warning' ? 'warning' : resultPresentation.tone === 'success' ? 'success' : 'info'}
                variant="outlined"
                role="status"
              >
                <AlertTitle>{resultPresentation.label}</AlertTitle>
                {resultPresentation.detail}
                {numberValue(resultRecord.jobId) ? (
                  <Accordion disableGutters elevation={0} sx={{ mt: 1, bgcolor: 'transparent', '&:before': { display: 'none' } }}>
                    <AccordionSummary expandIcon={<ExpandMoreRoundedIcon />} sx={{ px: 0 }}><Typography variant="caption">技术追踪标识</Typography></AccordionSummary>
                    <AccordionDetails sx={{ px: 0 }}><Typography component="code" variant="caption">Job #{numberValue(resultRecord.jobId)}</Typography></AccordionDetails>
                  </Accordion>
                ) : null}
              </Alert>
            ) : null}
            <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
              {action === 'cancel' ? (
                <>
                  <Button
                    color="inherit"
                    variant="outlined"
                    disabled={Boolean(disabledReason) || busy}
                    startIcon={cancelPreviewMutation.isPending ? <CircularProgress color="inherit" size={16} /> : undefined}
                    onClick={() => cancelPreviewMutation.mutate()}
                  >先做取消预检</Button>
                  <Button
                    color="error"
                    variant="contained"
                    disabled={!cancelPreview?.result.cancelAllowed || !cancelPreview.result.confirmToken || cancelPreview.fingerprint !== currentCancelFingerprint || busy}
                    startIcon={cancelConfirmMutation.isPending ? <CircularProgress color="inherit" size={16} /> : undefined}
                    onClick={() => cancelConfirmMutation.mutate()}
                  >确认提交取消请求</Button>
                </>
              ) : action !== 'none' ? (
                <Button
                  variant={action === 'work_order' ? 'contained' : 'outlined'}
                  color={action === 'work_order' ? 'primary' : 'inherit'}
                  disabled={Boolean(disabledReason) || busy}
                  startIcon={actionMutation.isPending ? <CircularProgress color="inherit" size={16} /> : undefined}
                  onClick={() => actionMutation.mutate()}
                >
                  {action === 'waybill_lookup' ? '查询运单' : action === 'work_order' ? '创建催派工单' : '提交联系号码更新'}
                </Button>
              ) : null}
            </Stack>
          </Stack>
        </Box>
      </Stack>
    </Box>
  )
}

function SourceSummary({ data, item }: { data: Record<string, unknown>; item: UnifiedOperatorQueueItem }) {
  const facts = [
    ['标题', sanitizeDisplayText(textValue(data.title) || '未提供')],
    ['状态', sanitizeDisplayText(textValue(data.status) || item.source_status)],
    ['优先级', sanitizeDisplayText(textValue(data.priority) || item.priority)],
  ]
  return (
    <Box component="section" sx={{ py: 2.5 }}>
      <SectionHeading title="来源摘要" />
      <Box component="dl" sx={{ display: 'grid', gap: 1.5, gridTemplateColumns: { xs: '1fr', sm: 'repeat(3, 1fr)' }, m: 0, mt: 2 }}>
        {facts.map(([label, value]) => (
          <Box key={label}>
            <Typography component="dt" variant="caption" color="text.secondary">{label}</Typography>
            <Typography component="dd" variant="body2" sx={{ m: 0, mt: 0.5 }}>{value}</Typography>
          </Box>
        ))}
      </Box>
    </Box>
  )
}

export function OperatorWorkspacePage({ scope }: { scope: WorkspaceScope }) {
  const queryClient = useQueryClient()
  const session = useSession()
  const capabilities = useMemo(() => new Set(session.data?.capabilities ?? []), [session.data?.capabilities])
  const [filters, setFilters] = useState<WorkspaceFilters>(defaultFilters)
  const [selectedQueueId, setSelectedQueueId] = useState<string | null>(() => initialQueueId())
  const [requestedSessionKey] = useState<string | null>(() => initialSessionKey())
  const [mobileView, setMobileView] = useState<WorkspaceMobileView>('queue')
  const [replyDraftDirty, setReplyDraftDirty] = useState(false)
  const [replyDiscardOpen, setReplyDiscardOpen] = useState(false)
  const pendingReplyActionRef = useRef<(() => void) | null>(null)
  const [retainedSelectedItem, setRetainedSelectedItem] = useState<UnifiedOperatorQueueItem | null>(null)

  useEffect(() => { document.title = '案例处理 · Nexus OSR' }, [])
  useEffect(() => {
    if (!replyDraftDirty) return undefined
    const protectDraft = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', protectDraft)
    return () => window.removeEventListener('beforeunload', protectDraft)
  }, [replyDraftDirty])
  useLayoutEffect(() => {
    const targetId: Record<WorkspaceMobileView, string> = {
      queue: 'workspace-queue',
      case: 'workspace-case',
      conversation: 'workspace-conversation',
      actions: 'workspace-actions',
    }
    document.getElementById(targetId[mobileView])?.focus({ preventScroll: true })
  }, [mobileView])
  useEffect(() => {
    const url = new URL(window.location.href)
    if (selectedQueueId) {
      url.searchParams.set('queue', selectedQueueId)
      url.searchParams.delete('session')
    } else {
      url.searchParams.delete('queue')
      if (!requestedSessionKey) url.searchParams.delete('session')
    }
    window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`)
  }, [requestedSessionKey, selectedQueueId])

  const canReadQueue = hasCapability(capabilities, 'operator_queue.read')
  const requestedConversation = useQuery({
    queryKey: ['operatorWorkspaceSessionDeepLink', scope, requestedSessionKey],
    queryFn: () => supportApi.supportConversationDetail(requestedSessionKey || ''),
    enabled: Boolean(session.data && canReadQueue && requestedSessionKey),
    retry: false,
  })
  const requestedQueueId = useMemo(() => {
    const conversation = requestedConversation.data?.conversation
    if (conversation?.handoff_request_id) return `handoff:${conversation.handoff_request_id}`
    if (conversation?.ticket_id) return `ticket:${conversation.ticket_id}`
    return null
  }, [requestedConversation.data?.conversation])
  const queue = useInfiniteQuery({
    queryKey: ['operatorWorkspaceQueue', scope, filters],
    queryFn: ({ pageParam }) => operatorWorkspaceApi.unifiedQueue(scope, filters, pageParam as string | null),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor || undefined,
    enabled: Boolean(session.data && canReadQueue),
    retry: false,
    refetchInterval: 15_000,
  })
  const queueItems = useMemo(() => queue.data?.pages.flatMap((page) => page.items) ?? [], [queue.data?.pages])
  const selectedQueueItem = useMemo(() => queueItems.find((item) => item.queue_id === selectedQueueId) ?? null, [queueItems, selectedQueueId])
  const requestedQueueItem = useMemo(() => queueItems.find((item) => item.queue_id === requestedQueueId) ?? null, [queueItems, requestedQueueId])
  const resolvingSessionDeepLink = Boolean(
    requestedSessionKey
    && !requestedConversation.isError
    && (
      requestedConversation.isLoading
      || (requestedQueueId && !requestedQueueItem && (queue.isLoading || queue.hasNextPage || queue.isFetchingNextPage))
    ),
  )
  const selectedQueueItemMissing = Boolean(selectedQueueId && !selectedQueueItem && retainedSelectedItem?.queue_id === selectedQueueId)
  const preserveMissingSelection = replyDraftDirty && selectedQueueItemMissing
  const selectedItem = selectedQueueItem
    ?? (preserveMissingSelection ? retainedSelectedItem : null)
    ?? requestedQueueItem
    ?? (resolvingSessionDeepLink ? null : queueItems[0] ?? null)

  useEffect(() => { if (selectedQueueItem) setRetainedSelectedItem(selectedQueueItem) }, [selectedQueueItem])
  useEffect(() => {
    if (requestedQueueItem && selectedQueueId !== requestedQueueItem.queue_id) setSelectedQueueId(requestedQueueItem.queue_id)
    else if (!selectedQueueId && selectedItem && !resolvingSessionDeepLink) setSelectedQueueId(selectedItem.queue_id)
    else if (selectedQueueId && !selectedQueueItem && !replyDraftDirty) setSelectedQueueId(queueItems[0]?.queue_id ?? null)
  }, [queueItems, replyDraftDirty, requestedQueueItem, resolvingSessionDeepLink, selectedItem, selectedQueueId, selectedQueueItem])
  useEffect(() => {
    if (requestedQueueId && !requestedQueueItem && queue.hasNextPage && !queue.isFetchingNextPage) {
      void queue.fetchNextPage()
    }
  }, [queue, requestedQueueId, requestedQueueItem])

  const thread = useQuery({
    queryKey: ['operatorWorkspaceThread', selectedItem?.queue_id, selectedItem?.source_links.conversation],
    queryFn: () => operatorWorkspaceApi.conversationThread(selectedItem?.source_links.conversation || ''),
    enabled: Boolean(selectedItem?.source_links.conversation),
    retry: false,
    refetchInterval: selectedItem?.source_links.conversation ? 5_000 : false,
  })
  const sourceRecord = useQuery({
    queryKey: ['operatorWorkspaceSourceRecord', selectedItem?.queue_id, selectedItem?.source_links.ticket],
    queryFn: () => operatorWorkspaceApi.sourceRecord(selectedItem?.source_links.ticket || ''),
    enabled: Boolean(selectedItem?.source_links.ticket && !selectedItem?.source_links.conversation),
    retry: false,
  })
  const refreshSelected = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['operatorWorkspaceQueue'] }),
      queryClient.invalidateQueries({ queryKey: ['operatorWorkspaceThread', selectedItem?.queue_id] }),
      queryClient.invalidateQueries({ queryKey: ['operatorWorkspaceSourceRecord', selectedItem?.queue_id] }),
    ])
  }
  const runWithReplyDraftGuard = (action: () => void) => {
    if (!replyDraftDirty) return action()
    pendingReplyActionRef.current = action
    setReplyDiscardOpen(true)
  }
  const selectItem = (item: UnifiedOperatorQueueItem) => runWithReplyDraftGuard(() => { setSelectedQueueId(item.queue_id); setMobileView('case') })
  const memory = supportMemoryFromThread(thread.data)
  const caseContentVisible = mobileView === 'case'
  const conversationVisible = mobileView === 'conversation'

  return (
    <Box component="main" data-testid="operator-workspace" sx={{ p: { xs: 1.5, md: 2 } }}>
      <Tabs
        value={mobileView}
        onChange={(_, value: WorkspaceMobileView) => setMobileView(value)}
        aria-label="移动端工作区"
        variant="scrollable"
        allowScrollButtonsMobile
        sx={{ display: { xs: 'flex', lg: 'none' }, mb: 1.5, borderBottom: 1, borderColor: 'divider' }}
      >
        {mobileViews.map((view) => <Tab key={view.value} value={view.value} label={view.label} />)}
      </Tabs>

      {session.isError ? <ErrorNotice title="无法读取当前用户" error={session.error} fallback="请重新登录" /> : null}
      {session.data && !canReadQueue ? <Alert severity="warning" variant="outlined">当前账号无权访问任务队列，请联系管理员核对权限。</Alert> : null}

      {session.data && canReadQueue ? (
        <Box
          sx={{
            display: 'grid',
            gap: 2,
            gridTemplateColumns: { xs: 'minmax(0, 1fr)', lg: 'minmax(280px, 330px) minmax(0, 1fr) minmax(300px, 360px)' },
            minHeight: { lg: 'calc(100dvh - 104px)' },
          }}
        >
          <Paper
            id="workspace-queue"
            component="aside"
            tabIndex={-1}
            variant="outlined"
            sx={{ display: { xs: mobileView === 'queue' ? 'block' : 'none', lg: 'block' }, minHeight: 0, p: 1.5 }}
          >
            <Stack spacing={2}>
              <QueueFilters filters={filters} onChange={(next) => runWithReplyDraftGuard(() => { setFilters(next); setSelectedQueueId(null) })} />
              {queue.isError ? (
                <ErrorNotice
                  title="任务队列不可用"
                  error={queue.error}
                  fallback="请检查当前授权范围"
                  action={<Button color="inherit" size="small" startIcon={<RefreshRoundedIcon />} onClick={() => queue.refetch()}>重新加载</Button>}
                />
              ) : null}
              <QueueRail
                items={queueItems}
                selectedQueueId={selectedItem?.queue_id ?? null}
                currentUserId={session.data.id}
                isLoading={queue.isLoading}
                isRefreshing={queue.isFetching && !queue.isLoading}
                hasNextPage={Boolean(queue.hasNextPage)}
                isFetchingNextPage={queue.isFetchingNextPage}
                onSelect={selectItem}
                onLoadMore={() => queue.fetchNextPage()}
              />
            </Stack>
          </Paper>

          <Paper
            id="workspace-case"
            component="section"
            aria-label="当前案例"
            tabIndex={-1}
            variant="outlined"
            sx={{ display: { xs: caseContentVisible || conversationVisible ? 'block' : 'none', lg: 'block' }, minWidth: 0, p: { xs: 2, md: 2.5 } }}
          >
            {selectedItem ? (
              <>
                <Box sx={{ display: { xs: caseContentVisible ? 'block' : 'none', lg: 'block' } }}>
                  <CaseHeader item={selectedItem} currentUserId={session.data.id} />
                  <CaseSpine item={selectedItem} memory={memory} />
                  {preserveMissingSelection ? (
                    <Alert severity="warning" variant="outlined" sx={{ mb: 2.5 }}>
                      <AlertTitle>当前任务已离开队列，回复草稿仍保留</AlertTitle>
                      发送和物流操作已暂停；切换任务前需要确认是否放弃草稿。
                    </Alert>
                  ) : null}
                  {sourceRecord.data && !thread.data ? <SourceSummary data={sourceRecord.data} item={selectedItem} /> : null}
                  <EvidencePanel memory={memory} />
                </Box>
                <ConversationPanel
                  item={selectedItem}
                  thread={thread.data ?? null}
                  isLoading={thread.isLoading}
                  isRefreshing={thread.isFetching && !thread.isLoading}
                  error={thread.error}
                  capabilities={capabilities}
                  onRefresh={refreshSelected}
                  onReplyDirtyChange={setReplyDraftDirty}
                  selectionUnavailable={preserveMissingSelection}
                  sx={{ display: { xs: conversationVisible ? 'block' : 'none', lg: 'block' }, mt: { lg: 3 }, pt: { lg: 3 }, borderTop: { lg: 1 }, borderColor: { lg: 'divider' } }}
                />
              </>
            ) : <EmptyState title="选择一个任务开始处理" description="从待处理任务中选择人工接管、客服工单或运营派发任务。" />}
          </Paper>

          <Paper
            component="aside"
            aria-label="案例操作与结果"
            variant="outlined"
            sx={{ display: { xs: mobileView === 'actions' ? 'block' : 'none', lg: 'block' }, minWidth: 0, p: 2, alignSelf: 'start', position: { lg: 'sticky' }, top: { lg: 84 } }}
          >
            {selectedItem ? (
              <Stack spacing={2.5}>
                <Box>
                  <Typography component="h2" variant="h3">当前任务</Typography>
                  <Typography variant="subtitle1" sx={{ mt: 1 }}>
                    {sanitizeDisplayText(memory?.required_action || memory?.next_actions?.[0]?.label || '核实当前事实并决定下一步')}
                  </Typography>
                  <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 0.75 }}>
                    页面提示不替代服务端权限、政策和真实操作结果。
                  </Typography>
                </Box>
                {preserveMissingSelection ? <Alert severity="warning">该任务已不在授权队列中，操作已暂停。</Alert> : <ActionPanel item={selectedItem} thread={thread.data ?? null} capabilities={capabilities} onRefresh={refreshSelected} />}
              </Stack>
            ) : <EmptyState title="暂无操作" description="选择案例后显示允许操作和结果。" />}
          </Paper>
        </Box>
      ) : null}

      <Dialog
        open={replyDiscardOpen}
        onClose={() => { setReplyDiscardOpen(false); pendingReplyActionRef.current = null }}
        aria-labelledby="reply-discard-title"
        aria-describedby="reply-discard-description"
      >
        <DialogTitle id="reply-discard-title">放弃未发送的回复？</DialogTitle>
        <DialogContent>
          <DialogContentText id="reply-discard-description">切换案例或筛选后，这段回复不会被保留。</DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button color="inherit" onClick={() => { setReplyDiscardOpen(false); pendingReplyActionRef.current = null }}>继续编辑</Button>
          <Button
            color="error"
            variant="contained"
            onClick={() => {
              const action = pendingReplyActionRef.current
              pendingReplyActionRef.current = null
              setReplyDiscardOpen(false)
              setReplyDraftDirty(false)
              action?.()
            }}
          >放弃回复</Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}

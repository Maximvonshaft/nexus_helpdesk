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
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { supportApi } from '@/lib/supportApi'
import { operatorWorkspaceApi } from '@/lib/operatorWorkspaceApi'
import type { OperatorWorkspaceThread } from '@/lib/operatorWorkspaceApi'
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
  { value: 'queue', label: '待处理' },
  { value: 'case', label: '任务详情' },
  { value: 'conversation', label: '客户沟通' },
  { value: 'actions', label: '操作' },
]

const toneColor: Record<BadgeTone, string> = {
  default: 'text.secondary',
  warning: 'warning.main',
  success: 'success.main',
  danger: 'error.main',
}

const EVENT_IDLE_POLL_MS = 4_000
const EVENT_RETRY_BASE_MS = 1_000
const EVENT_RETRY_MAX_MS = 30_000

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
  if (direction === 'ai') return '自动回复'
  return '系统'
}

function isOutboundMessage(message: WebchatMessage) {
  return message.direction === 'agent' || message.direction === 'ai'
}

function supportMemoryFromThread(thread?: OperatorWorkspaceThread | null) {
  return thread?.support_memory ?? null
}

function mergeMessages(...groups: WebchatMessage[][]) {
  const byId = new Map<string, WebchatMessage>()
  groups.flat().forEach((message) => byId.set(String(message.id), message))
  return [...byId.values()].sort((left, right) => Number(left.id) - Number(right.id))
}

function mergeLatestThread(current: OperatorWorkspaceThread | undefined, latest: OperatorWorkspaceThread) {
  if (!current?.history_expanded) return { ...latest, history_expanded: false }
  return {
    ...latest,
    messages: mergeMessages(current.messages, latest.messages),
    message_page: current.message_page,
    history_expanded: true,
  }
}

function mergeOlderThread(current: OperatorWorkspaceThread | undefined, older: OperatorWorkspaceThread) {
  if (!current) return { ...older, history_expanded: true }
  return {
    ...current,
    messages: mergeMessages(older.messages, current.messages),
    message_page: older.message_page,
    history_expanded: true,
  }
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

function muiStatusColor(tone: BadgeTone): 'default' | 'success' | 'warning' | 'error' {
  if (tone === 'success') return 'success'
  if (tone === 'warning') return 'warning'
  if (tone === 'danger') return 'error'
  return 'default'
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

function SectionHeading({ title, action, id }: { title: string; action?: ReactNode; id?: string }) {
  return (
    <Stack direction="row" spacing={2} alignItems="flex-start" justifyContent="space-between">
      <Typography id={id} component="h2" variant="h3">{title}</Typography>
      {action}
    </Stack>
  )
}

function LoadingState({ title }: { title: string }) {
  return (
    <Stack role="status" alignItems="center" spacing={1.5} sx={{ justifyContent: 'center', minHeight: 150, p: 3 }}>
      <CircularProgress size={28} />
      <Typography variant="subtitle2">{title}</Typography>
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

function ErrorNotice({ title, error, fallback, action }: { title: string; error: unknown; fallback: string; action?: ReactNode }) {
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
      aria-label="任务筛选"
      sx={{ display: 'grid', gap: 1.25, gridTemplateColumns: { xs: '1fr 1fr', md: 'repeat(5, minmax(0, 1fr))', lg: '1fr' } }}
    >
      <TextField select label="状态" value={filters.state} onChange={(event) => onChange({ ...filters, state: event.target.value as WorkspaceFilters['state'] })}>
        <MenuItem value="active">需要处理</MenuItem>
        <MenuItem value="terminal">来源已结束</MenuItem>
        <MenuItem value="all">全部</MenuItem>
      </TextField>
      <TextField select label="任务类型" value={filters.sourceType} onChange={(event) => onChange({ ...filters, sourceType: event.target.value as WorkspaceFilters['sourceType'] })}>
        <MenuItem value="all">全部类型</MenuItem>
        <MenuItem value="handoff">待接手</MenuItem>
        <MenuItem value="ticket">客服工单</MenuItem>
        <MenuItem value="dispatch">内部任务</MenuItem>
      </TextField>
      <TextField select label="当前负责人" value={filters.owner} onChange={(event) => onChange({ ...filters, owner: event.target.value as WorkspaceFilters['owner'] })}>
        <MenuItem value="any">全部负责人</MenuItem>
        <MenuItem value="mine">我的</MenuItem>
        <MenuItem value="unassigned">未分配</MenuItem>
        <MenuItem value="team">我的团队</MenuItem>
      </TextField>
      <TextField select label="处理时限" value={filters.sla} onChange={(event) => onChange({ ...filters, sla: event.target.value as WorkspaceFilters['sla'] })}>
        <MenuItem value="any">全部时限</MenuItem>
        <MenuItem value="breached">已超时</MenuItem>
        <MenuItem value="at_risk">即将超时</MenuItem>
        <MenuItem value="stale">长期未更新</MenuItem>
        <MenuItem value="paused">已暂停</MenuItem>
        <MenuItem value="healthy">正常</MenuItem>
        <MenuItem value="unavailable">未知</MenuItem>
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
            <Chip color={muiStatusColor(priority.tone)} label={priority.label} size="small" />
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
      <SectionHeading title="待处理任务" action={isRefreshing ? <CircularProgress size={18} aria-label="刷新中" /> : null} />
      <Divider sx={{ mt: 2 }} />
      {isLoading ? <LoadingState title="正在读取任务…" /> : null}
      {!isLoading && !items.length ? <EmptyState title="暂无待处理任务" description="请调整筛选或刷新" /> : null}
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
        <Typography component="h1" variant="h1" sx={{ minWidth: 0, overflowWrap: 'anywhere' }}>{item.case_key || item.queue_id}</Typography>
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
          <Typography variant="caption" color="text.secondary">系统信息</Typography>
        </AccordionSummary>
        <AccordionDetails sx={{ px: 0, pt: 0 }}>
          <Typography component="code" variant="caption" sx={{ overflowWrap: 'anywhere' }}>
            任务 {item.source_type}:{item.source_id}{item.ticket_id ? ` · 工单 #${item.ticket_id}` : ''}
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
    { label: '范围', value: `${item.country_code} · ${item.channel_key}`, available: true },
    { label: '已知信息', value: timeline.length ? `${timeline.length} 条` : '未提供', available: timeline.length > 0 },
    { label: '处理决定', value: decision ? sanitizeDisplayText(decision.label || decision.kind) : '未提供', available: Boolean(decision) },
    { label: '下一步', value: nextAction ? sanitizeDisplayText(nextAction) : '未提供', available: Boolean(nextAction) },
    { label: '操作结果', value: result ? sanitizeDisplayText(result.label || result.kind) : '未提供', available: Boolean(result) },
    { label: '客户通知', value: notification ? sanitizeDisplayText(notification.label || notification.kind) : '未提供', available: Boolean(notification) },
    { label: '结案状态', value: '暂无可信结案信息', available: false },
  ]

  return (
    <Paper variant="outlined" sx={{ mb: 3, overflow: 'hidden' }} aria-label="处理进度">
      <Box sx={{ px: 2, py: 1.5, bgcolor: 'background.default', borderBottom: 1, borderColor: 'divider' }}>
        <Typography variant="subtitle2">处理进度</Typography>
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
      <SectionHeading id="operator-evidence-title" title="已知信息" />
      <Divider sx={{ my: 2 }} />
      {!timeline.length ? <EmptyState title="暂无结构化信息" description="可查看任务摘要和客户沟通" /> : null}
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
              {entry.summary && Object.keys(entry.summary).length ? (
                <Accordion disableGutters variant="outlined" sx={{ mt: 1.25, '&:before': { display: 'none' } }}>
                  <AccordionSummary expandIcon={<ExpandMoreRoundedIcon />}>
                    <Typography variant="subtitle2">信息摘要</Typography>
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

function ConversationPanel({
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
  const [isNearMessageBottom, setIsNearMessageBottom] = useState(true)
  const [newMessageCount, setNewMessageCount] = useState(0)
  const messagesRef = useRef<HTMLDivElement | null>(null)
  const previousLatestMessageIdRef = useRef<string | number | undefined>(thread?.messages.at(-1)?.id)
  const canReply = Boolean(item.ticket_id && thread && !selectionUnavailable && hasCapability(capabilities, 'outbound.send', 'webchat.handoff.accept'))

  useEffect(() => {
    setReply('')
    setIsNearMessageBottom(true)
    setNewMessageCount(0)
    previousLatestMessageIdRef.current = undefined
  }, [item.queue_id])

  useLayoutEffect(() => {
    const messages = thread?.messages ?? []
    const currentLatestMessageId = messages.at(-1)?.id
    const previousLatestMessageId = previousLatestMessageIdRef.current
    const latestChanged = currentLatestMessageId !== previousLatestMessageId
    const previousLatestNumeric = Number(previousLatestMessageId)
    const added = latestChanged
      ? Number.isFinite(previousLatestNumeric)
        ? messages.filter((message) => Number(message.id) > previousLatestNumeric).length
        : messages.length
      : 0
    previousLatestMessageIdRef.current = currentLatestMessageId
    if (!added) return
    const list = messagesRef.current
    if (list && isNearMessageBottom) {
      list.scrollTo({ top: list.scrollHeight, behavior: scrollBehavior() })
      setNewMessageCount(0)
    } else {
      setNewMessageCount((count) => count + added)
    }
  }, [isNearMessageBottom, thread?.messages])

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
    messagesRef.current?.scrollTo({ top: messagesRef.current.scrollHeight, behavior: scrollBehavior() })
    setNewMessageCount(0)
  }

  return (
    <Box id="workspace-conversation" component="section" aria-labelledby="operator-conversation-title" tabIndex={-1} sx={sx}>
      <SectionHeading id="operator-conversation-title" title="客户沟通" action={isRefreshing ? <CircularProgress size={18} aria-label="刷新中" /> : null} />
      <Divider sx={{ my: 2 }} />
      {isLoading ? <LoadingState title="正在读取消息…" /> : null}
      {error ? <ErrorNotice title="无法读取客户沟通" error={error} fallback="仍可查看任务摘要" /> : null}
      {historyError ? <ErrorNotice title="更早消息加载失败" error={historyError} fallback="可稍后重试" /> : null}
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
                    </Stack>
                  ) : null}
                </Box>
              )
            })}
            {!thread.messages.length ? <EmptyState title="暂无消息" /> : null}
          </Stack>
          {newMessageCount ? <Button color="inherit" variant="outlined" onClick={scrollToLatest}>{newMessageCount} 条新消息，查看最新</Button> : null}
          {replyMutation.isError ? <ErrorNotice title="发送失败" error={replyMutation.error} fallback="请稍后重试" /> : null}
          <Box component="form" onSubmit={(event) => { event.preventDefault(); if (canReply && reply.trim()) replyMutation.mutate() }}>
            <Stack spacing={1.25}>
              <TextField
                label="回复客户"
                helperText={canReply ? '发送状态以送达结果为准。' : '当前不可回复。'}
                value={reply}
                onChange={(event) => setReply(event.target.value)}
                multiline
                minRows={4}
                placeholder="输入回复"
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
      ) : !isLoading ? <EmptyState title="暂无客户沟通" description="回复和接手处理暂不可用" /> : null}
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
  if (action === 'none') return '请先选择操作'
  if (!item.ticket_id) return '当前任务没有可操作的工单'
  if (action === 'waybill_lookup') return caller.trim() ? '' : '缺少客户电话'
  if (!waybill.trim()) return '缺少运单'
  if (!caller.trim()) return '缺少客户电话'
  if (action === 'work_order' && !hasCapability(capabilities, 'tool:speedaf.work_order.create:write')) return '无权创建催派工单'
  if (action === 'address_update' && !hasCapability(capabilities, 'tool:speedaf.order.update_address:write')) return '无权更新联系号码'
  if (action === 'cancel' && !hasCapability(capabilities, 'tool:speedaf.order.cancel:write')) return '无权申请取消'
  if (action === 'work_order' && !description.trim()) return '缺少催派说明'
  if (action === 'address_update' && !whatsappPhone.trim()) return '缺少确认后的联系号码'
  return ''
}

function ActionPanel({ item, thread, capabilities, onRefresh }: {
  item: UnifiedOperatorQueueItem
  thread: OperatorWorkspaceThread | null
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
      throw new Error('当前接手操作不可执行')
    },
    onSuccess: onRefresh,
  })

  const actionMutation = useMutation({
    mutationFn: async (): Promise<ActionResultEnvelope> => {
      if (!item.ticket_id) throw new Error('当前任务没有可操作的工单')
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
      throw new Error('请选择操作')
    },
    onSuccess: onRefresh,
  })

  const cancelPreviewMutation = useMutation({
    mutationFn: async () => {
      if (!item.ticket_id) throw new Error('当前任务没有可操作的工单')
      const fingerprint = currentCancelFingerprint
      const result = await supportApi.previewSpeedafCancel(item.ticket_id, { waybillCode: waybill.trim().toUpperCase(), callerID: caller.trim(), reasonCode })
      return { fingerprint, result }
    },
    onSuccess: setCancelPreview,
  })

  const cancelConfirmMutation = useMutation({
    mutationFn: async (): Promise<ActionResultEnvelope> => {
      if (!item.ticket_id) throw new Error('当前任务没有可操作的工单')
      if (!cancelPreview || cancelPreview.fingerprint !== currentCancelFingerprint) throw new Error('检查结果已失效，请重新检查')
      if (!cancelPreview.result.cancelAllowed || !cancelPreview.result.confirmToken) throw new Error('当前不可申请取消')
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
      <SectionHeading id="operator-actions-title" title="下一步" />
      <Divider sx={{ my: 2 }} />
      <Stack spacing={2.5}>
        {(handoff?.can_accept || handoff?.can_force_takeover || handoff?.can_decline || handoff?.can_release || handoff?.can_resume_ai) ? (
          <Box>
            <Typography component="h3" variant="subtitle1">接手任务</Typography>
            <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ mt: 1 }}>
              {handoff?.can_accept || handoff?.can_force_takeover ? (
                <Button
                  variant="contained"
                  disabled={!handoffAllowed || handoffMutation.isPending}
                  startIcon={handoffMutation.isPending ? <CircularProgress color="inherit" size={16} /> : undefined}
                  onClick={() => handoffMutation.mutate(handoff?.can_accept ? 'accept' : 'force')}
                >接手处理</Button>
              ) : null}
              {handoff?.can_decline ? <Button color="inherit" variant="outlined" onClick={() => handoffMutation.mutate('decline')}>暂不处理</Button> : null}
              {handoff?.can_release ? <Button color="inherit" onClick={() => handoffMutation.mutate('release')}>转回待处理</Button> : null}
              {handoff?.can_resume_ai ? <Button color="inherit" onClick={() => handoffMutation.mutate('resume')}>恢复自动回复</Button> : null}
            </Stack>
            {handoff?.reason_text ? <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>接手原因：{sanitizeDisplayText(handoff.reason_text)}</Typography> : null}
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
              <MenuItem value="waybill_lookup">按电话查询运单</MenuItem>
              <MenuItem value="work_order">创建催派工单</MenuItem>
              <MenuItem value="address_update">更新联系号码</MenuItem>
              <MenuItem value="cancel">申请取消订单</MenuItem>
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
            {disabledReason ? <Alert severity="info" variant="outlined">{disabledReason}</Alert> : null}
            {actionError ? <ErrorNotice title="操作失败" error={actionError} fallback="请稍后重试" /> : null}
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
                <AlertTitle>{cancelPreview.result.cancelAllowed ? '可以申请取消' : '当前不可取消'}</AlertTitle>
                {sanitizeDisplayText(cancelPreview.result.currentStatusLabel || cancelPreview.result.reasonLabel || '未返回原因')}
                <Typography variant="caption" display="block" sx={{ mt: 0.75 }}>
                  修改运单、电话或原因后需重新检查。
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
                    <AccordionSummary expandIcon={<ExpandMoreRoundedIcon />} sx={{ px: 0 }}><Typography variant="caption">处理编号</Typography></AccordionSummary>
                    <AccordionDetails sx={{ px: 0 }}><Typography component="code" variant="caption">#{numberValue(resultRecord.jobId)}</Typography></AccordionDetails>
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
                  >检查是否可取消</Button>
                  <Button
                    color="error"
                    variant="contained"
                    disabled={!cancelPreview?.result.cancelAllowed || !cancelPreview.result.confirmToken || cancelPreview.fingerprint !== currentCancelFingerprint || busy}
                    startIcon={cancelConfirmMutation.isPending ? <CircularProgress color="inherit" size={16} /> : undefined}
                    onClick={() => cancelConfirmMutation.mutate()}
                  >确认申请取消</Button>
                </>
              ) : action !== 'none' ? (
                <Button
                  variant={action === 'work_order' ? 'contained' : 'outlined'}
                  color={action === 'work_order' ? 'primary' : 'inherit'}
                  disabled={Boolean(disabledReason) || busy}
                  startIcon={actionMutation.isPending ? <CircularProgress color="inherit" size={16} /> : undefined}
                  onClick={() => actionMutation.mutate()}
                >
                  {action === 'waybill_lookup' ? '查询运单' : action === 'work_order' ? '创建催派工单' : '更新联系号码'}
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
      <SectionHeading title="任务摘要" />
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
  const [isLoadingOlderMessages, setIsLoadingOlderMessages] = useState(false)
  const [historyError, setHistoryError] = useState<unknown>(null)
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

  const threadPath = selectedItem?.source_links.conversation || ''
  const threadQueryKey = useMemo(
    () => ['operatorWorkspaceThread', selectedItem?.queue_id ?? null, threadPath] as const,
    [selectedItem?.queue_id, threadPath],
  )
  const thread = useQuery({
    queryKey: threadQueryKey,
    queryFn: () => operatorWorkspaceApi.conversationThread(threadPath),
    enabled: Boolean(threadPath),
    retry: false,
  })
  const sourceRecord = useQuery({
    queryKey: ['operatorWorkspaceSourceRecord', selectedItem?.queue_id, selectedItem?.source_links.ticket],
    queryFn: () => operatorWorkspaceApi.sourceRecord(selectedItem?.source_links.ticket || ''),
    enabled: Boolean(selectedItem?.source_links.ticket && !selectedItem?.source_links.conversation),
    retry: false,
  })

  useEffect(() => {
    setHistoryError(null)
    setIsLoadingOlderMessages(false)
  }, [threadPath])

  const refreshThreadSnapshot = useCallback(async () => {
    if (!threadPath) return
    const latest = await operatorWorkspaceApi.conversationThread(threadPath)
    queryClient.setQueryData<OperatorWorkspaceThread>(threadQueryKey, (current) => mergeLatestThread(current, latest))
  }, [queryClient, threadPath, threadQueryKey])

  const loadOlderMessages = useCallback(async () => {
    const beforeMessageId = thread.data?.message_page?.before_id
    if (!threadPath || !beforeMessageId || isLoadingOlderMessages) return
    setHistoryError(null)
    setIsLoadingOlderMessages(true)
    try {
      const older = await operatorWorkspaceApi.conversationThread(threadPath, { beforeMessageId })
      queryClient.setQueryData<OperatorWorkspaceThread>(threadQueryKey, (current) => mergeOlderThread(current, older))
    } catch (error) {
      setHistoryError(error)
    } finally {
      setIsLoadingOlderMessages(false)
    }
  }, [isLoadingOlderMessages, queryClient, thread.data?.message_page?.before_id, threadPath, threadQueryKey])

  useEffect(() => {
    const ticketId = selectedItem?.ticket_id
    if (!ticketId || !threadPath || !thread.isSuccess) return undefined

    const controller = new AbortController()
    let stopped = false
    let afterId = Math.max(0, Number(thread.data?.last_event_id ?? 0))
    let failureCount = 0
    const wait = (milliseconds: number) => new Promise((resolve) => window.setTimeout(resolve, milliseconds))

    const run = async () => {
      while (!stopped) {
        try {
          const page = await operatorWorkspaceApi.conversationEvents(ticketId, afterId, { signal: controller.signal })
          failureCount = 0
          afterId = Math.max(afterId, Number(page.last_event_id || 0))
          if (page.events.length) {
            await refreshThreadSnapshot()
            await queryClient.invalidateQueries({ queryKey: ['operatorWorkspaceQueue'] })
          }
          if (page.has_more) continue
          await wait(EVENT_IDLE_POLL_MS)
        } catch {
          if (stopped || controller.signal.aborted) return
          failureCount += 1
          const retryDelay = Math.min(EVENT_RETRY_MAX_MS, EVENT_RETRY_BASE_MS * (2 ** Math.min(failureCount - 1, 5)))
          await wait(retryDelay)
        }
      }
    }

    void run()
    return () => {
      stopped = true
      controller.abort()
    }
  }, [queryClient, refreshThreadSnapshot, selectedItem?.ticket_id, thread.data?.last_event_id, thread.isSuccess, threadPath])

  const refreshSelected = useCallback(async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['operatorWorkspaceQueue'] }),
      threadPath ? refreshThreadSnapshot() : Promise.resolve(),
      queryClient.invalidateQueries({ queryKey: ['operatorWorkspaceSourceRecord', selectedItem?.queue_id] }),
    ])
  }, [queryClient, refreshThreadSnapshot, selectedItem?.queue_id, threadPath])

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

      {session.isError ? <ErrorNotice title="无法读取账号" error={session.error} fallback="请重新登录" /> : null}
      {session.data && !canReadQueue ? <Alert severity="warning" variant="outlined">无权访问任务队列，请联系管理员。</Alert> : null}

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
                  title="无法读取任务"
                  error={queue.error}
                  fallback="请重新加载"
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
            aria-label="当前任务"
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
                      <AlertTitle>任务已离开待处理列表</AlertTitle>
                      回复草稿已保留，操作已暂停。
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
                  historyError={historyError}
                  isLoadingOlderMessages={isLoadingOlderMessages}
                  capabilities={capabilities}
                  onRefresh={refreshSelected}
                  onLoadOlderMessages={loadOlderMessages}
                  onReplyDirtyChange={setReplyDraftDirty}
                  selectionUnavailable={preserveMissingSelection}
                  sx={{ display: { xs: conversationVisible ? 'block' : 'none', lg: 'block' }, mt: { lg: 3 }, pt: { lg: 3 }, borderTop: { lg: 1 }, borderColor: { lg: 'divider' } }}
                />
              </>
            ) : <EmptyState title="选择一个任务" description="从待处理任务中选择" />}
          </Paper>

          <Paper
            component="aside"
            aria-label="任务操作与结果"
            variant="outlined"
            sx={{ display: { xs: mobileView === 'actions' ? 'block' : 'none', lg: 'block' }, minWidth: 0, p: 2, alignSelf: 'start', position: { lg: 'sticky' }, top: { lg: 84 } }}
          >
            {selectedItem ? (
              <Stack spacing={2.5}>
                <Box>
                  <Typography component="h2" variant="h3">当前任务</Typography>
                  <Typography variant="subtitle1" sx={{ mt: 1 }}>
                    {sanitizeDisplayText(memory?.required_action || memory?.next_actions?.[0]?.label || '核实信息并选择下一步')}
                  </Typography>
                </Box>
                {preserveMissingSelection ? <Alert severity="warning">任务已离开待处理列表，操作已暂停。</Alert> : <ActionPanel item={selectedItem} thread={thread.data ?? null} capabilities={capabilities} onRefresh={refreshSelected} />}
              </Stack>
            ) : <EmptyState title="暂无操作" description="请先选择任务" />}
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
          <DialogContentText id="reply-discard-description">切换任务后，未发送回复将丢失。</DialogContentText>
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

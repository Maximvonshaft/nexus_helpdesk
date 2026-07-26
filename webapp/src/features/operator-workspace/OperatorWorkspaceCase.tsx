import {
  Alert, AlertTitle, Box, Divider, Paper, Stack, Typography,
} from '@mui/material'
import {
  OperatorEmptyState,
  OperatorFactGrid,
  OperatorSectionHeading,
  OperatorStatusLine,
  OperatorTechnicalDisclosure,
} from '@/app/OperatorPresentation'
import type { OperatorWorkspaceThread } from '@/lib/operatorWorkspaceApi'
import type { UnifiedOperatorQueueItem, WorkspaceMobileView } from '@/lib/operatorWorkspaceTypes'
import {
  evidencePresentation,
  ownerPresentation,
  priorityPresentation,
  queueSourcePresentation,
  retryPresentation,
  slaPresentation,
  sourceStatusPresentation,
} from '@/lib/operatorWorkspacePresentation'
import type { SupportMemoryLedger } from '@/lib/types'
import type { TicketClosureReceipt } from '@/lib/ticketClosureTypes'
import { displayVerbatimText, formatDateTime, sanitizeDisplayText, stringValue } from '@/lib/format'
import { OperatorWorkspaceClosure } from './OperatorWorkspaceClosure'
import { OperatorWorkspaceConversation } from './OperatorWorkspaceConversation'
import { useTicketClosureReadiness } from './useTicketClosureReadiness'

function CaseHeader({ item, currentUserId }: { item: UnifiedOperatorQueueItem; currentUserId?: number }) {
  const source = queueSourcePresentation(item.source_type)
  const status = sourceStatusPresentation(item.source_status)
  const owner = ownerPresentation(item.owner, currentUserId)
  const sla = slaPresentation(item.sla)
  const retry = retryPresentation(item.retry)
  return (
    <Box component="header" sx={{ pb: 2.5 }}>
      <Typography variant="overline" color="text.secondary">{source.label} · {item.country_code} · {item.channel_key}</Typography>
      <Stack
        direction={{ xs: 'column', sm: 'row' }}
        spacing={2}
        sx={{ alignItems: { xs: 'stretch', sm: 'flex-start' }, justifyContent: 'space-between' }}
      >
        <Box sx={{ minWidth: 0 }}>
          <Typography component="h1" variant="h1" sx={{ overflowWrap: 'anywhere' }}>
            {displayVerbatimText(item.display_label || item.case_key, '当前任务')}
          </Typography>
          {item.display_summary ? (
            <Typography variant="body1" color="text.secondary" sx={{ mt: 0.75, overflowWrap: 'anywhere' }}>
              {displayVerbatimText(item.display_summary)}
            </Typography>
          ) : null}
        </Box>
        <Stack spacing={0.75} sx={{ minWidth: { sm: 220 } }}>
          <OperatorStatusLine presentation={status} />
          <OperatorStatusLine presentation={owner} />
          <OperatorStatusLine presentation={sla} />
          {item.source_type === 'dispatch' ? <OperatorStatusLine presentation={retry} /> : null}
          {item.reopened ? <OperatorStatusLine presentation={{ label: '已重新打开', tone: 'warning' }} /> : null}
        </Stack>
      </Stack>
      <Box sx={{ mt: 1.5 }}>
        <OperatorTechnicalDisclosure title="系统信息" compact>
          <Typography component="code" variant="caption" sx={{ overflowWrap: 'anywhere' }}>
            任务 {item.source_type}:{item.source_id}{item.case_key ? ` · 案例键 ${item.case_key}` : ''}{item.ticket_id ? ` · 工单 #${item.ticket_id}` : ' · 独立会话'}
          </Typography>
        </OperatorTechnicalDisclosure>
      </Box>
    </Box>
  )
}

type SpineTone = 'default' | 'success' | 'warning' | 'danger'

type SpineStage = {
  label: string
  value: string
  tone: SpineTone
}

function ticketClosureStage(receipt?: TicketClosureReceipt, pending = false, error?: unknown): SpineStage {
  if (pending) return { label: '结案状态', value: '正在核对服务器凭证', tone: 'default' }
  if (error || !receipt) return { label: '结案状态', value: '服务器关闭状态不可用', tone: 'danger' }
  const readiness = receipt.readiness
  const sourceClosed = receipt.ticket_status.toLowerCase() === 'closed'
  const repairRequired = readiness.blocked_reasons.includes('repair_required')
  if (sourceClosed && readiness.closure_ready) return { label: '结案状态', value: '已安全关闭', tone: 'success' }
  if (sourceClosed) return { label: '结案状态', value: '来源已关闭，安全关闭未确认', tone: 'danger' }
  if (repairRequired) return { label: '结案状态', value: '需要修复失败结果', tone: 'danger' }
  if (readiness.closure_ready) return { label: '结案状态', value: '可以安全关闭', tone: 'success' }
  if (!receipt.evidence.observation_elapsed) return { label: '结案状态', value: '观察期或其他条件未满足', tone: 'warning' }
  return { label: '结案状态', value: '关闭条件尚未满足', tone: 'warning' }
}

function serverOutcomeStage(receipt?: TicketClosureReceipt, pending = false, error?: unknown): SpineStage {
  if (pending) return { label: '操作结果', value: '正在核对服务器结果', tone: 'default' }
  if (error || !receipt) return { label: '操作结果', value: '服务器结果不可用', tone: 'danger' }
  if (receipt.readiness.blocked_reasons.includes('repair_required')) {
    return { label: '操作结果', value: '存在失败结果，需要修复', tone: 'danger' }
  }
  if (receipt.readiness.missing_outcome_levels.length) {
    return {
      label: '操作结果',
      value: `仍缺 ${receipt.readiness.missing_outcome_levels.length} 项业务结果`,
      tone: 'warning',
    }
  }
  return { label: '操作结果', value: '关闭所需结果已满足', tone: 'success' }
}

function serverNotificationStage(receipt?: TicketClosureReceipt, pending = false, error?: unknown): SpineStage {
  if (pending) return { label: '客户通知', value: '正在核对通知凭证', tone: 'default' }
  if (error || !receipt) return { label: '客户通知', value: '通知状态不可用', tone: 'danger' }
  return receipt.readiness.notification_satisfied
    ? { label: '客户通知', value: '通知要求已满足', tone: 'success' }
    : { label: '客户通知', value: '通知要求尚未满足', tone: 'warning' }
}

function CaseSpine({
  item,
  memory,
  thread,
  closureReceipt,
  closurePending,
  closureError,
  desktopLayout,
}: {
  item: UnifiedOperatorQueueItem
  memory: SupportMemoryLedger | null
  thread: OperatorWorkspaceThread | null
  closureReceipt?: TicketClosureReceipt
  closurePending: boolean
  closureError: unknown
  desktopLayout: boolean
}) {
  if (!item.ticket_id) {
    const handoffStatus = thread?.handoff?.status || item.source_status
    const stages: SpineStage[] = [
      { label: '范围', value: `${item.country_code} · ${item.channel_key}`, tone: 'default' },
      { label: '会话', value: thread?.status === 'closed' ? '已结束' : '进行中', tone: thread ? 'default' : 'warning' },
      { label: '自动处理', value: thread?.ai_suspended ? '已暂停' : sanitizeDisplayText(thread?.ai_status || '未处理'), tone: thread?.ai_suspended ? 'warning' : 'default' },
      { label: '人工转接', value: handoffStatus === 'accepted' ? '人工处理中' : handoffStatus === 'requested' ? '等待人工' : sanitizeDisplayText(handoffStatus), tone: handoffStatus === 'requested' ? 'warning' : 'default' },
      { label: '工单', value: '未创建', tone: 'default' },
      { label: '会话结果', value: thread?.outcome ? sanitizeDisplayText(thread.outcome) : '尚未结束', tone: thread?.outcome ? 'default' : 'warning' },
    ]
    return <SpineSurface label="会话进度" stages={stages} desktopLayout={desktopLayout} />
  }

  const timeline = memory?.evidence_timeline ?? []
  const latestHumanDecision = [...timeline].reverse().find((entry) => evidencePresentation(entry).evidenceClass === 'human')
  const nextAction = memory?.required_action || memory?.next_actions?.[0]?.label || ''
  const stages: SpineStage[] = [
    { label: '范围', value: `${item.country_code} · ${item.channel_key}`, tone: 'default' },
    { label: '已知信息', value: timeline.length ? `${timeline.length} 条可查看记录` : '尚无结构化信息', tone: timeline.length ? 'default' : 'warning' },
    {
      label: '处理决定',
      value: latestHumanDecision ? sanitizeDisplayText(latestHumanDecision.label || latestHumanDecision.kind) : '尚未记录人工决定',
      tone: latestHumanDecision ? 'default' : 'warning',
    },
    { label: '下一步', value: nextAction ? sanitizeDisplayText(nextAction) : '等待服务器给出下一步', tone: nextAction ? 'default' : 'warning' },
    serverOutcomeStage(closureReceipt, closurePending, closureError),
    serverNotificationStage(closureReceipt, closurePending, closureError),
    ticketClosureStage(closureReceipt, closurePending, closureError),
  ]
  return <SpineSurface label="处理进度" stages={stages} desktopLayout={desktopLayout} />
}

function SpineSurface({ label, stages, desktopLayout }: { label: string; stages: SpineStage[]; desktopLayout: boolean }) {
  return (
    <Paper variant="outlined" sx={{ mb: 3, overflow: 'hidden' }} aria-label={label}>
      <Box sx={{ px: 2, py: 1.5, bgcolor: 'background.default', borderBottom: 1, borderColor: 'divider' }}>
        <Typography variant="subtitle2">{label}</Typography>
      </Box>
      <Box sx={{ display: 'grid', gridTemplateColumns: desktopLayout ? `repeat(${stages.length}, minmax(0, 1fr))` : 'minmax(0, 1fr)' }}>
        {stages.map((stage, index) => (
          <Box
            key={stage.label}
            sx={{
              borderBottom: desktopLayout || index === stages.length - 1 ? 0 : 1,
              borderColor: 'divider',
              borderRight: desktopLayout && index !== stages.length - 1 ? 1 : 0,
              minWidth: 0,
              p: 1.5,
            }}
          >
            <OperatorStatusLine presentation={{ label: stage.label, tone: stage.tone }} compact />
            <Typography variant="body2" sx={{ mt: 0.75, overflowWrap: 'anywhere' }}>{stage.value}</Typography>
          </Box>
        ))}
      </Box>
    </Paper>
  )
}

function EvidencePanel({ memory }: { memory: SupportMemoryLedger | null }) {
  const timeline = memory?.evidence_timeline ?? []
  return (
    <Box component="section" aria-labelledby="operator-evidence-title">
      <OperatorSectionHeading id="operator-evidence-title" title="已知信息" />
      <Divider sx={{ my: 2 }} />
      {!timeline.length ? <OperatorEmptyState title="暂无结构化信息" description="可查看客户沟通和转接原因" /> : null}
      <Stack divider={<Divider flexItem />}>
        {timeline.map((entry, index) => {
          const presentation = evidencePresentation(entry)
          return (
            <Box
              component="article"
              key={`${entry.kind}-${entry.source_id || index}`}
              sx={{ py: 1.75, contentVisibility: 'auto', containIntrinsicSize: '120px' }}
            >
              <Stack direction="row" spacing={2} sx={{ alignItems: 'flex-start', justifyContent: 'space-between' }}>
                <OperatorStatusLine presentation={presentation} />
                {entry.created_at ? <Typography component="time" variant="caption" color="text.disabled">{formatDateTime(entry.created_at)}</Typography> : null}
              </Stack>
              <Typography variant="subtitle2" sx={{ mt: 1 }}>{sanitizeDisplayText(entry.label || entry.kind)}</Typography>
              {entry.summary && Object.keys(entry.summary).length ? (
                <Box sx={{ mt: 1.25 }}>
                  <OperatorTechnicalDisclosure title="信息摘要">
                    <Box component="pre" sx={{ m: 0, maxHeight: 320, overflow: 'auto', whiteSpace: 'pre-wrap', fontSize: 12 }}>
                      {JSON.stringify(entry.summary, null, 2)}
                    </Box>
                  </OperatorTechnicalDisclosure>
                </Box>
              ) : null}
            </Box>
          )
        })}
      </Stack>
    </Box>
  )
}

function SourceSummary({
  data,
  item,
  desktopLayout,
}: {
  data: Record<string, unknown>
  item: UnifiedOperatorQueueItem
  desktopLayout: boolean
}) {
  const status = sourceStatusPresentation(stringValue(data.status) || item.source_status)
  const priority = priorityPresentation(stringValue(data.priority) || item.priority)
  return (
    <Box component="section" sx={{ py: 2.5 }}>
      <OperatorSectionHeading title="任务摘要" />
      <Box sx={{ mt: 2 }}>
        <OperatorFactGrid columns={desktopLayout ? 3 : 1} facts={[
          ['标题', displayVerbatimText(stringValue(data.title), '未提供')],
          ['状态', status.label],
          ['优先级', priority.label],
        ]} />
      </Box>
    </Box>
  )
}

export function WorkspaceCasePane({
  item,
  currentUserId,
  memory,
  preserveMissingSelection,
  sourceRecord,
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
  mobileView,
  desktopLayout,
}: {
  item: UnifiedOperatorQueueItem | null
  currentUserId?: number
  memory: SupportMemoryLedger | null
  preserveMissingSelection: boolean
  sourceRecord: Record<string, unknown> | null
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
  mobileView: WorkspaceMobileView
  desktopLayout: boolean
}) {
  const caseVisible = mobileView === 'case'
  const conversationVisible = mobileView === 'conversation'
  const closure = useTicketClosureReadiness(item?.ticket_id)

  return (
    <Paper
      id="workspace-case"
      component="section"
      aria-label="当前任务"
      tabIndex={-1}
      variant="outlined"
      sx={{ display: desktopLayout || caseVisible || conversationVisible ? 'block' : 'none', minWidth: 0, p: { xs: 2, md: 2.5 } }}
    >
      {item ? (
        <>
          <Box sx={{ display: desktopLayout || caseVisible ? 'block' : 'none' }}>
            <CaseHeader item={item} currentUserId={currentUserId} />
            <CaseSpine
              item={item}
              memory={memory}
              thread={thread}
              closureReceipt={closure.data}
              closurePending={closure.isPending}
              closureError={closure.error}
              desktopLayout={desktopLayout}
            />
            {preserveMissingSelection ? <Alert severity="warning" variant="outlined" sx={{ mb: 2.5 }}><AlertTitle>任务已离开待处理列表</AlertTitle>回复草稿已保留，操作已暂停。</Alert> : null}
            {sourceRecord && !thread ? <SourceSummary data={sourceRecord} item={item} desktopLayout={desktopLayout} /> : null}
            <EvidencePanel memory={memory} />
            {item.ticket_id ? (
              <Box component="section" aria-label="安全关闭" sx={{ mt: 3, pt: 3, borderTop: 1, borderColor: 'divider' }}>
                <OperatorWorkspaceClosure
                  ticketId={item.ticket_id}
                  receipt={closure.data}
                  isPending={closure.isPending}
                  isFetching={closure.isFetching}
                  queryError={closure.error}
                  onRefetch={async () => closure.refetch()}
                  onRefresh={onRefresh}
                />
              </Box>
            ) : null}
          </Box>
          <OperatorWorkspaceConversation
            item={item}
            thread={thread}
            isLoading={isLoading}
            isRefreshing={isRefreshing}
            error={error}
            historyError={historyError}
            isLoadingOlderMessages={isLoadingOlderMessages}
            capabilities={capabilities}
            onRefresh={onRefresh}
            onLoadOlderMessages={onLoadOlderMessages}
            onReplyDirtyChange={onReplyDirtyChange}
            selectionUnavailable={preserveMissingSelection}
            sx={{
              display: desktopLayout || conversationVisible ? 'block' : 'none',
              mt: desktopLayout ? 3 : 0,
              pt: desktopLayout ? 3 : 0,
              borderTop: desktopLayout ? 1 : 0,
              borderColor: 'divider',
            }}
          />
        </>
      ) : <OperatorEmptyState title="选择一个任务" description="从待处理任务中选择" />}
    </Paper>
  )
}

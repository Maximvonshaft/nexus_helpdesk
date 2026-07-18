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
  queueSourcePresentation,
  retryPresentation,
  slaPresentation,
  sourceStatusPresentation,
} from '@/lib/operatorWorkspacePresentation'
import type { SupportMemoryLedger } from '@/lib/types'
import { formatDateTime, sanitizeDisplayText, stringValue } from '@/lib/format'
import { OperatorWorkspaceConversation } from './OperatorWorkspaceConversation'

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
        <Typography component="h1" variant="h1" sx={{ minWidth: 0, overflowWrap: 'anywhere' }}>{item.case_key || item.queue_id}</Typography>
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
            任务 {item.source_type}:{item.source_id}{item.ticket_id ? ` · 工单 #${item.ticket_id}` : ''}
          </Typography>
        </OperatorTechnicalDisclosure>
      </Box>
    </Box>
  )
}

function CaseSpine({ item, memory }: { item: UnifiedOperatorQueueItem; memory: SupportMemoryLedger | null }) {
  const timeline = memory?.evidence_timeline ?? []
  const latestByClass = (value: ReturnType<typeof evidencePresentation>['evidenceClass']) => [...timeline].reverse().find((entry) => evidencePresentation(entry).evidenceClass === value)
  const decision = latestByClass('human')
  const result = latestByClass('outcome')
  const notification = latestByClass('notification')
  const nextAction = memory?.required_action || memory?.next_actions?.[0]?.label || ''
  const stages = [
    ['范围', `${item.country_code} · ${item.channel_key}`, true],
    ['已知信息', timeline.length ? `${timeline.length} 条` : '未提供', timeline.length > 0],
    ['处理决定', decision ? sanitizeDisplayText(decision.label || decision.kind) : '未提供', Boolean(decision)],
    ['下一步', nextAction ? sanitizeDisplayText(nextAction) : '未提供', Boolean(nextAction)],
    ['操作结果', result ? sanitizeDisplayText(result.label || result.kind) : '未提供', Boolean(result)],
    ['客户通知', notification ? sanitizeDisplayText(notification.label || notification.kind) : '未提供', Boolean(notification)],
    ['结案状态', '暂无可信结案信息', false],
  ] as const
  return (
    <Paper variant="outlined" sx={{ mb: 3, overflow: 'hidden' }} aria-label="处理进度">
      <Box sx={{ px: 2, py: 1.5, bgcolor: 'background.default', borderBottom: 1, borderColor: 'divider' }}><Typography variant="subtitle2">处理进度</Typography></Box>
      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: 'repeat(2, 1fr)', xl: 'repeat(7, minmax(0, 1fr))' } }}>
        {stages.map(([label, value, available], index) => (
          <Box key={label} sx={{ borderBottom: { xs: index === stages.length - 1 ? 0 : 1, xl: 0 }, borderColor: 'divider', borderRight: { xl: index === stages.length - 1 ? 0 : 1 }, minWidth: 0, p: 1.5 }}>
            <Stack direction="row" spacing={0.75} sx={{ alignItems: 'center' }}><Box aria-hidden="true" sx={{ bgcolor: available ? 'primary.main' : 'divider', borderRadius: '50%', height: 8, width: 8 }} /><Typography variant="caption" color="text.secondary" sx={{ fontWeight: 650 }}>{label}</Typography></Stack>
            <Typography variant="body2" sx={{ mt: 0.75, overflowWrap: 'anywhere' }}>{value}</Typography>
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
      {!timeline.length ? <OperatorEmptyState title="暂无结构化信息" description="可查看任务摘要和客户沟通" /> : null}
      <Stack divider={<Divider flexItem />}>
        {timeline.map((entry, index) => {
          const presentation = evidencePresentation(entry)
          return (
            <Box component="article" key={`${entry.kind}-${entry.source_id || index}`} sx={{ py: 1.75 }}>
              <Stack direction="row" spacing={2} sx={{ alignItems: 'flex-start', justifyContent: 'space-between' }}>
                <OperatorStatusLine presentation={presentation} />
                {entry.created_at ? <Typography component="time" variant="caption" color="text.disabled">{formatDateTime(entry.created_at)}</Typography> : null}
              </Stack>
              <Typography variant="subtitle2" sx={{ mt: 1 }}>{sanitizeDisplayText(entry.label || entry.kind)}</Typography>
              {entry.summary && Object.keys(entry.summary).length ? (
                <Box sx={{ mt: 1.25 }}>
                  <OperatorTechnicalDisclosure title="信息摘要">
                    <Box component="pre" sx={{ m: 0, maxHeight: 320, overflow: 'auto', whiteSpace: 'pre-wrap', fontSize: 12 }}>{JSON.stringify(entry.summary, null, 2)}</Box>
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

function SourceSummary({ data, item }: { data: Record<string, unknown>; item: UnifiedOperatorQueueItem }) {
  return (
    <Box component="section" sx={{ py: 2.5 }}>
      <OperatorSectionHeading title="任务摘要" />
      <Box sx={{ mt: 2 }}>
        <OperatorFactGrid columns={3} facts={[
          ['标题', sanitizeDisplayText(stringValue(data.title) || '未提供')],
          ['状态', sanitizeDisplayText(stringValue(data.status) || item.source_status)],
          ['优先级', sanitizeDisplayText(stringValue(data.priority) || item.priority)],
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
}) {
  const caseVisible = mobileView === 'case'
  const conversationVisible = mobileView === 'conversation'
  return (
    <Paper id="workspace-case" component="section" aria-label="当前任务" tabIndex={-1} variant="outlined" sx={{ display: { xs: caseVisible || conversationVisible ? 'block' : 'none', lg: 'block' }, minWidth: 0, p: { xs: 2, md: 2.5 } }}>
      {item ? (
        <>
          <Box sx={{ display: { xs: caseVisible ? 'block' : 'none', lg: 'block' } }}>
            <CaseHeader item={item} currentUserId={currentUserId} />
            <CaseSpine item={item} memory={memory} />
            {preserveMissingSelection ? <Alert severity="warning" variant="outlined" sx={{ mb: 2.5 }}><AlertTitle>任务已离开待处理列表</AlertTitle>回复草稿已保留，操作已暂停。</Alert> : null}
            {sourceRecord && !thread ? <SourceSummary data={sourceRecord} item={item} /> : null}
            <EvidencePanel memory={memory} />
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
            sx={{ display: { xs: conversationVisible ? 'block' : 'none', lg: 'block' }, mt: { lg: 3 }, pt: { lg: 3 }, borderTop: { lg: 1 }, borderColor: { lg: 'divider' } }}
          />
        </>
      ) : <OperatorEmptyState title="选择一个任务" description="从待处理任务中选择" />}
    </Paper>
  )
}

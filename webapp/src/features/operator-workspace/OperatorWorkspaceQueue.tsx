import RefreshRoundedIcon from '@mui/icons-material/RefreshRounded'
import {
  Box, Button, Chip, CircularProgress, Divider, List, ListItemButton, MenuItem,
  Paper, Stack, Tab, Tabs, TextField, Typography,
} from '@mui/material'
import {
  OperatorEmptyState,
  OperatorErrorNotice,
  OperatorLoadingState,
  OperatorSectionHeading,
  OperatorStatusLine,
  operatorToneColor,
} from '@/app/OperatorPresentation'
import type { UnifiedOperatorQueueItem, WorkspaceFilters, WorkspaceMobileView } from '@/lib/operatorWorkspaceTypes'
import {
  ownerPresentation, priorityPresentation, queueSourcePresentation, retryPresentation,
  slaPresentation, sourceStatusPresentation,
} from '@/lib/operatorWorkspacePresentation'
import { formatDateTime } from '@/lib/format'

export const workspaceMobileViews: Array<{ value: WorkspaceMobileView; label: string }> = [
  { value: 'queue', label: '待处理' },
  { value: 'case', label: '任务详情' },
  { value: 'conversation', label: '客户沟通' },
  { value: 'actions', label: '操作' },
]

function QueueFilters({ filters, onChange }: { filters: WorkspaceFilters; onChange: (filters: WorkspaceFilters) => void }) {
  return (
    <Box aria-label="任务筛选" sx={{ display: 'grid', gap: 1.25, gridTemplateColumns: { xs: '1fr 1fr', md: 'repeat(5, minmax(0, 1fr))', lg: '1fr' } }}>
      <TextField select label="状态" value={filters.state} onChange={(event) => onChange({ ...filters, state: event.target.value as WorkspaceFilters['state'] })}>
        <MenuItem value="active">需要处理</MenuItem><MenuItem value="terminal">来源已结束</MenuItem><MenuItem value="all">全部</MenuItem>
      </TextField>
      <TextField select label="任务类型" value={filters.sourceType} onChange={(event) => onChange({ ...filters, sourceType: event.target.value as WorkspaceFilters['sourceType'] })}>
        <MenuItem value="all">全部类型</MenuItem><MenuItem value="handoff">待接手</MenuItem><MenuItem value="ticket">客服工单</MenuItem><MenuItem value="dispatch">内部任务</MenuItem>
      </TextField>
      <TextField select label="当前负责人" value={filters.owner} onChange={(event) => onChange({ ...filters, owner: event.target.value as WorkspaceFilters['owner'] })}>
        <MenuItem value="any">全部负责人</MenuItem><MenuItem value="mine">我的</MenuItem><MenuItem value="unassigned">未分配</MenuItem><MenuItem value="team">我的团队</MenuItem>
      </TextField>
      <TextField select label="处理时限" value={filters.sla} onChange={(event) => onChange({ ...filters, sla: event.target.value as WorkspaceFilters['sla'] })}>
        <MenuItem value="any">全部时限</MenuItem><MenuItem value="breached">已超时</MenuItem><MenuItem value="at_risk">即将超时</MenuItem><MenuItem value="stale">长期未更新</MenuItem><MenuItem value="paused">已暂停</MenuItem><MenuItem value="healthy">正常</MenuItem><MenuItem value="unavailable">未知</MenuItem>
      </TextField>
      <TextField select label="排序" value={filters.sort} onChange={(event) => onChange({ ...filters, sort: event.target.value as WorkspaceFilters['sort'] })}>
        <MenuItem value="oldest">最早待办优先</MenuItem><MenuItem value="newest">最新更新优先</MenuItem>
      </TextField>
    </Box>
  )
}

function QueueRow({ item, active, currentUserId, onSelect }: { item: UnifiedOperatorQueueItem; active: boolean; currentUserId?: number; onSelect: () => void }) {
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
        alignItems: 'stretch', borderBottom: 1, borderColor: 'divider', display: 'block', px: 1.5, py: 1.5,
        textAlign: 'left', width: '100%',
        '&.Mui-selected': { bgcolor: 'action.selected', boxShadow: (theme) => `inset 3px 0 0 ${theme.palette.primary.main}` },
        '&.Mui-selected:hover': { bgcolor: 'action.selected' },
      }}
    >
      <Stack spacing={0.75}>
        <Stack direction="row" spacing={1} sx={{ alignItems: 'flex-start', justifyContent: 'space-between' }}>
          <Typography variant="subtitle2" sx={{ overflowWrap: 'anywhere' }}>{item.case_key || item.queue_id}</Typography>
          {priority.tone === 'danger' || priority.tone === 'warning' ? <Chip color={operatorToneColor(priority.tone)} label={priority.label} size="small" /> : null}
        </Stack>
        <Typography variant="caption" color="text.secondary">{source.label} · {item.country_code} · {item.channel_key}{item.reopened ? ' · 已重新打开' : ''}</Typography>
        <Stack direction="row" spacing={2} useFlexGap sx={{ flexWrap: 'wrap' }}><OperatorStatusLine presentation={owner} compact /><OperatorStatusLine presentation={sla} compact /></Stack>
        {item.source_type === 'dispatch' ? <Typography variant="caption" color="text.secondary">{retry.label}</Typography> : null}
        <Typography variant="caption" color="text.secondary">{sourceStatus.label}</Typography>
        <Typography component="time" variant="caption" color="text.disabled">{formatDateTime(item.updated_at)}</Typography>
      </Stack>
    </ListItemButton>
  )
}

export function WorkspaceMobileTabs({ value, onChange }: { value: WorkspaceMobileView; onChange: (value: WorkspaceMobileView) => void }) {
  return (
    <Tabs value={value} onChange={(_, next: WorkspaceMobileView) => onChange(next)} aria-label="移动端工作区" variant="scrollable" allowScrollButtonsMobile sx={{ display: { xs: 'flex', lg: 'none' }, mb: 1.5, borderBottom: 1, borderColor: 'divider' }}>
      {workspaceMobileViews.map((view) => <Tab key={view.value} value={view.value} label={view.label} />)}
    </Tabs>
  )
}

export function WorkspaceQueuePane({
  filters, onFiltersChange, error, onRetry, items, selectedQueueId, currentUserId,
  isLoading, isRefreshing, hasNextPage, isFetchingNextPage, onSelect, onLoadMore, visible,
}: {
  filters: WorkspaceFilters
  onFiltersChange: (filters: WorkspaceFilters) => void
  error: unknown
  onRetry: () => void
  items: UnifiedOperatorQueueItem[]
  selectedQueueId: string | null
  currentUserId?: number
  isLoading: boolean
  isRefreshing: boolean
  hasNextPage: boolean
  isFetchingNextPage: boolean
  onSelect: (item: UnifiedOperatorQueueItem) => void
  onLoadMore: () => void
  visible: boolean
}) {
  return (
    <Paper id="workspace-queue" component="aside" tabIndex={-1} variant="outlined" sx={{ display: { xs: visible ? 'block' : 'none', lg: 'block' }, minHeight: 0, p: 1.5 }}>
      <Stack spacing={2}>
        <QueueFilters filters={filters} onChange={onFiltersChange} />
        {error ? <OperatorErrorNotice title="无法读取任务" error={error} fallback="请重新加载" action={<Button color="inherit" size="small" startIcon={<RefreshRoundedIcon />} onClick={onRetry}>重新加载</Button>} /> : null}
        <Box component="section" aria-label="待处理任务" aria-busy={isLoading} sx={{ minHeight: 0 }}>
          <OperatorSectionHeading title="待处理任务" action={isRefreshing ? <CircularProgress size={18} aria-label="刷新中" /> : null} />
          <Divider sx={{ mt: 2 }} />
          {isLoading ? <OperatorLoadingState label="正在读取任务…" /> : null}
          {!isLoading && !items.length ? <OperatorEmptyState title="暂无待处理任务" description="请调整筛选或刷新" /> : null}
          <List disablePadding sx={{ maxHeight: { lg: 'calc(100dvh - 360px)' }, overflowY: 'auto' }}>
            {items.map((item) => <QueueRow key={item.queue_id} item={item} active={item.queue_id === selectedQueueId} currentUserId={currentUserId} onSelect={() => onSelect(item)} />)}
          </List>
          {hasNextPage ? <Button fullWidth color="inherit" disabled={isFetchingNextPage} startIcon={isFetchingNextPage ? <CircularProgress color="inherit" size={16} /> : undefined} onClick={onLoadMore} sx={{ mt: 1.5 }}>{isFetchingNextPage ? '加载中…' : '加载更多任务'}</Button> : null}
        </Box>
      </Stack>
    </Paper>
  )
}

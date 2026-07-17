import ExpandMoreRoundedIcon from '@mui/icons-material/ExpandMoreRounded'
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  AlertTitle,
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Divider,
  MenuItem,
  Paper,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { OperatorEmptyState, OperatorErrorNotice } from '@/app/OperatorPresentation'
import { supportApi } from '@/lib/supportApi'
import { operatorWorkspaceApi } from '@/lib/operatorWorkspaceApi'
import type { OperatorWorkspaceThread } from '@/lib/operatorWorkspaceApi'
import type {
  UnifiedOperatorQueueItem,
  WorkspaceFilters,
  WorkspaceMobileView,
  WorkspaceScope,
} from '@/lib/operatorWorkspaceTypes'
import { outcomePresentation } from '@/lib/operatorWorkspacePresentation'
import type { SupportMemoryLedger } from '@/lib/types'
import type { SpeedafCancelPreviewResponse } from '@/lib/speedafTypes'
import { useSession } from '@/hooks/useAuth'
import { sanitizeDisplayText } from '@/lib/format'
import {
  hasWorkspaceCapability,
  safeWorkspaceRecord,
  workspaceNumber,
  workspaceText,
  WorkspaceSectionHeading,
} from './OperatorWorkspaceCommon'
import { WorkspaceCasePane } from './OperatorWorkspaceCase'
import { WorkspaceMobileTabs, WorkspaceQueuePane } from './OperatorWorkspaceQueue'
import {
  cancelPreviewFingerprint,
  initialWorkspaceQueueId,
  initialWorkspaceSessionKey,
  mergeLatestWorkspaceThread,
  mergeOlderWorkspaceThread,
} from './operatorWorkspaceState'

type SpeedafActionKind = 'none' | 'waybill_lookup' | 'work_order' | 'address_update' | 'cancel'
type ActionResultEnvelope = { kind: SpeedafActionKind; result: Record<string, unknown> }
type CancelPreviewBinding = { fingerprint: string; result: SpeedafCancelPreviewResponse }

const defaultFilters: WorkspaceFilters = {
  state: 'active',
  sourceType: 'all',
  owner: 'any',
  priority: 'all',
  sla: 'any',
  retry: 'any',
  sort: 'oldest',
}

const EVENT_IDLE_POLL_MS = 4_000
const EVENT_RETRY_BASE_MS = 1_000
const EVENT_RETRY_MAX_MS = 30_000

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
  if (action === 'work_order' && !hasWorkspaceCapability(capabilities, 'tool:speedaf.work_order.create:write')) return '无权创建催派工单'
  if (action === 'address_update' && !hasWorkspaceCapability(capabilities, 'tool:speedaf.order.update_address:write')) return '无权更新联系号码'
  if (action === 'cancel' && !hasWorkspaceCapability(capabilities, 'tool:speedaf.order.cancel:write')) return '无权申请取消'
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
  const currentCancelFingerprint = cancelPreviewFingerprint(item.ticket_id, waybill, caller, reasonCode)

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
        waybillCode: waybill.trim().toUpperCase(), callerID: caller.trim(), reasonCode,
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
  const candidates = Array.isArray(resultRecord.candidates) ? resultRecord.candidates.map(safeWorkspaceRecord) : []
  const handoff = thread?.handoff
  const handoffAllowed = hasWorkspaceCapability(capabilities, 'webchat.handoff.accept', 'webchat.handoff.force_takeover', 'webchat.handoff.release', 'webchat.handoff.resume_ai')

  return (
    <Box id="workspace-actions" component="section" aria-labelledby="operator-actions-title" tabIndex={-1}>
      <WorkspaceSectionHeading id="operator-actions-title" title="下一步" />
      <Divider sx={{ my: 2 }} />
      <Stack spacing={2.5}>
        {(handoff?.can_accept || handoff?.can_force_takeover || handoff?.can_decline || handoff?.can_release || handoff?.can_resume_ai) ? (
          <Box>
            <Typography component="h3" variant="subtitle1">接手任务</Typography>
            <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ mt: 1 }}>
              {handoff?.can_accept || handoff?.can_force_takeover ? <Button variant="contained" disabled={!handoffAllowed || handoffMutation.isPending} startIcon={handoffMutation.isPending ? <CircularProgress color="inherit" size={16} /> : undefined} onClick={() => handoffMutation.mutate(handoff?.can_accept ? 'accept' : 'force')}>接手处理</Button> : null}
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
            <TextField select label="选择操作" value={action} onChange={(event) => { setAction(event.target.value as SpeedafActionKind); invalidateCancelPreview(); actionMutation.reset(); cancelConfirmMutation.reset() }}>
              <MenuItem value="none">请选择操作</MenuItem><MenuItem value="waybill_lookup">按电话查询运单</MenuItem><MenuItem value="work_order">创建催派工单</MenuItem><MenuItem value="address_update">更新联系号码</MenuItem><MenuItem value="cancel">申请取消订单</MenuItem>
            </TextField>
            {action !== 'none' ? (
              <>
                {action !== 'waybill_lookup' ? <TextField label="运单" required value={waybill} onChange={(event) => { setWaybill(event.target.value.toUpperCase()); invalidateCancelPreview() }} autoComplete="off" /> : null}
                <TextField label="客户电话" required type="tel" value={caller} onChange={(event) => { setCaller(event.target.value); invalidateCancelPreview() }} autoComplete="off" />
                {action === 'waybill_lookup' ? <TextField label="国家代码" required value={countryCode} onChange={(event) => setCountryCode(event.target.value.toUpperCase())} /> : null}
                {action === 'work_order' ? <TextField label="催派说明" required value={description} onChange={(event) => setDescription(event.target.value)} multiline minRows={3} /> : null}
                {action === 'address_update' ? <TextField label="确认后的联系号码" required type="tel" value={whatsappPhone} onChange={(event) => setWhatsappPhone(event.target.value)} /> : null}
                {action === 'cancel' ? <TextField select label="取消原因" required value={reasonCode} onChange={(event) => { setReasonCode(event.target.value); invalidateCancelPreview() }}><MenuItem value="CC01">派送太慢</MenuItem><MenuItem value="CC02">快递员服务问题</MenuItem><MenuItem value="CC03">不支持验货</MenuItem><MenuItem value="CC04">不支持部分签收</MenuItem><MenuItem value="CC05">其他原因</MenuItem></TextField> : null}
              </>
            ) : null}
            {disabledReason ? <Alert severity="info" variant="outlined">{disabledReason}</Alert> : null}
            {actionError ? <OperatorErrorNotice title="操作失败" error={actionError} fallback="请稍后重试" /> : null}
            {candidates.length ? (
              <Paper variant="outlined" sx={{ p: 1.5 }}>
                <Typography variant="subtitle2">候选运单</Typography>
                <Stack divider={<Divider flexItem />} sx={{ mt: 1 }}>
                  {candidates.map((candidate) => <Stack key={workspaceText(candidate.waybillCode)} direction="row" spacing={1} alignItems="center" justifyContent="space-between" sx={{ py: 1 }}><Typography component="code" variant="body2">{sanitizeDisplayText(workspaceText(candidate.waybillCode))}</Typography><Button size="small" color="inherit" variant="outlined" onClick={() => { setWaybill(workspaceText(candidate.waybillCode)); setAction('work_order'); invalidateCancelPreview() }}>填入催派</Button></Stack>)}
                </Stack>
              </Paper>
            ) : null}
            {cancelPreview ? <Alert severity={cancelPreview.result.cancelAllowed ? 'info' : 'warning'} variant="outlined" role="status"><AlertTitle>{cancelPreview.result.cancelAllowed ? '可以申请取消' : '当前不可取消'}</AlertTitle>{sanitizeDisplayText(cancelPreview.result.currentStatusLabel || cancelPreview.result.reasonLabel || '未返回原因')}<Typography variant="caption" display="block" sx={{ mt: 0.75 }}>修改运单、电话或原因后需重新检查。</Typography></Alert> : null}
            {resultPresentation ? <Alert severity={resultPresentation.tone === 'danger' ? 'error' : resultPresentation.tone === 'warning' ? 'warning' : resultPresentation.tone === 'success' ? 'success' : 'info'} variant="outlined" role="status"><AlertTitle>{resultPresentation.label}</AlertTitle>{resultPresentation.detail}{workspaceNumber(resultRecord.jobId) ? <Accordion disableGutters elevation={0} sx={{ mt: 1, bgcolor: 'transparent', '&:before': { display: 'none' } }}><AccordionSummary expandIcon={<ExpandMoreRoundedIcon />} sx={{ px: 0 }}><Typography variant="caption">处理编号</Typography></AccordionSummary><AccordionDetails sx={{ px: 0 }}><Typography component="code" variant="caption">#{workspaceNumber(resultRecord.jobId)}</Typography></AccordionDetails></Accordion> : null}</Alert> : null}
            <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
              {action === 'cancel' ? <><Button color="inherit" variant="outlined" disabled={Boolean(disabledReason) || busy} startIcon={cancelPreviewMutation.isPending ? <CircularProgress color="inherit" size={16} /> : undefined} onClick={() => cancelPreviewMutation.mutate()}>检查是否可取消</Button><Button color="error" variant="contained" disabled={!cancelPreview?.result.cancelAllowed || !cancelPreview.result.confirmToken || cancelPreview.fingerprint !== currentCancelFingerprint || busy} startIcon={cancelConfirmMutation.isPending ? <CircularProgress color="inherit" size={16} /> : undefined} onClick={() => cancelConfirmMutation.mutate()}>确认申请取消</Button></> : action !== 'none' ? <Button variant={action === 'work_order' ? 'contained' : 'outlined'} color={action === 'work_order' ? 'primary' : 'inherit'} disabled={Boolean(disabledReason) || busy} startIcon={actionMutation.isPending ? <CircularProgress color="inherit" size={16} /> : undefined} onClick={() => actionMutation.mutate()}>{action === 'waybill_lookup' ? '查询运单' : action === 'work_order' ? '创建催派工单' : '更新联系号码'}</Button> : null}
            </Stack>
          </Stack>
        </Box>
      </Stack>
    </Box>
  )
}

export function OperatorWorkspacePage({ scope }: { scope: WorkspaceScope }) {
  const queryClient = useQueryClient()
  const session = useSession()
  const capabilities = useMemo(() => new Set(session.data?.capabilities ?? []), [session.data?.capabilities])
  const [filters, setFilters] = useState<WorkspaceFilters>(defaultFilters)
  const [selectedQueueId, setSelectedQueueId] = useState<string | null>(() => initialWorkspaceQueueId())
  const [requestedSessionKey] = useState<string | null>(() => initialWorkspaceSessionKey())
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
    const protectDraft = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', protectDraft)
    return () => window.removeEventListener('beforeunload', protectDraft)
  }, [replyDraftDirty])
  useLayoutEffect(() => {
    const targetId: Record<WorkspaceMobileView, string> = { queue: 'workspace-queue', case: 'workspace-case', conversation: 'workspace-conversation', actions: 'workspace-actions' }
    document.getElementById(targetId[mobileView])?.focus({ preventScroll: true })
  }, [mobileView])
  useEffect(() => {
    const url = new URL(window.location.href)
    if (selectedQueueId) { url.searchParams.set('queue', selectedQueueId); url.searchParams.delete('session') }
    else { url.searchParams.delete('queue'); if (!requestedSessionKey) url.searchParams.delete('session') }
    window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`)
  }, [requestedSessionKey, selectedQueueId])

  const canReadQueue = hasWorkspaceCapability(capabilities, 'operator_queue.read')
  const requestedResolution = useQuery({
    queryKey: ['operatorWorkspaceSessionResolve', scope, requestedSessionKey],
    queryFn: () => supportApi.resolveSupportConversation(requestedSessionKey || ''),
    enabled: Boolean(session.data && canReadQueue && requestedSessionKey),
    retry: false,
  })
  const requestedQueueId = useMemo(() => {
    const conversation = requestedResolution.data?.conversation
    if (conversation?.handoff_request_id) return `handoff:${conversation.handoff_request_id}`
    if (conversation?.ticket_id) return `ticket:${conversation.ticket_id}`
    return null
  }, [requestedResolution.data?.conversation])
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
  const resolvingSessionDeepLink = Boolean(requestedSessionKey && !requestedResolution.isError && (requestedResolution.isLoading || (requestedQueueId && !requestedQueueItem && (queue.isLoading || queue.hasNextPage || queue.isFetchingNextPage))))
  const selectedQueueItemMissing = Boolean(selectedQueueId && !selectedQueueItem && retainedSelectedItem?.queue_id === selectedQueueId)
  const preserveMissingSelection = replyDraftDirty && selectedQueueItemMissing
  const selectedItem = selectedQueueItem ?? (preserveMissingSelection ? retainedSelectedItem : null) ?? requestedQueueItem ?? (resolvingSessionDeepLink ? null : queueItems[0] ?? null)

  useEffect(() => { if (selectedQueueItem) setRetainedSelectedItem(selectedQueueItem) }, [selectedQueueItem])
  useEffect(() => {
    if (requestedQueueItem && selectedQueueId !== requestedQueueItem.queue_id) setSelectedQueueId(requestedQueueItem.queue_id)
    else if (!selectedQueueId && selectedItem && !resolvingSessionDeepLink) setSelectedQueueId(selectedItem.queue_id)
    else if (selectedQueueId && !selectedQueueItem && !replyDraftDirty) setSelectedQueueId(queueItems[0]?.queue_id ?? null)
  }, [queueItems, replyDraftDirty, requestedQueueItem, resolvingSessionDeepLink, selectedItem, selectedQueueId, selectedQueueItem])
  useEffect(() => { if (requestedQueueId && !requestedQueueItem && queue.hasNextPage && !queue.isFetchingNextPage) void queue.fetchNextPage() }, [queue, requestedQueueId, requestedQueueItem])

  const threadPath = selectedItem?.source_links.conversation || ''
  const threadQueryKey = useMemo(() => ['operatorWorkspaceThread', selectedItem?.queue_id ?? null, threadPath] as const, [selectedItem?.queue_id, threadPath])
  const thread = useQuery({ queryKey: threadQueryKey, queryFn: () => operatorWorkspaceApi.conversationThread(threadPath), enabled: Boolean(threadPath), retry: false })
  const sourceRecord = useQuery({ queryKey: ['operatorWorkspaceSourceRecord', selectedItem?.queue_id, selectedItem?.source_links.ticket], queryFn: () => operatorWorkspaceApi.sourceRecord(selectedItem?.source_links.ticket || ''), enabled: Boolean(selectedItem?.source_links.ticket && !selectedItem?.source_links.conversation), retry: false })

  useEffect(() => { setHistoryError(null); setIsLoadingOlderMessages(false) }, [threadPath])
  const refreshThreadSnapshot = useCallback(async () => {
    if (!threadPath) return
    const latest = await operatorWorkspaceApi.conversationThread(threadPath)
    queryClient.setQueryData<OperatorWorkspaceThread>(threadQueryKey, (current) => mergeLatestWorkspaceThread(current, latest))
  }, [queryClient, threadPath, threadQueryKey])
  const loadOlderMessages = useCallback(async () => {
    const beforeMessageId = thread.data?.message_page?.before_id
    if (!threadPath || !beforeMessageId || isLoadingOlderMessages) return
    setHistoryError(null); setIsLoadingOlderMessages(true)
    try {
      const older = await operatorWorkspaceApi.conversationThread(threadPath, { beforeMessageId })
      queryClient.setQueryData<OperatorWorkspaceThread>(threadQueryKey, (current) => mergeOlderWorkspaceThread(current, older))
    } catch (error) { setHistoryError(error) } finally { setIsLoadingOlderMessages(false) }
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
          if (page.events.length) { await refreshThreadSnapshot(); await queryClient.invalidateQueries({ queryKey: ['operatorWorkspaceQueue'] }) }
          if (page.has_more) continue
          await wait(EVENT_IDLE_POLL_MS)
        } catch {
          if (stopped || controller.signal.aborted) return
          failureCount += 1
          await wait(Math.min(EVENT_RETRY_MAX_MS, EVENT_RETRY_BASE_MS * (2 ** Math.min(failureCount - 1, 5))))
        }
      }
    }
    void run()
    return () => { stopped = true; controller.abort() }
  }, [queryClient, refreshThreadSnapshot, selectedItem?.ticket_id, thread.data?.last_event_id, thread.isSuccess, threadPath])

  const refreshSelected = useCallback(async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['operatorWorkspaceQueue'] }),
      threadPath ? refreshThreadSnapshot() : Promise.resolve(),
      queryClient.invalidateQueries({ queryKey: ['operatorWorkspaceSourceRecord', selectedItem?.queue_id] }),
    ])
  }, [queryClient, refreshThreadSnapshot, selectedItem?.queue_id, threadPath])
  const runWithReplyDraftGuard = (next: () => void) => { if (!replyDraftDirty) return next(); pendingReplyActionRef.current = next; setReplyDiscardOpen(true) }
  const memory: SupportMemoryLedger | null = thread.data?.support_memory ?? null

  return (
    <Box component="main" data-testid="operator-workspace" sx={{ p: { xs: 1.5, md: 2 } }}>
      <WorkspaceMobileTabs value={mobileView} onChange={setMobileView} />
      {session.isError ? <OperatorErrorNotice title="无法读取账号" error={session.error} fallback="请重新登录" /> : null}
      {session.data && !canReadQueue ? <Alert severity="warning" variant="outlined">无权访问任务队列，请联系管理员。</Alert> : null}
      {session.data && canReadQueue ? (
        <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: 'minmax(0, 1fr)', lg: 'minmax(280px, 330px) minmax(0, 1fr) minmax(300px, 360px)' }, minHeight: { lg: 'calc(100dvh - 104px)' } }}>
          <WorkspaceQueuePane
            filters={filters}
            onFiltersChange={(next) => runWithReplyDraftGuard(() => { setFilters(next); setSelectedQueueId(null) })}
            error={queue.error}
            onRetry={() => { void queue.refetch() }}
            items={queueItems}
            selectedQueueId={selectedItem?.queue_id ?? null}
            currentUserId={session.data.id}
            isLoading={queue.isLoading}
            isRefreshing={queue.isFetching && !queue.isLoading}
            hasNextPage={Boolean(queue.hasNextPage)}
            isFetchingNextPage={queue.isFetchingNextPage}
            onSelect={(item) => runWithReplyDraftGuard(() => { setSelectedQueueId(item.queue_id); setMobileView('case') })}
            onLoadMore={() => { void queue.fetchNextPage() }}
            visible={mobileView === 'queue'}
          />
          <WorkspaceCasePane
            item={selectedItem}
            currentUserId={session.data.id}
            memory={memory}
            preserveMissingSelection={preserveMissingSelection}
            sourceRecord={sourceRecord.data ?? null}
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
            mobileView={mobileView}
          />
          <Paper component="aside" aria-label="任务操作与结果" variant="outlined" sx={{ display: { xs: mobileView === 'actions' ? 'block' : 'none', lg: 'block' }, minWidth: 0, p: 2, alignSelf: 'start', position: { lg: 'sticky' }, top: { lg: 84 } }}>
            {selectedItem ? <Stack spacing={2.5}><Box><Typography component="h2" variant="h3">当前任务</Typography><Typography variant="subtitle1" sx={{ mt: 1 }}>{sanitizeDisplayText(memory?.required_action || memory?.next_actions?.[0]?.label || '核实信息并选择下一步')}</Typography></Box>{preserveMissingSelection ? <Alert severity="warning">任务已离开待处理列表，操作已暂停。</Alert> : <ActionPanel item={selectedItem} thread={thread.data ?? null} capabilities={capabilities} onRefresh={refreshSelected} />}</Stack> : <OperatorEmptyState title="暂无操作" description="请先选择任务" />}
          </Paper>
        </Box>
      ) : null}

      <Dialog open={replyDiscardOpen} onClose={() => { setReplyDiscardOpen(false); pendingReplyActionRef.current = null }} aria-labelledby="reply-discard-title" aria-describedby="reply-discard-description">
        <DialogTitle id="reply-discard-title">放弃未发送的回复？</DialogTitle>
        <DialogContent><DialogContentText id="reply-discard-description">切换任务后，未发送回复将丢失。</DialogContentText></DialogContent>
        <DialogActions>
          <Button color="inherit" onClick={() => { setReplyDiscardOpen(false); pendingReplyActionRef.current = null }}>继续编辑</Button>
          <Button color="error" variant="contained" onClick={() => { const next = pendingReplyActionRef.current; pendingReplyActionRef.current = null; setReplyDiscardOpen(false); setReplyDraftDirty(false); next?.() }}>放弃回复</Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}

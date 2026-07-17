import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Paper,
  Stack,
  Typography,
} from '@mui/material'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query'
import { OperatorEmptyState, OperatorErrorNotice } from '@/app/OperatorPresentation'
import { useSession } from '@/hooks/useAuth'
import { sanitizeDisplayText } from '@/lib/format'
import { operatorWorkspaceApi } from '@/lib/operatorWorkspaceApi'
import type { OperatorWorkspaceThread } from '@/lib/operatorWorkspaceApi'
import type {
  UnifiedOperatorQueueItem,
  WorkspaceFilters,
  WorkspaceMobileView,
  WorkspaceScope,
} from '@/lib/operatorWorkspaceTypes'
import { supportApi } from '@/lib/supportApi'
import type { SupportMemoryLedger } from '@/lib/types'
import { OperatorWorkspaceActions } from './OperatorWorkspaceActions'
import { WorkspaceCasePane } from './OperatorWorkspaceCase'
import { WorkspaceMobileTabs, WorkspaceQueuePane } from './OperatorWorkspaceQueue'
import {
  hasWorkspaceCapability,
  initialWorkspaceQueueId,
  initialWorkspaceSessionKey,
  mergeLatestWorkspaceThread,
  mergeOlderWorkspaceThread,
} from './operatorWorkspaceState'

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

  const canReadQueue = hasWorkspaceCapability(capabilities, 'operator_queue.read')
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
  const selectedQueueItem = useMemo(
    () => queueItems.find((item) => item.queue_id === selectedQueueId) ?? null,
    [queueItems, selectedQueueId],
  )
  const requestedQueueItem = useMemo(
    () => queueItems.find((item) => item.queue_id === requestedQueueId) ?? null,
    [queueItems, requestedQueueId],
  )
  const resolvingSessionDeepLink = Boolean(
    requestedSessionKey
    && !requestedConversation.isError
    && (
      requestedConversation.isLoading
      || (requestedQueueId && !requestedQueueItem && (queue.isLoading || queue.hasNextPage || queue.isFetchingNextPage))
    )
  )
  const selectedQueueItemMissing = Boolean(
    selectedQueueId
    && !selectedQueueItem
    && retainedSelectedItem?.queue_id === selectedQueueId
  )
  const preserveMissingSelection = replyDraftDirty && selectedQueueItemMissing
  const selectedItem = selectedQueueItem
    ?? (preserveMissingSelection ? retainedSelectedItem : null)
    ?? requestedQueueItem
    ?? (resolvingSessionDeepLink ? null : queueItems[0] ?? null)

  useEffect(() => {
    if (selectedQueueItem) setRetainedSelectedItem(selectedQueueItem)
  }, [selectedQueueItem])
  useEffect(() => {
    if (requestedQueueItem && selectedQueueId !== requestedQueueItem.queue_id) {
      setSelectedQueueId(requestedQueueItem.queue_id)
    } else if (!selectedQueueId && selectedItem && !resolvingSessionDeepLink) {
      setSelectedQueueId(selectedItem.queue_id)
    } else if (selectedQueueId && !selectedQueueItem && !replyDraftDirty) {
      setSelectedQueueId(queueItems[0]?.queue_id ?? null)
    }
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
    queryClient.setQueryData<OperatorWorkspaceThread>(
      threadQueryKey,
      (current) => mergeLatestWorkspaceThread(current, latest),
    )
  }, [queryClient, threadPath, threadQueryKey])
  const loadOlderMessages = useCallback(async () => {
    const beforeMessageId = thread.data?.message_page?.before_id
    if (!threadPath || !beforeMessageId || isLoadingOlderMessages) return
    setHistoryError(null)
    setIsLoadingOlderMessages(true)
    try {
      const older = await operatorWorkspaceApi.conversationThread(threadPath, { beforeMessageId })
      queryClient.setQueryData<OperatorWorkspaceThread>(
        threadQueryKey,
        (current) => mergeOlderWorkspaceThread(current, older),
      )
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
          await wait(Math.min(EVENT_RETRY_MAX_MS, EVENT_RETRY_BASE_MS * (2 ** Math.min(failureCount - 1, 5))))
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
  const runWithReplyDraftGuard = (next: () => void) => {
    if (!replyDraftDirty) return next()
    pendingReplyActionRef.current = next
    setReplyDiscardOpen(true)
  }
  const memory: SupportMemoryLedger | null = thread.data?.support_memory ?? null

  return (
    <Box component="main" data-testid="operator-workspace" sx={{ p: { xs: 1.5, md: 2 } }}>
      <WorkspaceMobileTabs value={mobileView} onChange={setMobileView} />
      {session.isError ? <OperatorErrorNotice title="无法读取账号" error={session.error} fallback="请重新登录" /> : null}
      {session.data && !canReadQueue ? <Alert severity="warning" variant="outlined">无权访问任务队列，请联系管理员。</Alert> : null}
      {session.data && canReadQueue ? (
        <Box
          sx={{
            display: 'grid',
            gap: 2,
            gridTemplateColumns: {
              xs: 'minmax(0, 1fr)',
              lg: 'minmax(280px, 330px) minmax(0, 1fr) minmax(300px, 360px)',
            },
            minHeight: { lg: 'calc(100dvh - 104px)' },
          }}
        >
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
          <Paper
            component="aside"
            aria-label="任务操作与结果"
            variant="outlined"
            sx={{
              display: { xs: mobileView === 'actions' ? 'block' : 'none', lg: 'block' },
              minWidth: 0,
              p: 2,
              alignSelf: 'start',
              position: { lg: 'sticky' },
              top: { lg: 84 },
            }}
          >
            {selectedItem ? (
              <Stack spacing={2.5}>
                <Box>
                  <Typography component="h2" variant="h3">当前任务</Typography>
                  <Typography variant="subtitle1" sx={{ mt: 1 }}>
                    {sanitizeDisplayText(memory?.required_action || memory?.next_actions?.[0]?.label || '核实信息并选择下一步')}
                  </Typography>
                </Box>
                {preserveMissingSelection ? (
                  <Alert severity="warning">任务已离开待处理列表，操作已暂停。</Alert>
                ) : (
                  <OperatorWorkspaceActions
                    item={selectedItem}
                    thread={thread.data ?? null}
                    capabilities={capabilities}
                    onRefresh={refreshSelected}
                  />
                )}
              </Stack>
            ) : <OperatorEmptyState title="暂无操作" description="请先选择任务" />}
          </Paper>
        </Box>
      ) : null}

      <Dialog
        open={replyDiscardOpen}
        onClose={() => {
          setReplyDiscardOpen(false)
          pendingReplyActionRef.current = null
        }}
        aria-labelledby="reply-discard-title"
        aria-describedby="reply-discard-description"
      >
        <DialogTitle id="reply-discard-title">放弃未发送的回复？</DialogTitle>
        <DialogContent>
          <DialogContentText id="reply-discard-description">切换任务后，未发送回复将丢失。</DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button color="inherit" onClick={() => { setReplyDiscardOpen(false); pendingReplyActionRef.current = null }}>
            继续编辑
          </Button>
          <Button
            color="error"
            variant="contained"
            onClick={() => {
              const next = pendingReplyActionRef.current
              pendingReplyActionRef.current = null
              setReplyDiscardOpen(false)
              setReplyDraftDirty(false)
              next?.()
            }}
          >
            放弃回复
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}

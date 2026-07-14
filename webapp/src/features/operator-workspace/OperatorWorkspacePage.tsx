import { useEffect, useMemo, useRef, useState } from 'react'
import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { Button } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/EmptyState'
import { ErrorSummary } from '@/components/ui/ErrorSummary'
import { useLogout, useSession } from '@/hooks/useAuth'
import { ServiceAppShell } from '@/components/layout/ServiceAppShell'
import { operatorWorkspaceApi, loadWorkspaceScope, saveWorkspaceScope } from '@/lib/operatorWorkspaceApi'
import type {
  UnifiedOperatorQueueItem,
  WorkspaceFilters,
  WorkspaceMobileView,
  WorkspaceScope,
} from '@/lib/operatorWorkspaceTypes'
import { CaseOverview } from './components/CaseOverview'
import { ConversationPanel } from './components/ConversationPanel'
import { OutcomePanel } from './components/OutcomePanel'
import { QueuePanel } from './components/QueuePanel'
import { ScopeFiltersPanel } from './components/ScopeFiltersPanel'
import { ServiceActionsPanel } from './components/ServiceActionsPanel'
import { errorCopy, hasCapability, reducedMotionPreferred } from './workspaceUtils'

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
  { value: 'queue', label: '待办' },
  { value: 'case', label: '案例' },
  { value: 'conversation', label: '沟通' },
  { value: 'actions', label: '处理' },
]

function initialQueueId() {
  if (typeof window === 'undefined') return null
  return new URLSearchParams(window.location.search).get('queue')
}

export function OperatorWorkspacePage() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const logout = useLogout()
  const session = useSession()
  const capabilities = useMemo(() => new Set(session.data?.capabilities ?? []), [session.data?.capabilities])
  const [scopeDraft, setScopeDraft] = useState<WorkspaceScope>(() => loadWorkspaceScope())
  const [scope, setScope] = useState<WorkspaceScope | null>(() => {
    const initial = loadWorkspaceScope()
    return initial.tenantKey && initial.countryCode && initial.channelKey ? initial : null
  })
  const [filters, setFilters] = useState<WorkspaceFilters>(defaultFilters)
  const [selectedQueueId, setSelectedQueueId] = useState<string | null>(() => initialQueueId())
  const [mobileView, setMobileView] = useState<WorkspaceMobileView>('queue')
  const [replyDraftDirty, setReplyDraftDirty] = useState(false)
  const [discardDialogOpen, setDiscardDialogOpen] = useState(false)
  const pendingActionRef = useRef<(() => void) | null>(null)
  const [retainedSelectedItem, setRetainedSelectedItem] = useState<UnifiedOperatorQueueItem | null>(null)

  useEffect(() => {
    document.title = '客服工作台 · Nexus 客服中心'
  }, [])

  useEffect(() => {
    if (typeof window === 'undefined') return
    const url = new URL(window.location.href)
    if (selectedQueueId) url.searchParams.set('queue', selectedQueueId)
    else url.searchParams.delete('queue')
    window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`)
  }, [selectedQueueId])

  const canReadQueue = hasCapability(capabilities, 'operator_queue.read')
  const scopeReady = Boolean(scope?.tenantKey && scope.countryCode && scope.channelKey)

  const queue = useInfiniteQuery({
    queryKey: ['operatorWorkspaceQueue', scope, filters],
    queryFn: ({ pageParam }) => operatorWorkspaceApi.unifiedQueue(scope as WorkspaceScope, filters, pageParam as string | null),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor || undefined,
    enabled: Boolean(session.data && canReadQueue && scopeReady),
    retry: false,
    refetchInterval: 15000,
  })

  const queueItems = useMemo(
    () => queue.data?.pages.flatMap((page) => page.items) ?? [],
    [queue.data?.pages],
  )
  const selectedQueueItem = useMemo(
    () => queueItems.find((item) => item.queue_id === selectedQueueId) ?? null,
    [queueItems, selectedQueueId],
  )
  const selectedQueueItemMissing = Boolean(
    selectedQueueId
    && !selectedQueueItem
    && retainedSelectedItem?.queue_id === selectedQueueId,
  )
  const preserveMissingSelection = replyDraftDirty && selectedQueueItemMissing
  const selectedItem = selectedQueueItem
    ?? (preserveMissingSelection ? retainedSelectedItem : queueItems[0] ?? null)

  useEffect(() => {
    if (selectedQueueItem) setRetainedSelectedItem(selectedQueueItem)
  }, [selectedQueueItem])

  useEffect(() => {
    if (!selectedQueueId && selectedItem) {
      setSelectedQueueId(selectedItem.queue_id)
      return
    }
    if (selectedQueueId && !selectedQueueItem && !replyDraftDirty) {
      setSelectedQueueId(queueItems[0]?.queue_id ?? null)
    }
  }, [queueItems, replyDraftDirty, selectedItem, selectedQueueId, selectedQueueItem])

  const thread = useQuery({
    queryKey: ['operatorWorkspaceThread', selectedItem?.queue_id, selectedItem?.source_links.conversation],
    queryFn: () => operatorWorkspaceApi.conversationThread(selectedItem?.source_links.conversation || ''),
    enabled: Boolean(selectedItem?.source_links.conversation),
    retry: false,
    refetchInterval: selectedItem?.source_links.conversation ? 5000 : false,
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

  const runWithDraftGuard = (action: () => void) => {
    if (!replyDraftDirty) {
      action()
      return
    }
    pendingActionRef.current = action
    setDiscardDialogOpen(true)
  }

  const confirmDiscard = () => {
    const action = pendingActionRef.current
    pendingActionRef.current = null
    setDiscardDialogOpen(false)
    setReplyDraftDirty(false)
    action?.()
  }

  const applyScope = () => runWithDraftGuard(() => {
    const normalizedScope = {
      tenantKey: scopeDraft.tenantKey.trim(),
      countryCode: scopeDraft.countryCode.trim().toUpperCase(),
      channelKey: scopeDraft.channelKey.trim().toLowerCase(),
    }
    saveWorkspaceScope(normalizedScope)
    setScope(normalizedScope)
    setSelectedQueueId(null)
    setMobileView('queue')
  })

  const updateFilters = (next: WorkspaceFilters) => runWithDraftGuard(() => {
    setFilters(next)
    setSelectedQueueId(null)
  })

  const selectItem = (item: UnifiedOperatorQueueItem) => {
    if (item.queue_id === selectedItem?.queue_id) {
      setMobileView('case')
      return
    }
    runWithDraftGuard(() => {
      setSelectedQueueId(item.queue_id)
      setMobileView('case')
    })
  }

  const showMobileView = (view: WorkspaceMobileView) => {
    setMobileView(view)
    const targetId = view === 'queue'
      ? 'workspace-queue'
      : view === 'case'
        ? 'workspace-case'
        : view === 'conversation'
          ? 'workspace-conversation'
          : 'workspace-actions'
    window.requestAnimationFrame(() => {
      const target = document.getElementById(targetId)
      target?.scrollIntoView({ block: 'start', behavior: reducedMotionPreferred() ? 'auto' : 'smooth' })
      target?.focus({ preventScroll: true })
    })
  }

  const appliedScopeMatches = Boolean(
    scope
    && scope.tenantKey === scopeDraft.tenantKey.trim()
    && scope.countryCode === scopeDraft.countryCode.trim().toUpperCase()
    && scope.channelKey === scopeDraft.channelKey.trim().toLowerCase(),
  )

  if (!session.data && session.isLoading) {
    return <main className="service-entry-state"><EmptyState title="正在验证账号" description="正在加载你的客服权限和工作范围。" /></main>
  }

  if (session.isError || !session.data) {
    return (
      <main className="service-entry-state">
        <ErrorSummary
          title="无法读取当前账号"
          errors={[errorCopy(session.error, '请重新登录')]}
          action={<Button onClick={() => navigate({ to: '/login', replace: true })}>返回登录</Button>}
        />
      </main>
    )
  }

  return (
    <ServiceAppShell
      active="workspace"
      userName={session.data.display_name || session.data.username}
      capabilities={capabilities}
      title="客服工作台"
      description="从客户诉求开始，核实事实、执行处理、回复客户并确认结果。"
      meta={scope ? <span>{scope.countryCode} · {scope.channelKey}</span> : <span>未选择工作范围</span>}
      onLogout={logout}
      onNavigateRequest={runWithDraftGuard}
    >
      {!canReadQueue ? (
        <EmptyState title="当前账号不能访问客服待办" description="请联系管理员补充客服队列权限。" />
      ) : (
        <>
          <div className="workspace-mobile-tabs" role="navigation" aria-label="移动端工作区">
            {mobileViews.map((view) => (
              <button
                key={view.value}
                type="button"
                className={mobileView === view.value ? 'is-active' : ''}
                aria-pressed={mobileView === view.value}
                onClick={() => showMobileView(view.value)}
              >
                {view.label}
              </button>
            ))}
          </div>

          <div className={`customer-service-workspace is-mobile-${mobileView}`}>
            <aside id="workspace-queue" className="workspace-queue-column" tabIndex={-1}>
              <ScopeFiltersPanel
                draft={scopeDraft}
                applied={appliedScopeMatches}
                filters={filters}
                onDraftChange={setScopeDraft}
                onApply={applyScope}
                onFiltersChange={updateFilters}
              />
              {scopeReady ? (
                <>
                  {queue.isError ? (
                    <ErrorSummary
                      title="客户待办暂不可用"
                      errors={[errorCopy(queue.error, '请检查工作范围后重试')]}
                      action={<Button onClick={() => queue.refetch()}>重新加载</Button>}
                    />
                  ) : null}
                  <QueuePanel
                    items={queueItems}
                    selectedQueueId={selectedItem?.queue_id ?? null}
                    currentUserId={session.data.id}
                    loading={queue.isLoading}
                    refreshing={queue.isFetching}
                    hasNextPage={Boolean(queue.hasNextPage)}
                    loadingMore={queue.isFetchingNextPage}
                    onSelect={selectItem}
                    onLoadMore={() => queue.fetchNextPage()}
                  />
                </>
              ) : <EmptyState title="先应用工作范围" description="选择业务组织、服务国家和客户渠道后加载待办。" />}
            </aside>

            <section id="workspace-case" className="workspace-case-column" tabIndex={-1}>
              {selectedItem ? (
                <>
                  {preserveMissingSelection ? (
                    <div className="stale-selection" role="status">
                      <strong>当前案例已离开待办，回复草稿仍已保留</strong>
                      <p>案例可能已被其他客服处理或重新分配。发送回复和处理动作已暂停。</p>
                    </div>
                  ) : null}
                  <CaseOverview
                    item={selectedItem}
                    currentUserId={session.data.id}
                    thread={thread.data ?? null}
                    sourceRecord={sourceRecord.data ?? null}
                  />
                  {sourceRecord.isError ? <ErrorSummary title="案例摘要暂不可用" errors={[errorCopy(sourceRecord.error, '仍可根据待办信息继续分诊')]} /> : null}
                  <div id="workspace-conversation" tabIndex={-1}>
                    <ConversationPanel
                      item={selectedItem}
                      thread={thread.data ?? null}
                      loading={thread.isLoading}
                      error={thread.error}
                      capabilities={capabilities}
                      selectionUnavailable={preserveMissingSelection}
                      onRefresh={refreshSelected}
                      onDirtyChange={setReplyDraftDirty}
                    />
                  </div>
                </>
              ) : <EmptyState title="选择一个客户待办" description="从左侧列表选择案例后开始处理。" />}
            </section>

            <aside id="workspace-actions" className="workspace-action-column" tabIndex={-1}>
              {selectedItem ? (
                <>
                  <ServiceActionsPanel
                    item={selectedItem}
                    thread={thread.data ?? null}
                    capabilities={capabilities}
                    selectionUnavailable={preserveMissingSelection}
                    onRefresh={refreshSelected}
                  />
                  <OutcomePanel memory={thread.data?.support_memory ?? null} />
                </>
              ) : <EmptyState title="暂无处理动作" description="选择案例后显示允许的处理动作和实际结果。" />}
            </aside>
          </div>
        </>
      )}

      <ConfirmDialog
        open={discardDialogOpen}
        title="放弃未发送的回复？"
        description="切换案例、工作范围、页面或退出后，这段回复不会被保留。"
        confirmLabel="放弃回复"
        destructive
        onOpenChange={(open) => {
          setDiscardDialogOpen(open)
          if (!open) pendingActionRef.current = null
        }}
        onConfirm={confirmDiscard}
      />
    </ServiceAppShell>
  )
}

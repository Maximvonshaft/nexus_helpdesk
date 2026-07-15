import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { operationalPresentation } from '@/domain/operationalPresentation'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { EmptyState } from '@/components/ui/EmptyState'
import { ErrorSummary } from '@/components/ui/ErrorSummary'
import { Field, Input, Select, Textarea } from '@/components/ui/Field'
import { TechnicalDetails } from '@/components/ui/TechnicalDetails'
import { formatDateTime, sanitizeDisplayText } from '@/lib/format'
import { supportApi } from '@/lib/supportApi'
import { healthPresentation } from '@/lib/supportStatus'
import type { ChannelOnboardingTask } from '@/lib/channelControlTypes'
import type { ChannelAccount } from '@/lib/types'
import '@/features/admin-routes/admin-routes.css'

type PendingTaskAction = 'complete' | 'fail' | 'cancel' | null

type OnboardingDraft = {
  provider: string
  targetSlot: string
  displayName: string
  accountBinding: string
  externalAccountId: string
}

const emptyDraft: OnboardingDraft = {
  provider: 'whatsapp',
  targetSlot: '',
  displayName: '',
  accountBinding: '',
  externalAccountId: '',
}

function providerLabel(value: string) {
  if (value === 'webchat') return '网页客服'
  if (value === 'whatsapp') return 'WhatsApp'
  if (value === 'email') return '邮件'
  if (value === 'voice') return '语音'
  return sanitizeDisplayText(value)
}

function maskPhone(value: string | null | undefined) {
  const text = String(value || '').trim()
  if (!text) return '未返回'
  const digits = text.replace(/\D/g, '')
  return digits.length > 4 ? `•••• ${digits.slice(-4)}` : '已配置'
}

function errorCopy(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback
}

function taskStatus(task: ChannelOnboardingTask) {
  if (task.status === 'completed') return { tone: 'success' as const, label: '已完成' }
  if (task.status === 'failed') return { tone: 'danger' as const, label: '需要修复' }
  if (task.status === 'cancelled') return { tone: 'default' as const, label: '已取消' }
  if (task.status === 'in_progress') return { tone: 'warning' as const, label: '处理中' }
  return { tone: 'warning' as const, label: '待开始' }
}

function canStart(task: ChannelOnboardingTask) {
  return task.status === 'pending'
}

function canSettle(task: ChannelOnboardingTask) {
  return task.status === 'pending' || task.status === 'in_progress'
}

export function ChannelsPage() {
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<OnboardingDraft>(emptyDraft)
  const [selectedTask, setSelectedTask] = useState<ChannelOnboardingTask | null>(null)
  const [pendingAction, setPendingAction] = useState<PendingTaskAction>(null)
  const [failureReason, setFailureReason] = useState('')

  const accounts = useQuery({
    queryKey: ['canonicalChannelAccounts'],
    queryFn: supportApi.channelAccounts,
    refetchInterval: 30_000,
    retry: false,
  })
  const tasks = useQuery({
    queryKey: ['canonicalChannelOnboardingTasks'],
    queryFn: () => supportApi.channelOnboardingTasks({ limit: 50 }),
    refetchInterval: 15_000,
    retry: false,
  })
  const activeAccounts = useMemo(
    () => (accounts.data ?? []).filter((item: ChannelAccount) => item.is_active),
    [accounts.data],
  )
  const whatsappAccount = useMemo(
    () => activeAccounts.find((item: ChannelAccount) => item.provider === 'whatsapp'),
    [activeAccounts],
  )
  const whatsappStatus = useQuery({
    queryKey: ['canonicalWhatsappStatus', whatsappAccount?.account_id],
    queryFn: () => supportApi.whatsappNativeStatus(whatsappAccount?.account_id || ''),
    enabled: Boolean(whatsappAccount?.account_id),
    refetchInterval: 10_000,
    retry: false,
  })
  const whatsappHealth = healthPresentation(
    whatsappStatus.data?.channel_health_status
      || whatsappStatus.data?.status
      || whatsappAccount?.health_status,
  )

  const invalidateChannels = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['canonicalChannelAccounts'] }),
      queryClient.invalidateQueries({ queryKey: ['canonicalChannelOnboardingTasks'] }),
      queryClient.invalidateQueries({ queryKey: ['canonicalWhatsappStatus'] }),
    ])
  }

  const createTask = useMutation({
    mutationFn: () => supportApi.createChannelOnboardingTask({
      provider: draft.provider.trim(),
      target_slot: draft.targetSlot.trim() || null,
      desired_display_name: draft.displayName.trim() || null,
      desired_channel_account_binding: draft.accountBinding.trim() || null,
      external_channel_account_id: draft.externalAccountId.trim() || null,
    }),
    onSuccess: async () => {
      setDraft(emptyDraft)
      await invalidateChannels()
    },
  })

  const startTask = useMutation({
    mutationFn: (taskId: number) => supportApi.startChannelOnboardingTask(taskId),
    onSuccess: invalidateChannels,
  })

  const settleTask = useMutation({
    mutationFn: async () => {
      if (!selectedTask || !pendingAction) throw new Error('没有待执行的渠道任务动作')
      if (pendingAction === 'complete') {
        return supportApi.completeChannelOnboardingTask(selectedTask.id, {
          external_channel_account_id: selectedTask.external_channel_account_id || null,
          desired_channel_account_binding: selectedTask.desired_channel_account_binding || null,
        })
      }
      if (pendingAction === 'fail') {
        if (!failureReason.trim()) throw new Error('请填写具体失败原因')
        return supportApi.failChannelOnboardingTask(selectedTask.id, failureReason.trim())
      }
      return supportApi.cancelChannelOnboardingTask(selectedTask.id)
    },
    onSuccess: async () => {
      setSelectedTask(null)
      setPendingAction(null)
      setFailureReason('')
      await invalidateChannels()
    },
  })

  const actionError = createTask.error || startTask.error || settleTask.error
  const createReady = Boolean(draft.provider.trim() && (draft.displayName.trim() || draft.targetSlot.trim() || draft.externalAccountId.trim()))

  return (
    <main className="nd-admin-page">
      <header className="nd-admin-page__header">
        <div>
          <h1>渠道管理</h1>
          <p>查看渠道运行状态，并在同一后台创建、推进、完成或修复渠道接入任务。</p>
        </div>
        {accounts.isFetching || tasks.isFetching ? <Badge>正在刷新</Badge> : null}
      </header>

      {actionError ? <ErrorSummary title="渠道操作未完成" errors={[errorCopy(actionError, '请稍后重试')]} /> : null}

      <div className="nd-admin-grid">
        <section className="nd-admin-panel" aria-labelledby="channel-accounts-title">
          <div className="nd-admin-panel__head">
            <h2 id="channel-accounts-title">已启用渠道</h2>
            <Badge>{activeAccounts.length} 个账号</Badge>
          </div>
          <div className="nd-admin-panel__body">
            {accounts.isError ? (
              <ErrorSummary title="无法读取渠道账号" errors={[errorCopy(accounts.error, '请稍后重试')]} />
            ) : activeAccounts.length ? (
              <div className="nd-admin-table-wrap">
                <table className="nd-admin-table">
                  <caption className="sr-only">当前启用的渠道账号</caption>
                  <thead><tr><th scope="col">渠道</th><th scope="col">显示名称</th><th scope="col">运行状态</th><th scope="col">优先级</th><th scope="col">最近更新</th></tr></thead>
                  <tbody>
                    {activeAccounts.map((item) => {
                      const health = healthPresentation(item.health_status)
                      return <tr key={item.id}><td data-label="渠道">{providerLabel(item.provider)}</td><td data-label="显示名称">{sanitizeDisplayText(item.display_name || `${providerLabel(item.provider)} 账号`)}</td><td data-label="运行状态"><Badge tone={health.tone}>{health.label}</Badge></td><td data-label="优先级">{item.priority}</td><td data-label="最近更新">{formatDateTime(item.updated_at)}</td></tr>
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState title="暂无已启用渠道" description="创建接入任务并完成后，再启用实际渠道账号。" />
            )}
          </div>
        </section>

        <aside className="nd-admin-panel" aria-labelledby="whatsapp-health-title">
          <div className="nd-admin-panel__head"><h2 id="whatsapp-health-title">WhatsApp 连接</h2><Badge tone={whatsappHealth.tone}>{whatsappHealth.label}</Badge></div>
          <div className="nd-admin-panel__body nd-admin-stack">
            {!whatsappAccount ? <EmptyState title="未启用 WhatsApp" description="当前没有启用的 WhatsApp 渠道账号。" /> : whatsappStatus.isError ? <ErrorSummary title="无法读取 WhatsApp 状态" errors={[errorCopy(whatsappStatus.error, '请稍后重试')]} /> : <><dl className="nd-admin-facts"><div><dt>连接状态</dt><dd>{whatsappHealth.label}</dd></div><div><dt>绑定号码</dt><dd>{maskPhone(whatsappStatus.data?.phone_number)}</dd></div><div><dt>登录确认</dt><dd>{sanitizeDisplayText(whatsappStatus.data?.qr_status || '状态未知')}</dd></div><div><dt>重连次数</dt><dd>{whatsappStatus.data?.reconnect_count ?? 0}</dd></div></dl>{whatsappStatus.data?.last_error_message ? <ErrorSummary title="最近一次连接异常" errors={[sanitizeDisplayText(whatsappStatus.data.last_error_message)]} /> : null}<TechnicalDetails title="渠道技术详情" summary="默认收起"><dl><div><dt>Provider</dt><dd><code>{sanitizeDisplayText(whatsappAccount.provider)}</code></dd></div><div><dt>账号标识</dt><dd><code>{sanitizeDisplayText(whatsappAccount.account_id)}</code></dd></div><div><dt>连接记录</dt><dd>{whatsappStatus.data?.last_connected_at ? formatDateTime(whatsappStatus.data.last_connected_at) : '暂无'}</dd></div><div><dt>错误代码</dt><dd>{sanitizeDisplayText(whatsappStatus.data?.last_error_code || '无')}</dd></div></dl></TechnicalDetails></>}
          </div>
        </aside>
      </div>

      <div className="nd-admin-grid">
        <section className="nd-admin-panel" aria-labelledby="channel-onboarding-create-title">
          <div className="nd-admin-panel__head"><h2 id="channel-onboarding-create-title">新建渠道接入任务</h2></div>
          <div className="nd-admin-panel__body nd-admin-stack">
            <Field label="渠道" required><Select value={draft.provider} onChange={(event) => setDraft((current) => ({ ...current, provider: event.target.value }))}><option value="whatsapp">WhatsApp</option><option value="email">邮件</option><option value="webchat">网页客服</option><option value="voice">语音</option></Select></Field>
            <Field label="目标槽位" hint="例如 ch-primary、email-ch 或 voice-ch"><Input value={draft.targetSlot} onChange={(event) => setDraft((current) => ({ ...current, targetSlot: event.target.value }))} /></Field>
            <Field label="显示名称"><Input value={draft.displayName} onChange={(event) => setDraft((current) => ({ ...current, displayName: event.target.value }))} /></Field>
            <Field label="期望绑定"><Input value={draft.accountBinding} onChange={(event) => setDraft((current) => ({ ...current, accountBinding: event.target.value }))} /></Field>
            <Field label="外部账号 ID"><Input value={draft.externalAccountId} onChange={(event) => setDraft((current) => ({ ...current, externalAccountId: event.target.value }))} /></Field>
            <Button variant="primary" loading={createTask.isPending} disabled={!createReady} onClick={() => createTask.mutate()}>创建接入任务</Button>
            <p className="nd-admin-muted">创建任务不等于账号已经接通。任务必须开始、验证并完成后，才形成可审计的接入结果。</p>
          </div>
        </section>

        <section className="nd-admin-panel" aria-labelledby="channel-onboarding-list-title">
          <div className="nd-admin-panel__head"><h2 id="channel-onboarding-list-title">接入与修复任务</h2><Badge>{tasks.data?.total ?? 0} 项</Badge></div>
          <div className="nd-admin-panel__body nd-admin-stack">
            {tasks.isError ? <ErrorSummary title="无法读取渠道任务" errors={[errorCopy(tasks.error, '请稍后重试')]} /> : !(tasks.data?.tasks.length) ? <EmptyState title="暂无渠道任务" description="创建第一条渠道接入或修复任务。" /> : tasks.data.tasks.map((task) => {
              const status = taskStatus(task)
              const result = operationalPresentation(task.status, task.last_error)
              return <article className="nd-control-action" key={task.id}><div><div className="nd-control-action__title"><strong>{providerLabel(task.provider)} · {sanitizeDisplayText(task.desired_display_name || task.target_slot || `任务 #${task.id}`)}</strong><Badge tone={status.tone}>{status.label}</Badge></div><p>{task.last_error ? sanitizeDisplayText(task.last_error) : result.detail || '等待下一步处理。'}</p><small>更新于 {formatDateTime(task.updated_at)}</small></div><div className="nd-admin-stack">{canStart(task) ? <Button size="sm" loading={startTask.isPending} onClick={() => startTask.mutate(task.id)}>开始处理</Button> : null}{canSettle(task) ? <><Button size="sm" variant="primary" onClick={() => { setSelectedTask(task); setPendingAction('complete') }}>标记完成</Button><Button size="sm" variant="secondary" onClick={() => { setSelectedTask(task); setPendingAction('fail') }}>记录失败</Button><Button size="sm" variant="ghost" onClick={() => { setSelectedTask(task); setPendingAction('cancel') }}>取消任务</Button></> : null}</div></article>
            })}
          </div>
        </section>
      </div>

      <ConfirmDialog
        open={Boolean(selectedTask && pendingAction)}
        title={pendingAction === 'complete' ? '确认渠道任务完成？' : pendingAction === 'fail' ? '记录渠道任务失败？' : '取消渠道任务？'}
        description={pendingAction === 'complete' ? '仅在外部账号和绑定已经实际验证后确认。' : pendingAction === 'fail' ? '失败原因将成为后续修复和审计依据。' : '取消不会删除历史记录，但该任务将不再继续执行。'}
        confirmLabel={pendingAction === 'complete' ? '确认完成' : pendingAction === 'fail' ? '记录失败' : '确认取消'}
        destructive={pendingAction === 'fail' || pendingAction === 'cancel'}
        busy={settleTask.isPending}
        onOpenChange={(open) => { if (!open) { setSelectedTask(null); setPendingAction(null); setFailureReason('') } }}
        onConfirm={() => settleTask.mutate()}
      >
        {pendingAction === 'fail' ? <Field label="失败原因" required><Textarea value={failureReason} onChange={(event) => setFailureReason(event.target.value)} rows={4} placeholder="说明连接、凭证、供应商或配置中的具体失败原因" /></Field> : null}
      </ConfirmDialog>
    </main>
  )
}

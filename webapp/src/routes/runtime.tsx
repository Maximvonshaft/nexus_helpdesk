import { createRoute, redirect, useNavigate } from '@tanstack/react-router'
import { useMutation, useQueries, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken, type RuntimeRecoveryResult } from '@/lib/api'
import { formatDateTime, sanitizeDisplayText, signoffLabel } from '@/lib/format'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { DataTable } from '@/components/ui/DataTable'
import { PageHeader } from '@/components/ui/PageHeader'
import { Button } from '@/components/ui/Button'
import { Toast } from '@/components/ui/Toast'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { useState, useEffect } from 'react'
import { useSession } from '@/hooks/useAuth'
import { canViewOps } from '@/lib/access'
import type { BackgroundJob } from '@/lib/types'

type RecoveryAction = 'requeue_dead_jobs' | 'requeue_dead_outbound' | 'requeue_job'
type PendingRecovery = { action: RecoveryAction; message: string; job?: BackgroundJob } | null

function recoveryResultMessage(action: RecoveryAction, result: RuntimeRecoveryResult) {
  if (action === 'requeue_dead_jobs') return `已重排 ${result.requeued ?? 0} 个 dead 后台任务`
  if (action === 'requeue_dead_outbound') return `已重排 ${result.requeued ?? 0} 条 dead outbound 消息`
  return `任务 #${result.job_id ?? '—'} 已重排为 ${result.status ?? 'pending'}`
}

function jobRows(jobs: BackgroundJob[], requeueJob: (job: BackgroundJob) => void, pending: boolean) {
  return jobs.map((j) => [
    sanitizeDisplayText(j.job_type),
    sanitizeDisplayText(j.status),
    String(j.attempt_count),
    formatDateTime(j.updated_at),
    j.status === 'dead'
      ? <Button variant="secondary" disabled={pending} onClick={() => requeueJob(j)}>重排此任务</Button>
      : <span className="section-subtitle">无需处理</span>,
  ])
}

function RuntimePage() {
  const [toast, setToast] = useState<{ message: string; tone?: 'default' | 'danger' | 'success' } | null>(null)
  const session = useSession()
  const navigate = useNavigate()
  const client = useQueryClient()
  const permitted = canViewOps(session.data)
  const [pendingRecovery, setPendingRecovery] = useState<PendingRecovery>(null)
  const [runtime, readiness, signoff, jobs, queue, connectivity] = useQueries({
    queries: [
      { queryKey: ['runtimeHealth'], queryFn: api.runtimeHealth, enabled: permitted },
      { queryKey: ['readiness'], queryFn: api.readiness, enabled: permitted },
      { queryKey: ['signoff'], queryFn: api.signoff, enabled: permitted },
      { queryKey: ['jobs'], queryFn: api.jobs, enabled: permitted },
      { queryKey: ['queueSummary'], queryFn: api.queueSummary, enabled: permitted },
      { queryKey: ['legacySessionConnectivity'], queryFn: api.external_channelConnectivityCheck, enabled: permitted },
    ],
  })

  async function refreshRuntimeViews() {
    await Promise.all([
      client.invalidateQueries({ queryKey: ['runtimeHealth'] }),
      client.invalidateQueries({ queryKey: ['readiness'] }),
      client.invalidateQueries({ queryKey: ['signoff'] }),
      client.invalidateQueries({ queryKey: ['jobs'] }),
      client.invalidateQueries({ queryKey: ['queueSummary'] }),
      client.invalidateQueries({ queryKey: ['legacySessionConnectivity'] }),
    ])
  }

  const consumeOnce = useMutation({
    mutationFn: api.consumeExternalChannelEventsOnce,
    onSuccess: async (data) => {
      setToast({ message: `已执行一次消息同步，处理 ${data.processed} 批`, tone: 'success' })
      await refreshRuntimeViews()
    },
    onError: (err: Error) => setToast({ message: err.message || '执行消息同步失败', tone: 'danger' }),
  })
  const checkConnectivity = useMutation({
    mutationFn: api.external_channelConnectivityCheck,
    onSuccess: async (data) => {
      setToast({ message: data.bridge_started ? '旧会话桥接仍可用。' : '旧会话桥接未启用或不可用。', tone: data.bridge_started ? 'success' : 'default' })
      await refreshRuntimeViews()
    },
    onError: (err: Error) => setToast({ message: err.message || '旧会话桥接检查失败', tone: 'danger' }),
  })
  const recovery = useMutation({
    mutationFn: async ({ action, job }: { action: RecoveryAction; job?: BackgroundJob }) => {
      if (action === 'requeue_dead_jobs') return { action, result: await api.requeueDeadJobs({ limit: 50 }) }
      if (action === 'requeue_dead_outbound') return { action, result: await api.requeueDeadOutbound({ limit: 50 }) }
      if (!job) throw new Error('Missing job for requeue action')
      return { action, result: await api.requeueJob(job.id) }
    },
    onSuccess: async ({ action, result }) => {
      setToast({ message: recoveryResultMessage(action, result), tone: 'success' })
      await refreshRuntimeViews()
    },
    onError: (err: Error) => setToast({ message: err.message || '恢复动作执行失败', tone: 'danger' }),
  })

  function confirmAndRecover(action: RecoveryAction, message: string, job?: BackgroundJob) {
    setPendingRecovery({ action, message, job })
  }

  useEffect(() => {
    if (session.data && !permitted) navigate({ to: '/' })
  }, [navigate, permitted, session.data])

  const deadJobCount = queue.data?.dead_jobs ?? 0
  const deadOutboundCount = queue.data?.dead_outbound ?? 0
  const recoveryPending = recovery.isPending

  return (
    <AppShell>
      <PageHeader
        eyebrow="运营保障"
        title="发送健康与上线检查"
        description="给主管看系统是不是稳、哪里需要补处理。这个页面默认不展示给普通客服。"
        actions={<div style={{ display: 'flex', gap: 8 }}><Button onClick={() => checkConnectivity.mutate()} disabled={checkConnectivity.isPending || !permitted}>{checkConnectivity.isPending ? '检查中…' : '检查旧会话桥接'}</Button><Button onClick={() => consumeOnce.mutate()} disabled={consumeOnce.isPending || !permitted}>{consumeOnce.isPending ? '执行中…' : '执行一次旧同步'}</Button></div>}
      />
      {!permitted ? <Card><CardHeader title="无权限访问" subtitle="一线客服默认不需要进入运营保障页面。" /><CardBody><div className="message" data-role="agent">如需排查发送异常、同步中断、队列积压等问题，请联系主管或管理员。</div></CardBody></Card> : <><div className="metrics-grid"><Card className="metric"><div className="metric-label">待发送消息</div><div className="metric-value">{queue.data?.pending_outbound ?? '—'}</div></Card><Card className="metric"><div className="metric-label">发送异常</div><div className="metric-value">{queue.data?.dead_outbound ?? '—'}</div></Card><Card className="metric"><div className="metric-label">待处理任务</div><div className="metric-value">{queue.data?.pending_jobs ?? '—'}</div></Card><Card className="metric"><div className="metric-label">已关联客户会话</div><div className="metric-value">{queue.data?.external_channel_links ?? '—'}</div></Card></div>

      <Card className="soft">
        <CardHeader title="运行恢复动作" subtitle="只对 dead 状态对象做安全重排；所有动作会走后台权限校验和审计日志。执行后会刷新运行状态、任务列表和队列汇总。" />
        <CardBody>
          <div className="button-row" data-testid="runtime-recovery-actions">
            <Button variant="secondary" disabled={recoveryPending || deadJobCount <= 0} onClick={() => confirmAndRecover('requeue_dead_jobs', `确认重排最多 50 个 dead 后台任务？当前 dead jobs=${deadJobCount}`)}>{recoveryPending ? '处理中…' : '重排 dead 后台任务'}</Button>
            <Button variant="secondary" disabled={recoveryPending || deadOutboundCount <= 0} onClick={() => confirmAndRecover('requeue_dead_outbound', `确认重排最多 50 条 dead outbound 消息？当前 dead outbound=${deadOutboundCount}`)}>{recoveryPending ? '处理中…' : '重排 dead outbound'}</Button>
          </div>
          <div className="section-subtitle">不会删除任务，不会跳过权限，不会绕过后端审计。单条 dead job 可在“最近后台任务”表格中单独重排。</div>
        </CardBody>
      </Card>

      <div className="page-grid split-grid"><Card><CardHeader title="旧会话同步状态" /><CardBody><DataTable columns={['项目', '值']} rows={[['同步游标', sanitizeDisplayText(runtime.data?.sync_cursor)],['当前状态', sanitizeDisplayText(runtime.data?.sync_daemon_status)],['最近心跳', formatDateTime(runtime.data?.sync_daemon_last_seen_at)],['待补同步', String(runtime.data?.stale_link_count ?? '—')],['待执行同步任务', String(runtime.data?.pending_sync_jobs ?? '—')],['失败同步任务', String(runtime.data?.dead_sync_jobs ?? '—')],['待处理附件任务', String(runtime.data?.pending_attachment_jobs ?? '—')]]} /></CardBody></Card><Card><CardHeader title="上线检查清单" /><CardBody><DataTable columns={['检查项', '状态']} rows={Object.entries(signoff.data?.checks ?? {}).map(([key, value]) => [signoffLabel(key), value ? '通过' : '未通过'])} /></CardBody></Card><Card><CardHeader title="旧会话桥接状态" subtitle="只保留兼容检查；默认运行链路不再依赖旧桥接。" /><CardBody><DataTable columns={['项目', '值']} rows={[['部署模式', sanitizeDisplayText(connectivity.data?.deployment_mode)],['消息方式', sanitizeDisplayText(connectivity.data?.transport)],['命令', sanitizeDisplayText(connectivity.data?.command)],['桥接地址', sanitizeDisplayText(connectivity.data?.url)],['桥接已启动', connectivity.data?.bridge_started ? '是' : '否'],['会话列表工具可用', connectivity.data?.conversations_tool_ok ? '是' : '否'],['可见会话数', String(connectivity.data?.conversations_seen ?? '—')],['示例会话键', sanitizeDisplayText(connectivity.data?.sample_session_key)],['Token 鉴权已配置', connectivity.data?.token_auth_configured ? '是' : '否'],['Password 鉴权已配置', connectivity.data?.password_auth_configured ? '是' : '否']]} /></CardBody></Card><Card><CardHeader title="系统配置状态" /><CardBody><DataTable columns={['项目', '值']} rows={[['环境', sanitizeDisplayText(readiness.data?.app_env)],['数据库', sanitizeDisplayText(readiness.data?.database_url_scheme)],['附件存储', sanitizeDisplayText(readiness.data?.storage_backend)],['旧会话桥接', sanitizeDisplayText(readiness.data?.external_channel_transport)],['监控开关', String(readiness.data?.metrics_enabled ?? false)],['旧同步开关', String(readiness.data?.external_channel_sync_enabled ?? false)]]} /></CardBody></Card><Card><CardHeader title="最近后台任务" /><CardBody><DataTable columns={['任务类型', '状态', '尝试次数', '更新时间', '恢复动作']} rows={jobRows(jobs.data ?? [], (job) => confirmAndRecover('requeue_job', `确认重排 dead 任务 #${job.id}（${job.job_type}）？`, job), recoveryPending)} /></CardBody></Card></div></>}
      {toast ? <Toast message={toast.message} tone={toast.tone} onClose={() => setToast(null)} /> : null}
      <ConfirmDialog
        open={pendingRecovery !== null}
        title="确认执行运行恢复？"
        description={pendingRecovery?.message || ''}
        consequence="该动作只会重排 dead 状态对象，不会删除数据；后端会再次校验 runtime.manage、限流并记录审计。"
        confirmLabel="确认重排"
        tone="danger"
        pending={recovery.isPending}
        onCancel={() => setPendingRecovery(null)}
        onConfirm={() => {
          if (!pendingRecovery) return
          recovery.mutate({ action: pendingRecovery.action, job: pendingRecovery.job })
          setPendingRecovery(null)
        }}
      />
    </AppShell>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/runtime',
  beforeLoad: () => { if (!getToken()) throw redirect({ to: '/login' }) },
  component: RuntimePage,
})

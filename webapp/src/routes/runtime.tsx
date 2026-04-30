import { createRoute, redirect, useNavigate } from '@tanstack/react-router'
import { useMutation, useQueries } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import { formatDateTime, sanitizeDisplayText, signoffLabel } from '@/lib/format'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { DataTable } from '@/components/ui/DataTable'
import { PageHeader } from '@/components/ui/PageHeader'
import { Button } from '@/components/ui/Button'
import { Toast } from '@/components/ui/Toast'
import { useState, useEffect } from 'react'
import { useSession } from '@/hooks/useAuth'
import { canViewOps } from '@/lib/access'

function healthRows(service?: { status?: string | null; last_seen_at?: string | null; instance_id?: string | null; details?: Record<string, unknown> | null } | null) {
  return [
    ['状态', sanitizeDisplayText(service?.status || 'unknown')],
    ['实例', sanitizeDisplayText(service?.instance_id || '—')],
    ['最近心跳', formatDateTime(service?.last_seen_at)],
    ['详情', sanitizeDisplayText(service?.details ? JSON.stringify(service.details) : '—')],
  ]
}

function RuntimePage() {
  const [toast, setToast] = useState<{ message: string; tone?: 'default' | 'danger' | 'success' } | null>(null)
  const session = useSession()
  const navigate = useNavigate()
  const permitted = canViewOps(session.data)
  const [runtime, readiness, signoff, jobs, queue, connectivity] = useQueries({
    queries: [
      { queryKey: ['runtimeHealth'], queryFn: api.runtimeHealth, enabled: permitted },
      { queryKey: ['readiness'], queryFn: api.readiness, enabled: permitted },
      { queryKey: ['signoff'], queryFn: api.signoff, enabled: permitted },
      { queryKey: ['jobs'], queryFn: api.jobs, enabled: permitted },
      { queryKey: ['queueSummary'], queryFn: api.queueSummary, enabled: permitted },
      { queryKey: ['openclawConnectivity'], queryFn: api.openclawConnectivityCheck, enabled: permitted },
    ],
  })
  const consumeOnce = useMutation({
    mutationFn: api.consumeOpenClawEventsOnce,
    onSuccess: (data) => setToast({ message: `已执行一次消息同步，处理 ${data.processed} 批`, tone: 'success' }),
    onError: (err: Error) => setToast({ message: err.message || '执行消息同步失败', tone: 'danger' }),
  })
  const checkConnectivity = useMutation({
    mutationFn: api.openclawConnectivityCheck,
    onSuccess: (data) => setToast({ message: data.bridge_started ? `OpenClaw ${data.level || ''} 检查可用。` : 'OpenClaw 已检查，但桥接还没有真正连通。', tone: data.bridge_started ? 'success' : 'danger' }),
    onError: (err: Error) => setToast({ message: err.message || 'OpenClaw 联调检查失败', tone: 'danger' }),
  })
  useEffect(() => {
    if (session.data && !permitted) navigate({ to: '/' })
  }, [navigate, permitted, session.data])

  const warningRows = (runtime.data?.warnings ?? []).map((item) => ['运行告警', sanitizeDisplayText(item)])

  return (
    <AppShell>
      <PageHeader eyebrow="运营保障" title="发送健康与上线检查" description="给主管看系统是不是稳、哪里需要补处理。这个页面默认不展示给普通客服。" actions={<div style={{ display: 'flex', gap: 8 }}><Button onClick={() => checkConnectivity.mutate()} disabled={checkConnectivity.isPending || !permitted}>{checkConnectivity.isPending ? '检查中…' : '检查 OpenClaw 联调'}</Button><Button onClick={() => consumeOnce.mutate()} disabled={consumeOnce.isPending || !permitted}>{consumeOnce.isPending ? '执行中…' : '执行一次消息同步'}</Button></div>} />
      {!permitted ? <Card><CardHeader title="无权限访问" subtitle="一线客服默认不需要进入运营保障页面。" /><CardBody><div className="message" data-role="agent">如需排查发送异常、同步中断、队列积压等问题，请联系主管或管理员。</div></CardBody></Card> : <>
        <div className="metrics-grid">
          <Card className="metric"><div className="metric-label">待发送消息</div><div className="metric-value">{queue.data?.pending_outbound ?? runtime.data?.queue?.pending_outbound ?? '—'}</div></Card>
          <Card className="metric"><div className="metric-label">发送异常</div><div className="metric-value">{queue.data?.dead_outbound ?? runtime.data?.queue?.dead_outbound ?? '—'}</div></Card>
          <Card className="metric"><div className="metric-label">待处理任务</div><div className="metric-value">{queue.data?.pending_jobs ?? runtime.data?.queue?.pending_jobs ?? '—'}</div></Card>
          <Card className="metric"><div className="metric-label">失败后台任务</div><div className="metric-value">{queue.data?.dead_jobs ?? runtime.data?.queue?.dead_jobs ?? '—'}</div></Card>
        </div>
        <div className="page-grid split-grid">
          <Card><CardHeader title="API / DB Health" subtitle="基础服务与数据库连接。" /><CardBody><DataTable columns={['项目', '值']} rows={[
            ['环境', sanitizeDisplayText(readiness.data?.app_env)],
            ['数据库类型', sanitizeDisplayText(readiness.data?.database_url_scheme)],
            ['PostgreSQL 配置', readiness.data?.is_postgres ? '是' : '否'],
            ['附件存储', sanitizeDisplayText(readiness.data?.storage_backend)],
            ['监控开关', String(readiness.data?.metrics_enabled ?? false)],
          ]} /></CardBody></Card>
          <Card><CardHeader title="Worker Health" subtitle="处理 outbound 与 background jobs 的后台进程。" /><CardBody><DataTable columns={['项目', '值']} rows={healthRows(runtime.data?.worker)} /></CardBody></Card>
          <Card><CardHeader title="OpenClaw Sync Daemon Health" subtitle="负责 transcript / attachment stale sync。" /><CardBody><DataTable columns={['项目', '值']} rows={healthRows(runtime.data?.openclaw_sync_daemon)} /></CardBody></Card>
          <Card><CardHeader title="OpenClaw Event Daemon Health" subtitle="负责消费 OpenClaw 事件。" /><CardBody><DataTable columns={['项目', '值']} rows={healthRows(runtime.data?.openclaw_event_daemon)} /></CardBody></Card>
          <Card><CardHeader title="Queue / Job Backlog" subtitle="积压与失败任务。" /><CardBody><DataTable columns={['项目', '值']} rows={[
            ['待执行同步任务', String(runtime.data?.openclaw?.pending_sync_jobs ?? runtime.data?.pending_sync_jobs ?? '—')],
            ['失败同步任务', String(runtime.data?.openclaw?.dead_sync_jobs ?? runtime.data?.dead_sync_jobs ?? '—')],
            ['待补同步会话', String(runtime.data?.openclaw?.stale_link_count ?? runtime.data?.stale_link_count ?? '—')],
            ['待处理附件任务', String(runtime.data?.pending_attachment_jobs ?? '—')],
            ['失败附件任务', String(runtime.data?.dead_attachment_jobs ?? '—')],
            ...warningRows,
          ]} /></CardBody></Card>
          <Card><CardHeader title="OpenClaw Gateway Connectivity" subtitle="分层检查 Gateway 是否真的可用。" /><CardBody><DataTable columns={['项目', '值']} rows={[
            ['Probe 级别', sanitizeDisplayText(connectivity.data?.level || 'L0')],
            ['部署模式', sanitizeDisplayText(connectivity.data?.deployment_mode)],
            ['消息方式', sanitizeDisplayText(connectivity.data?.transport)],
            ['Gateway 地址', sanitizeDisplayText(connectivity.data?.url)],
            ['桥接已启动', connectivity.data?.bridge_started ? '是' : '否'],
            ['会话列表工具可用', connectivity.data?.conversations_tool_ok ? '是' : '否'],
            ['Transcript 读取可用', connectivity.data?.transcript_read_ok ? '是' : '否'],
            ['Same-route 发送前置满足', connectivity.data?.same_route_send_ready ? '是' : '否'],
            ['附件元数据可用', connectivity.data?.attachment_metadata_ok ? '是' : '否'],
            ['可见会话数', String(connectivity.data?.conversations_seen ?? '—')],
            ['示例会话键', sanitizeDisplayText(connectivity.data?.sample_session_key)],
          ]} /></CardBody></Card>
          <Card><CardHeader title="上线检查清单" /><CardBody><DataTable columns={['检查项', '状态']} rows={Object.entries(signoff.data?.checks ?? {}).map(([key, value]) => [signoffLabel(key), value ? '通过' : '未通过'])} /></CardBody></Card>
          <Card><CardHeader title="最近后台任务" /><CardBody><DataTable columns={['任务类型', '状态', '尝试次数', '更新时间']} rows={(jobs.data ?? []).map((j) => [sanitizeDisplayText(j.job_type), sanitizeDisplayText(j.status), String(j.attempt_count), formatDateTime(j.updated_at)])} /></CardBody></Card>
        </div>
      </>}
      {toast ? <Toast message={toast.message} tone={toast.tone} onClose={() => setToast(null)} /> : null}
    </AppShell>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/runtime',
  beforeLoad: () => { if (!getToken()) throw redirect({ to: '/login' }) },
  component: RuntimePage,
})

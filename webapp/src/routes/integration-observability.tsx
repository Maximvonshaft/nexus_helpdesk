import { useMemo, useState } from 'react'
import { createRoute, redirect } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import type { IntegrationObservabilityQuery, IntegrationRequestLogItem } from '@/lib/types'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { DataTable } from '@/components/ui/DataTable'
import { Field, Input, Select } from '@/components/ui/Field'
import { MetricCard } from '@/components/ui/MetricCard'
import { PageHeader } from '@/components/ui/PageHeader'
import { Skeleton } from '@/components/ui/Skeleton'
import { Toast } from '@/components/ui/Toast'
import { RequireCapability } from '@/components/security/RequireCapability'
import { formatDateTime, labelize, sanitizeDisplayText } from '@/lib/format'
import { routeAccess } from '@/lib/rbac'
import { useAutoRefresh } from '@/hooks/useAutoRefresh'

function statusTone(item: IntegrationRequestLogItem) {
  if (item.retryable) return 'warning' as const
  if (item.status_family === '2xx') return 'success' as const
  if (item.status_family === '4xx' || item.status_family === '5xx') return 'danger' as const
  return 'default' as const
}

function statusLabel(item: IntegrationRequestLogItem) {
  return item.status_code ? String(item.status_code) : labelize(item.status_family)
}

function downloadTextFile(filename: string, content: string, type: string) {
  const blob = new Blob([content], { type })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

function IntegrationObservabilityPage() {
  const queryClient = useQueryClient()
  const autoRefresh = useAutoRefresh(true)
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState('all')
  const [clientId, setClientId] = useState('')
  const [toast, setToast] = useState<{ message: string; tone?: 'default' | 'danger' | 'success' } | null>(null)

  const params = useMemo<IntegrationObservabilityQuery>(() => ({
    status,
    q: query.trim() || undefined,
    client_id: clientId ? Number(clientId) : undefined,
    limit: 75,
  }), [clientId, query, status])

  const observability = useQuery({
    queryKey: ['integrationObservability', params],
    queryFn: () => api.integrationObservability(params),
    refetchInterval: autoRefresh.enabled ? 15000 : false,
  })

  const exportCsv = useMutation({
    mutationFn: () => api.exportIntegrationObservabilityCsv({ ...params, limit: 300 }),
    onSuccess: (csv) => {
      downloadTextFile('integration-observability.csv', csv, 'text/csv;charset=utf-8')
      setToast({ message: 'CSV 已导出，后端已写入 admin audit。', tone: 'success' })
    },
    onError: (err: Error) => setToast({ message: err.message || '导出 Integration 观测 CSV 失败', tone: 'danger' }),
  })

  const data = observability.data
  const clients = data?.clients ?? []
  const requestRows = data?.items ?? []

  return (
    <AppShell>
      <RequireCapability requirement={routeAccess['/integration-observability']}>
        <PageHeader
          eyebrow="Integration Observability"
          title="外部 API 调用观测"
          description="读取真实 integration client、profile/task 请求日志、scope、幂等状态、request_id 和可重试错误；不提供假新增 client 入口。"
          actions={
            <div className="button-row">
              <Button variant="secondary" onClick={() => autoRefresh.setEnabled(!autoRefresh.enabled)}>
                {autoRefresh.enabled ? '暂停刷新' : '恢复刷新'}
              </Button>
              <Button variant="secondary" onClick={() => queryClient.invalidateQueries({ queryKey: ['integrationObservability'] })} disabled={observability.isFetching}>
                {observability.isFetching ? '刷新中...' : '立即刷新'}
              </Button>
              <Button onClick={() => exportCsv.mutate()} disabled={exportCsv.isPending || !data?.capabilities.csv_export}>
                {exportCsv.isPending ? '导出中...' : '导出 CSV'}
              </Button>
            </div>
          }
        />

        <div className="metrics-grid">
          <MetricCard label="请求总数" value={data?.summary.total ?? '—'} hint="当前筛选条件下的 request log" />
          <MetricCard label="成功" value={data?.summary.success_count ?? '—'} hint="2xx integration calls" />
          <MetricCard label="错误" value={data?.summary.error_count ?? '—'} hint="4xx/5xx 外部调用结果" />
          <MetricCard label="可重试" value={data?.summary.retryable_count ?? '—'} hint="processing、429 或 5xx" />
        </div>

        <Card className="soft">
          <CardHeader title="筛选" subtitle="筛选条件全部下推到后端，CSV 导出复用同一组条件并写 admin audit。" />
          <CardBody>
            <div className="workspace-toolbar">
              <Field label="搜索">
                <Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="client、endpoint、error_code、request_id、idempotency key" />
              </Field>
              <Field label="状态">
                <Select value={status} onChange={(event) => setStatus(event.target.value)}>
                  <option value="all">全部</option>
                  <option value="2xx">2xx 成功</option>
                  <option value="4xx">4xx 客户端错误</option>
                  <option value="5xx">5xx 服务端错误</option>
                  <option value="retryable">可重试</option>
                  <option value="processing">处理中</option>
                </Select>
              </Field>
              <Field label="Client">
                <Select value={clientId} onChange={(event) => setClientId(event.target.value)}>
                  <option value="">全部 client</option>
                  {clients.map((client) => <option value={client.id} key={client.id}>{client.name}</option>)}
                </Select>
              </Field>
              <div className="workspace-toolbar-meta">最近请求：{formatDateTime(data?.summary.last_created_at)}</div>
            </div>
          </CardBody>
        </Card>

        {observability.isLoading ? <Skeleton lines={8} /> : null}
        {observability.isError ? <div className="message" data-role="agent">无法加载 Integration 观测数据。</div> : null}

        {data ? (
          <div className="page-grid split-grid">
            <Card>
              <CardHeader title="External API Usage" subtitle="只展示后端实际存在的 profile/task 契约；latency 目前未持久化，所以明确为空。" />
              <CardBody>
                <DataTable
                  columns={['Endpoint', 'Scope', '调用', '成功', '错误', '可重试', '最近']}
                  rows={data.usage.map((item) => [
                    <span><strong>{item.method}</strong> {sanitizeDisplayText(item.endpoint)}</span>,
                    sanitizeDisplayText(item.scope),
                    String(item.count),
                    String(item.success_count),
                    String(item.error_count),
                    String(item.retryable_count),
                    formatDateTime(item.last_seen_at),
                  ])}
                />
              </CardBody>
            </Card>

            <Card>
              <CardHeader title="Integration Clients Registry" subtitle="读取 /api/admin/integration-clients 同源后端表；不会暴露密钥材料。" />
              <CardBody>
                <DataTable
                  columns={['Client', 'Scopes', 'Rate Limit', '状态', '请求', '最近使用']}
                  rows={clients.map((client) => [
                    <span><strong>{sanitizeDisplayText(client.name)}</strong><br /><small>{sanitizeDisplayText(client.key_id)}</small></span>,
                    client.scopes.join(', '),
                    `${client.rate_limit_per_minute}/min`,
                    <Badge tone={client.is_active ? 'success' : 'warning'}>{client.is_active ? 'Active' : 'Inactive'}</Badge>,
                    `${client.request_count} / err ${client.error_count}`,
                    formatDateTime(client.last_used_at || client.last_log_created_at),
                  ])}
                />
              </CardBody>
            </Card>

            <Card>
              <CardHeader title="Integration Request Log" subtitle="request_hash 只显示是否存在；response preview 做安全截断和字段脱敏。" />
              <CardBody>
                <DataTable
                  columns={['Client', 'Endpoint', 'Status', 'Idempotency', 'Request ID', 'Retry', 'Created']}
                  rows={requestRows.map((item) => [
                    sanitizeDisplayText(item.client_name),
                    <span><strong>{item.method}</strong> {sanitizeDisplayText(item.endpoint)}<br /><small>{sanitizeDisplayText(item.error_code || item.scope)}</small></span>,
                    <Badge tone={statusTone(item)}>{statusLabel(item)}</Badge>,
                    item.idempotency_key_present ? <Badge tone="success">key</Badge> : <Badge>none</Badge>,
                    item.request_id_available ? sanitizeDisplayText(item.request_id) : <span className="section-subtitle">未记录</span>,
                    item.retryable ? <Badge tone="warning">retryable</Badge> : <Badge>no</Badge>,
                    formatDateTime(item.created_at),
                  ])}
                />
              </CardBody>
            </Card>

            <Card>
              <CardHeader title="Backend Contract" subtitle="页面能力来自后端返回的契约声明，缺失项在这里明确展示。" />
              <CardBody>
                <div className="badges">
                  <Badge tone={data.capabilities.request_id_persisted ? 'success' : 'warning'}>request_id persisted</Badge>
                  <Badge tone={data.capabilities.csv_export ? 'success' : 'warning'}>CSV audit export</Badge>
                  <Badge tone={data.capabilities.client_registration_api ? 'success' : 'warning'}>client write API {data.capabilities.client_registration_api ? 'on' : 'not implemented'}</Badge>
                  <Badge tone={data.capabilities.latency_available ? 'success' : 'warning'}>latency {data.capabilities.latency_available ? 'available' : 'not persisted'}</Badge>
                </div>
                <DataTable
                  columns={['Method', 'Path', 'Scope', 'Idempotency', 'Request ID']}
                  rows={data.contracts.map((item) => [
                    item.method,
                    sanitizeDisplayText(item.path),
                    sanitizeDisplayText(item.scope),
                    item.idempotency_required ? 'required' : 'not required',
                    sanitizeDisplayText(item.request_id_header),
                  ])}
                />
              </CardBody>
            </Card>
          </div>
        ) : null}
      </RequireCapability>
      {toast ? <Toast message={toast.message} tone={toast.tone} onClose={() => setToast(null)} /> : null}
    </AppShell>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/integration-observability',
  beforeLoad: () => { if (!getToken()) throw redirect({ to: '/login' }) },
  component: IntegrationObservabilityPage,
})

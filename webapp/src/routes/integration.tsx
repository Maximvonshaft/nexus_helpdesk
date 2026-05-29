import { useMemo, useState } from 'react'
import { createRoute, redirect } from '@tanstack/react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import { RequireCapability } from '@/components/security/RequireCapability'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { DataTable } from '@/components/ui/DataTable'
import { EmptyState } from '@/components/ui/EmptyState'
import { Field, Input, Select } from '@/components/ui/Field'
import { PageHeader } from '@/components/ui/PageHeader'
import { useSession } from '@/hooks/useAuth'
import { canAccess, routeAccess } from '@/lib/rbac'
import { formatDateTime, labelize, sanitizeDisplayText } from '@/lib/format'
import type { BadgeTone, IntegrationObservabilityItem } from '@/lib/types'

const statusOptions = ['all', 'success', 'retryable', 'failed', 'conflict', 'pending'] as const

function statusTone(status?: string | null): BadgeTone {
  if (status === 'success') return 'success'
  if (status === 'retryable' || status === 'pending') return 'warning'
  if (status === 'failed' || status === 'conflict') return 'danger'
  return 'default'
}

function previewText(item: IntegrationObservabilityItem) {
  if (!item.response_preview) return '—'
  if (typeof item.response_preview === 'string') return sanitizeDisplayText(item.response_preview).slice(0, 220)
  return sanitizeDisplayText(JSON.stringify(item.response_preview)).slice(0, 220)
}

function requestRows(items: IntegrationObservabilityItem[]) {
  return items.map((item) => [
    <div className="stack compact">
      <strong>{sanitizeDisplayText(item.endpoint)}</strong>
      <span className="section-subtitle">{sanitizeDisplayText(item.method)} · {formatDateTime(item.created_at)}</span>
    </div>,
    <div className="stack compact">
      <span>{sanitizeDisplayText(item.client_name || item.client_key_id)}</span>
      <span className="section-subtitle">{item.scopes.length ? item.scopes.join(', ') : 'no scopes'}</span>
    </div>,
    <div className="stack compact">
      <span>{sanitizeDisplayText(item.request_id)}</span>
      <span className="section-subtitle">{sanitizeDisplayText(item.idempotency_key)}</span>
    </div>,
    <div className="stack compact">
      <Badge tone={statusTone(item.status_bucket)}>{labelize(item.status_bucket)}</Badge>
      <span className="section-subtitle">HTTP {item.status_code ?? '—'} · {sanitizeDisplayText(item.error_code)}</span>
    </div>,
    <Badge tone={item.retryable ? 'warning' : 'success'}>{item.retryable ? 'retryable' : 'not retryable'}</Badge>,
    previewText(item),
  ])
}

function IntegrationObservabilityPage() {
  const session = useSession()
  const queryClient = useQueryClient()
  const permitted = canAccess(session.data, routeAccess['/integration'])
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState<(typeof statusOptions)[number]>('all')
  const normalizedQuery = query.trim()
  const requests = useQuery({
    queryKey: ['integrationObservabilityRequests', normalizedQuery, status],
    queryFn: ({ signal }) => api.integrationObservabilityRequests({
      q: normalizedQuery || undefined,
      status_bucket: status === 'all' ? undefined : status,
      limit: 80,
    }, { signal }),
    enabled: permitted,
    retry: false,
  })
  const summary = requests.data?.summary
  const rows = useMemo(() => requestRows(requests.data?.items ?? []), [requests.data?.items])

  return (
    <AppShell>
      <RequireCapability requirement={routeAccess['/integration']}>
        <PageHeader
          eyebrow="System config"
          title="Integration Observability"
          description="外部集成请求、scope、幂等键、request_id 和 retryability 的只读排障视图。"
          actions={<Button variant="secondary" onClick={() => queryClient.invalidateQueries({ queryKey: ['integrationObservabilityRequests'] })}>刷新</Button>}
        />
        <div className="metrics-grid metrics-grid-wide" data-testid="integration-observability-workbench">
          <Card className="metric"><div className="metric-label">请求记录</div><div className="metric-value">{summary?.total ?? '—'}</div></Card>
          <Card className="metric"><div className="metric-label">可重试</div><div className="metric-value">{summary?.retryable ?? '—'}</div></Card>
          <Card className="metric"><div className="metric-label">缺 request_id</div><div className="metric-value">{summary?.missing_request_id ?? '—'}</div></Card>
          <Card className="metric"><div className="metric-label">Endpoint</div><div className="metric-value">{summary?.endpoints.length ?? '—'}</div></Card>
          <Card className="metric"><div className="metric-label">Client</div><div className="metric-value">{summary?.clients.length ?? '—'}</div></Card>
          <Card className="metric"><div className="metric-label">Error code</div><div className="metric-value">{summary?.error_codes.length ?? '—'}</div></Card>
        </div>

        <Card>
          <CardHeader title="Integration Request Log" subtitle="读取真实 integration_request_logs，不暴露 secret/token/authorization。" />
          <CardBody>
            <div className="form-grid">
              <Field label="搜索">
                <Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="request_id / idempotency / endpoint / client / error_code" />
              </Field>
              <Field label="状态">
                <Select value={status} onChange={(event) => setStatus(event.target.value as (typeof statusOptions)[number])}>
                  {statusOptions.map((item) => <option key={item} value={item}>{labelize(item)}</option>)}
                </Select>
              </Field>
            </div>
            <div style={{ marginTop: 12 }}>
              {requests.isError ? <EmptyState title="无法加载 Integration Observability" description={(requests.error as Error).message} /> : null}
              <DataTable
                columns={['Endpoint', 'Client / scopes', 'Request / idempotency', 'Status', 'Retryability', 'Safe response preview']}
                rows={rows}
                loading={requests.isLoading}
                empty={<EmptyState text="当前筛选下没有外部集成请求日志。" />}
              />
            </div>
          </CardBody>
        </Card>
      </RequireCapability>
    </AppShell>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/integration',
  beforeLoad: () => { if (!getToken()) throw redirect({ to: '/login' }) },
  component: IntegrationObservabilityPage,
})

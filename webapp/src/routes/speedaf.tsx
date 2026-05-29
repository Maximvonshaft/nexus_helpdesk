import { useEffect, useMemo, useState } from 'react'
import { createRoute, redirect } from '@tanstack/react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import type { CaseDetail, CaseListItem } from '@/lib/types'
import { actionAccess, canAccess, routeAccess } from '@/lib/rbac'
import { formatDateTime, labelize, marketLabel, priorityTone, sanitizeDisplayText, statusTone } from '@/lib/format'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { EmptyState } from '@/components/ui/EmptyState'
import { Input, Select } from '@/components/ui/Field'
import { GuidedWorkflow } from '@/components/ui/GuidedWorkflow'
import { MetricCard } from '@/components/ui/MetricCard'
import { PageHeader } from '@/components/ui/PageHeader'
import { RequireCapability } from '@/components/security/RequireCapability'
import { Skeleton } from '@/components/ui/Skeleton'
import { Toast } from '@/components/ui/Toast'
import { SpeedafActionsPanel } from '@/components/operator/SpeedafActionsPanel'
import { useAutoRefresh } from '@/hooks/useAutoRefresh'
import { useSession } from '@/hooks/useAuth'

const TERMINAL_STATUSES = new Set(['resolved', 'closed', 'canceled', 'cancelled'])
const SPEEDAF_QUEUE_TOKENS = new Set(['speedaf', 'delivery', 'shipment', 'parcel', 'waybill', 'tracking'])

function isSpeedafCandidate(item: CaseListItem) {
  if (item.tracking_number) return true
  const text = [item.title, item.source_channel, item.category, item.sub_category]
    .map((value) => String(value || '').toLowerCase())
    .join(' ')
  return text.split(/[^a-z0-9]+/).some((token) => SPEEDAF_QUEUE_TOKENS.has(token))
}

function defaultCaller(activeCase?: CaseDetail | null) {
  return activeCase?.customer?.phone || activeCase?.preferred_reply_contact || ''
}

function timelineTitle(item: Record<string, unknown>) {
  const fieldName = String(item.field_name || '')
  if (fieldName === 'speedaf_work_order') return 'Speedaf 催派工单'
  if (fieldName === 'speedaf_address_update') return 'Speedaf 地址更新'
  if (fieldName === 'speedaf_cancel') return 'Speedaf 取消请求'
  if (String(item.source_type || '') === 'ticket_event') return '工单事件'
  return labelize(String(item.source_type || item.kind || 'timeline'))
}

function timelineBody(item: Record<string, unknown>) {
  return sanitizeDisplayText(String(item.note || item.body || item.summary || item.event_type || item.id || ''))
}

function timelinePayload(item: Record<string, unknown>) {
  const payload = item.payload
  if (!payload || typeof payload !== 'object') return null
  const parsed = payload as Record<string, unknown>
  return Object.keys(parsed).length ? parsed : null
}

function evidenceValue(payload: Record<string, unknown> | null, key: string) {
  const value = payload?.[key]
  if (value === null || value === undefined || value === '') return '-'
  return sanitizeDisplayText(String(value))
}

function SpeedafActionCenterPage() {
  const session = useSession()
  const autoRefresh = useAutoRefresh(true)
  const client = useQueryClient()
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState('')
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [toast, setToast] = useState<{ message: string; tone?: 'default' | 'danger' | 'success' } | null>(null)

  const cases = useQuery({
    queryKey: ['speedafActionCenterCases', query, status],
    queryFn: () => api.cases({ q: query || undefined, status: status || undefined, limit: 80 }),
    refetchInterval: autoRefresh.enabled ? 15000 : false,
  })

  const rows = useMemo(() => {
    const items = cases.data ?? []
    const speedafItems = items.filter(isSpeedafCandidate)
    return speedafItems.length ? speedafItems : items
  }, [cases.data])

  useEffect(() => {
    if (!selectedId && rows.length) setSelectedId(rows[0].id)
  }, [rows, selectedId])

  const detail = useQuery({
    queryKey: ['caseDetail', selectedId],
    queryFn: () => api.caseDetail(selectedId as number),
    enabled: !!selectedId,
    refetchInterval: autoRefresh.enabled ? 10000 : false,
  })

  const timeline = useQuery({
    queryKey: ['ticketTimeline', selectedId],
    queryFn: () => api.ticketTimeline(selectedId as number, { limit: 35 }),
    enabled: !!selectedId,
    refetchInterval: autoRefresh.enabled ? 10000 : false,
  })

  const activeCase = detail.data
  const activeCount = rows.filter((item) => !TERMINAL_STATUSES.has(String(item.status))).length
  const trackingReadyCount = rows.filter((item) => Boolean(item.tracking_number)).length
  const missingDataCount = activeCase && (!activeCase.tracking_number || !defaultCaller(activeCase)) ? 1 : 0
  const canWorkOrder = canAccess(session.data, actionAccess.createSpeedafWorkOrder)
  const canAddress = canAccess(session.data, actionAccess.updateSpeedafAddress)
  const canCancel = canAccess(session.data, actionAccess.cancelSpeedafOrder)

  const refreshAll = async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: ['speedafActionCenterCases'] }),
      client.invalidateQueries({ queryKey: ['caseDetail', selectedId] }),
      client.invalidateQueries({ queryKey: ['ticketTimeline', selectedId] }),
      client.invalidateQueries({ queryKey: ['cases'] }),
    ])
  }

  return (
    <AppShell>
      <RequireCapability requirement={routeAccess['/speedaf']}>
        <PageHeader
          eyebrow="Speedaf"
          title="Speedaf Action Center"
          description="集中处理模板中的催派工单、地址更新和取消运单动作；每个写动作仍由后端 feature flag、capability、限流、幂等和 timeline/audit 保护。"
          actions={
            <div className="button-row">
              <Button variant="secondary" onClick={() => autoRefresh.setEnabled(!autoRefresh.enabled)}>
                {autoRefresh.enabled ? '暂停刷新' : '恢复刷新'}
              </Button>
              <Button onClick={() => void refreshAll()} disabled={cases.isFetching}>
                {cases.isFetching ? '刷新中...' : '立即刷新'}
              </Button>
            </div>
          }
        />

        <GuidedWorkflow steps={[
          { title: '选择工单', description: '优先处理有运单号和客户电话的 Speedaf 工单。', status: selectedId ? 'done' : 'active' },
          { title: '核对身份', description: '确认 waybillCode、callerID 和客户联系方式。', status: activeCase?.tracking_number && defaultCaller(activeCase) ? 'done' : selectedId ? 'active' : 'todo' },
          { title: '预检动作', description: '取消运单必须先查询当前状态并获取短效确认令牌。', status: canCancel ? 'active' : 'todo' },
          { title: '提交动作', description: '催派和地址更新进入后台队列；取消请求提交后不自动关闭工单。', status: canWorkOrder || canAddress || canCancel ? 'active' : 'todo' },
          { title: '审计回写', description: '成功后刷新 ticket timeline、job id、dedupe key 和 request_id 证据。', status: 'todo' },
        ]} />

        <div className="metrics-grid">
          <MetricCard label="队列项" value={rows.length} hint="Speedaf 候选或 ticket fallback" />
          <MetricCard label="处理中" value={activeCount} hint="非终态工单" />
          <MetricCard label="运单已就绪" value={trackingReadyCount} hint="tracking_number present" />
          <MetricCard label="当前缺资料" value={missingDataCount} hint="当前工单缺运单号或 callerID" />
        </div>

        <div className="workspace-toolbar">
          <Input placeholder="搜索工单、客户、运单号、Speedaf..." value={query} onChange={(event) => setQuery(event.target.value)} />
          <Select value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="">全部状态</option>
            <option value="in_progress">处理中</option>
            <option value="waiting_customer">待客户回复</option>
            <option value="escalated">已升级</option>
            <option value="resolved">已解决</option>
          </Select>
          <div className="workspace-toolbar-meta">共 {rows.length} 条</div>
        </div>

        <div className="page-grid workspace" data-testid="speedaf-action-center">
          <Card>
            <CardHeader title="Speedaf Queue" subtitle="按 ticket 队列承载高风险外部动作，避免脱离工单证据链。" />
            <CardBody>
              <div className="stack">
                {cases.isLoading ? <Skeleton lines={6} /> : null}
                {cases.isError ? <div className="message" data-role="agent">无法加载 Speedaf 队列。</div> : null}
                {rows.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    className={`queue-card ${selectedId === item.id ? 'selected' : ''}`}
                    onClick={() => setSelectedId(item.id)}
                  >
                    <div className="badges">
                      <Badge tone={statusTone(item.status)}>{labelize(item.status)}</Badge>
                      <Badge tone={priorityTone(item.priority)}>{labelize(item.priority)}</Badge>
                      {item.tracking_number ? <Badge tone="success">运单</Badge> : <Badge tone="warning">待补运单</Badge>}
                    </div>
                    <div className="queue-card-title">#{item.id} {sanitizeDisplayText(item.title)}</div>
                    <div className="queue-card-meta">{sanitizeDisplayText(item.customer_name || '未填写客户')} · {marketLabel(item.market_code, item.country_code)}</div>
                    <div className="queue-card-meta">运单 {sanitizeDisplayText(item.tracking_number || '-')} · 更新 {formatDateTime(item.updated_at)}</div>
                  </button>
                ))}
                {!rows.length && !cases.isLoading ? <EmptyState title="没有 Speedaf 队列项" description="当前筛选没有可处理的工单。" /> : null}
              </div>
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Ticket Context / Audit" subtitle="客户资料、运单、公告和 timeline 统一从真实 ticket API 读取。" />
            <CardBody>
              {detail.isLoading && !activeCase ? <Skeleton lines={8} /> : null}
              {activeCase ? (
                <div className="stack" data-testid="speedaf-ticket-context">
                  <div className="hero-block">
                    <div>
                      <div className="hero-title">#{activeCase.id} · {sanitizeDisplayText(activeCase.title)}</div>
                      <div className="section-subtitle">{sanitizeDisplayText(activeCase.customer_name || activeCase.customer?.name || '未填写客户')} · {formatDateTime(activeCase.updated_at)}</div>
                    </div>
                    <div className="badges">
                      <Badge tone={statusTone(activeCase.status)}>{labelize(activeCase.status)}</Badge>
                      <Badge tone={priorityTone(activeCase.priority)}>{labelize(activeCase.priority)}</Badge>
                    </div>
                  </div>
                  <div className="kv-grid">
                    <div className="kv"><label>waybillCode</label><div>{sanitizeDisplayText(activeCase.tracking_number || '缺失')}</div></div>
                    <div className="kv"><label>callerID</label><div>{sanitizeDisplayText(defaultCaller(activeCase) || '缺失')}</div></div>
                    <div className="kv"><label>客户</label><div>{sanitizeDisplayText(activeCase.customer_name || activeCase.customer?.name || '-')}</div></div>
                    <div className="kv"><label>市场</label><div>{marketLabel(activeCase.market_code, activeCase.country_code)}</div></div>
                  </div>
                  <div className="badges">
                    <Badge tone={canWorkOrder ? 'success' : 'warning'}>催派 {canWorkOrder ? '已授权' : '未授权'}</Badge>
                    <Badge tone={canAddress ? 'success' : 'warning'}>地址更新 {canAddress ? '已授权' : '未授权'}</Badge>
                    <Badge tone={canCancel ? 'success' : 'warning'}>取消 {canCancel ? '已授权' : '未授权'}</Badge>
                  </div>
                  <div className="message" data-role="user">{sanitizeDisplayText(activeCase.last_customer_message || activeCase.customer_request || activeCase.issue_summary || '暂无客户请求摘要。')}</div>
                  <div className="timeline" data-testid="speedaf-audit-timeline">
                    {(timeline.data?.items ?? []).map((item, index) => (
                      <div key={String(item.id || index)} className="message" data-role="agent">
                        <div className="message-head">
                          <strong>{timelineTitle(item as Record<string, unknown>)}</strong>
                          <span>{formatDateTime(String(item.created_at || ''))}</span>
                        </div>
                        <div>{timelineBody(item as Record<string, unknown>)}</div>
                        {timelinePayload(item as Record<string, unknown>) ? (
                          <div className="kv-grid" style={{ marginTop: 10 }}>
                            <div className="kv"><label>request_id</label><div>{evidenceValue(timelinePayload(item as Record<string, unknown>), 'request_id')}</div></div>
                            <div className="kv"><label>dedupe_key</label><div>{evidenceValue(timelinePayload(item as Record<string, unknown>), 'dedupe_key')}</div></div>
                            <div className="kv"><label>job_id</label><div>{evidenceValue(timelinePayload(item as Record<string, unknown>), 'job_id')}</div></div>
                            <div className="kv"><label>new_value</label><div>{sanitizeDisplayText(String(item.new_value || '-'))}</div></div>
                          </div>
                        ) : null}
                      </div>
                    ))}
                    {timeline.isLoading ? <Skeleton lines={4} /> : null}
                    {!timeline.isLoading && !(timeline.data?.items ?? []).length ? <EmptyState title="暂无 Speedaf 审计" description="提交动作后这里会显示 timeline 证据。" /> : null}
                  </div>
                </div>
              ) : (
                <EmptyState title="请选择一条工单" description="选择后展示身份核对、Speedaf 操作和 audit 证据。" />
              )}
            </CardBody>
          </Card>

          {activeCase ? (
            <SpeedafActionsPanel activeCase={activeCase} onToast={setToast} />
          ) : (
            <Card>
              <CardHeader title="Speedaf 操作" subtitle="选择工单后展示授权范围内的写动作。" />
              <CardBody><EmptyState title="等待选择工单" description="Speedaf 动作必须绑定 ticket。" /></CardBody>
            </Card>
          )}
        </div>
        {toast ? <Toast message={toast.message} tone={toast.tone} onClose={() => setToast(null)} /> : null}
      </RequireCapability>
    </AppShell>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/speedaf',
  beforeLoad: () => { if (!getToken()) throw redirect({ to: '/login' }) },
  component: SpeedafActionCenterPage,
})

import { useMemo, useState, type FormEvent } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, ApiError } from '@/lib/api'
import type { CaseListItem } from '@/lib/types'
import { canAccess, actionAccess } from '@/lib/rbac'
import { useSession } from '@/hooks/useAuth'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { Field, Input, Select } from '@/components/ui/Field'
import { EmptyState } from '@/components/ui/EmptyState'
import { ErrorState, LoadingState, WarningState } from '@/components/ui/StateViews'
import { formatDateTime, sanitizeDisplayText, statusTone } from '@/lib/format'

type LookupMode = 'customer' | 'waybill' | 'phone' | 'caller'

type LookupResult = {
  query: string
  mode: LookupMode
  selectedTicketId: number | null
}

const MODE_COPY: Record<LookupMode, { label: string; placeholder: string; help: string }> = {
  customer: { label: 'Customer 360', placeholder: '输入客户姓名、邮箱、手机号或客户关键字', help: '优先展示客户资料线索、历史会话、历史工单和风险提示。' },
  waybill: { label: '运单查询', placeholder: '输入 waybill / tracking number / order number', help: '复用真实工单搜索接口按运单号、订单号或标题关键字检索。' },
  phone: { label: '手机号查询', placeholder: '输入客户手机号，支持国家码或本地号码片段', help: '复用真实工单搜索接口按 preferred contact、客户电话或标题关键字检索。' },
  caller: { label: 'Caller ID 查询', placeholder: '输入 WebCall caller ID 或来电号码', help: '当前前端未发现独立 callerID 后端查询 API；先通过工单搜索兜底并明确标记缺口。' },
}

function errorMessage(error: unknown) {
  if (error instanceof ApiError && error.status === 403) return `403：缺少 capability，无法读取客户或工单资料。Request detail: ${String(error.detail || error.message)}`
  if (error instanceof ApiError && error.status === 500) return `500：后端处理失败。请查看 request id / server log。${error.message}`
  if (error instanceof Error) return error.message
  return '未知错误'
}

function ticketLabel(ticket: CaseListItem) {
  return ticket.ticket_no || `#${ticket.id}`
}

function modeRiskNote(mode: LookupMode) {
  if (mode === 'caller') return 'Caller ID 独立查询 API 未在当前 api.ts 暴露；该页不伪造结果，只用真实工单搜索做兜底。'
  if (mode === 'waybill') return '如果后端未按 tracking_number 建索引，搜索结果可能只覆盖标题/客户字段命中的工单。'
  if (mode === 'phone') return '手机号属于客户资料；当前页必须由 customer_profile.read 控制。'
  return 'Customer 360 读取客户历史信息；禁止在无权限时渲染敏感内容。'
}

function timelineCreatedAt(item: Record<string, unknown> | undefined) {
  const value = item?.created_at
  return typeof value === 'string' && value ? formatDateTime(value) : '-'
}

export function CustomerSearchPanel({ initialMode = 'customer' }: { initialMode?: LookupMode }) {
  const session = useSession()
  const [query, setQuery] = useState('')
  const [mode, setMode] = useState<LookupMode>(initialMode)
  const [submitted, setSubmitted] = useState<LookupResult | null>(null)
  const [selectedTicketId, setSelectedTicketId] = useState<number | null>(null)

  const resultQuery = useQuery({
    queryKey: ['customer-waybill-lookup', submitted?.mode, submitted?.query],
    queryFn: () => api.casesPage({ q: submitted?.query, limit: 25 }),
    enabled: Boolean(submitted?.query),
    retry: false,
  })

  const tickets = resultQuery.data?.items ?? []
  const selectedTicket = useMemo(() => tickets.find((ticket) => ticket.id === (selectedTicketId ?? submitted?.selectedTicketId)) ?? tickets[0] ?? null, [tickets, selectedTicketId, submitted?.selectedTicketId])

  const detailQuery = useQuery({
    queryKey: ['customer-waybill-ticket-detail', selectedTicket?.id],
    queryFn: () => api.caseDetail(selectedTicket?.id as number),
    enabled: Boolean(selectedTicket?.id),
    retry: false,
  })
  const timelineQuery = useQuery({
    queryKey: ['customer-waybill-ticket-timeline', selectedTicket?.id],
    queryFn: () => api.ticketTimeline(selectedTicket?.id as number, { limit: 20 }),
    enabled: Boolean(selectedTicket?.id),
    retry: false,
  })
  const threadQuery = useQuery({
    queryKey: ['customer-waybill-webchat-thread', selectedTicket?.id],
    queryFn: ({ signal }) => api.webchatThread(selectedTicket?.id as number, { signal }),
    enabled: Boolean(selectedTicket?.id),
    retry: false,
  })

  const canCreateSpeedafWorkOrder = canAccess(session.data, actionAccess.createSpeedafWorkOrder)
  const canUpdateSpeedafAddress = canAccess(session.data, actionAccess.updateSpeedafAddress)
  const canCancelSpeedafOrder = canAccess(session.data, actionAccess.cancelSpeedafOrder)

  function submitLookup(event: FormEvent) {
    event.preventDefault()
    const trimmed = query.trim()
    if (!trimmed) return
    setSelectedTicketId(null)
    setSubmitted({ query: trimmed, mode, selectedTicketId: null })
  }

  return (
    <div className="stack" data-testid="customer-waybill-lookup">
      <Card>
        <CardHeader title="Customer / Waybill / Caller ID 查询中心" subtitle="一级入口；只调用真实 API，不做 mock，不放无后端支撑的假动作。" />
        <CardBody>
          <form className="stack" onSubmit={submitLookup}>
            <div className="form-grid">
              <Field label="查询类型" description={MODE_COPY[mode].help}>
                <Select value={mode} onChange={(event) => setMode(event.target.value as LookupMode)}>
                  <option value="customer">Customer 360</option>
                  <option value="waybill">运单查询</option>
                  <option value="phone">手机号查询</option>
                  <option value="caller">Caller ID 查询</option>
                </Select>
              </Field>
              <Field label={MODE_COPY[mode].label} required hint={modeRiskNote(mode)}>
                <Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={MODE_COPY[mode].placeholder} />
              </Field>
            </div>
            <div className="button-row">
              <Button type="submit" variant="primary" disabled={!query.trim() || resultQuery.isFetching}>查询</Button>
              <Button type="button" variant="secondary" onClick={() => { setQuery(''); setSubmitted(null); setSelectedTicketId(null) }}>清空</Button>
            </div>
          </form>
        </CardBody>
      </Card>

      {submitted && resultQuery.isLoading ? <LoadingState title="正在查询" description="正在通过真实工单搜索接口读取客户/运单线索。" /> : null}
      {resultQuery.isError ? <ErrorState description={errorMessage(resultQuery.error)} onRetry={() => resultQuery.refetch()} /> : null}
      {submitted && !resultQuery.isLoading && !resultQuery.isError && !tickets.length ? <EmptyState title="没有查询结果" description="未从真实工单搜索接口命中客户、运单或 caller 线索；请换用完整 waybill、手机号或客户名。" /> : null}

      {tickets.length ? (
        <div className="page-grid split-grid-wide">
          <Card>
            <CardHeader title="匹配工单 / 运单线索" subtitle={`查询词：${sanitizeDisplayText(submitted?.query || '')}`} />
            <CardBody>
              <div className="list">
                {tickets.map((ticket) => (
                  <button key={ticket.id} className={`queue-card ${selectedTicket?.id === ticket.id ? 'selected' : ''}`} onClick={() => setSelectedTicketId(ticket.id)}>
                    <div className="badges">
                      <Badge tone={statusTone(ticket.status)}>{ticket.status}</Badge>
                      <Badge>{ticket.priority}</Badge>
                      {ticket.tracking_number ? <Badge tone="success">Waybill</Badge> : null}
                    </div>
                    <div className="queue-card-title">{ticketLabel(ticket)} · {sanitizeDisplayText(ticket.title)}</div>
                    <div className="queue-card-meta">{sanitizeDisplayText(ticket.customer_name || 'Unknown customer')} · {sanitizeDisplayText(ticket.tracking_number || 'no tracking number')} · {sanitizeDisplayText(ticket.source_channel || 'unknown channel')}</div>
                  </button>
                ))}
              </div>
            </CardBody>
          </Card>

          <div className="stack">
            <Card data-testid="customer-360-panel">
              <CardHeader title="Customer 360" subtitle="客户基础资料、历史工单、历史会话和风险提示。" />
              <CardBody>
                {!selectedTicket ? <EmptyState text="请选择一条查询结果。" /> : null}
                {detailQuery.isLoading ? <LoadingState title="正在读取客户详情" description="正在读取 ticket summary。" /> : null}
                {detailQuery.isError ? <ErrorState description={errorMessage(detailQuery.error)} onRetry={() => detailQuery.refetch()} /> : null}
                {detailQuery.data ? (
                  <div className="kv-grid">
                    <div className="kv"><label>客户名称</label><div>{sanitizeDisplayText(detailQuery.data.customer_name || detailQuery.data.customer?.name || '-')}</div></div>
                    <div className="kv"><label>联系方式</label><div>{sanitizeDisplayText(detailQuery.data.preferred_reply_contact || detailQuery.data.customer?.email || detailQuery.data.customer?.phone || '-')}</div></div>
                    <div className="kv"><label>运单号</label><div>{sanitizeDisplayText(detailQuery.data.tracking_number || '-')}</div></div>
                    <div className="kv"><label>市场</label><div>{sanitizeDisplayText(detailQuery.data.market_code || detailQuery.data.country_code || '-')}</div></div>
                    <div className="kv"><label>异常状态解释</label><div>{sanitizeDisplayText(detailQuery.data.customer_update || detailQuery.data.missing_fields || '暂无异常说明')}</div></div>
                    <div className="kv"><label>风险提示</label><div>{detailQuery.data.required_action ? sanitizeDisplayText(detailQuery.data.required_action) : '未发现 required action 标记。'}</div></div>
                  </div>
                ) : null}
              </CardBody>
            </Card>

            <Card data-testid="waybill-history-panel">
              <CardHeader title="历史会话 / 工单 / Timeline" subtitle="复用真实 WebChat thread 与 ticket timeline。" />
              <CardBody>
                {threadQuery.isLoading || timelineQuery.isLoading ? <LoadingState title="正在读取历史记录" description="正在加载 WebChat thread 与 timeline。" /> : null}
                {threadQuery.isError ? <WarningState title="WebChat thread 暂不可用" description={errorMessage(threadQuery.error)} /> : null}
                {timelineQuery.isError ? <WarningState title="Timeline 暂不可用" description={errorMessage(timelineQuery.error)} /> : null}
                <div className="stack compact">
                  <div className="message" data-role="agent"><strong>历史会话</strong><div>{threadQuery.data?.messages?.length ?? 0} messages · conversation {threadQuery.data?.conversation_id || '-'}</div></div>
                  <div className="message" data-role="agent"><strong>历史工单</strong><div>当前查询结果共 {tickets.length} 条；进一步跨客户全量历史需要后端 Customer 360 聚合 API。</div></div>
                  <div className="message" data-role="agent"><strong>Timeline</strong><div>{timelineQuery.data?.items?.length ?? 0} events · latest {timelineCreatedAt(timelineQuery.data?.items?.[0])}</div></div>
                </div>
              </CardBody>
            </Card>

            <Card data-testid="speedaf-actions-panel">
              <CardHeader title="Speedaf 操作" subtitle="无后端 API client 的动作不会渲染成可点击假按钮。" />
              <CardBody>
                <div className="stack compact">
                  <WarningState title="Backend capability gap" description="当前 api.ts 未暴露 order/query、order/waybillCode/query、order/cancel、order/updateAddress、workOrder/create 的前端 client 方法；本页只展示缺口和权限状态，不执行 fake action。" />
                  <div className="button-row">
                    <Button disabled variant="secondary">创建 Speedaf 催派工单：{canCreateSpeedafWorkOrder ? 'capability yes / API missing' : 'no capability'}</Button>
                    <Button disabled variant="secondary">地址更新：{canUpdateSpeedafAddress ? 'capability yes / API missing' : 'no capability'}</Button>
                    <Button disabled variant="secondary">取消运单：{canCancelSpeedafOrder ? 'capability yes / API missing' : 'no capability'}</Button>
                  </div>
                </div>
              </CardBody>
            </Card>
          </div>
        </div>
      ) : null}
    </div>
  )
}

export function Customer360Panel() {
  return <CustomerSearchPanel initialMode="customer" />
}

export function WaybillSearchPanel() {
  return <CustomerSearchPanel initialMode="waybill" />
}

export function CallerIdSearchPanel() {
  return <CustomerSearchPanel initialMode="caller" />
}

export function SpeedafActionsPanel() {
  return <CustomerSearchPanel initialMode="waybill" />
}

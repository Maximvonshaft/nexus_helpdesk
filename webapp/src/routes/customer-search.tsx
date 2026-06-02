import { useMemo, useState } from 'react'
import { createRoute, redirect, useNavigate } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { EmptyState } from '@/components/ui/EmptyState'
import { Field, Input, Select } from '@/components/ui/Field'
import { PageHeader } from '@/components/ui/PageHeader'
import { Skeleton } from '@/components/ui/Skeleton'
import { formatDateTime, labelize, marketLabel, priorityTone, sanitizeDisplayText, statusTone } from '@/lib/format'

type SearchMode = 'all' | 'waybill' | 'customer' | 'caller_id'

function normalizeQuery(value: string) {
  return value.trim().replace(/\s+/g, ' ')
}

function modeHelp(mode: SearchMode) {
  if (mode === 'waybill') return '按运单号或 tracking number 查询关联工单。'
  if (mode === 'caller_id') return '按来电号码、CallerID 或客户电话查询。'
  if (mode === 'customer') return '按客户姓名、邮箱、手机号或客户关键字查询。'
  return '统一查询客户、运单、CallerID、工单号和最近消息。'
}

function CustomerSearchPage() {
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [mode, setMode] = useState<SearchMode>('all')
  const normalized = normalizeQuery(query)
  const enabled = normalized.length >= 2
  const search = useQuery({
    queryKey: ['customerSearch', normalized, mode],
    queryFn: () => api.casesPage({ q: normalized, limit: 25 }),
    enabled,
  })
  const rows = search.data?.items ?? []
  const grouped = useMemo(() => {
    const terminal = new Set(['resolved', 'closed', 'canceled', 'cancelled'])
    return {
      active: rows.filter((item) => !terminal.has(String(item.status))),
      closed: rows.filter((item) => terminal.has(String(item.status))),
    }
  }, [rows])

  return (
    <AppShell>
      <PageHeader eyebrow="客户 / 运单查询" title="客户、运单与 CallerID 快查" description="给一线客服的一级查询入口。先查对象，再进入工单、WebChat、Email 或 WebCall 继续处理。当前版本复用后端工单搜索能力。" />
      <Card className="soft">
        <CardHeader title="查询条件" subtitle={modeHelp(mode)} />
        <CardBody>
          <div className="workspace-toolbar customer-search-toolbar">
            <Field label="查询类型">
              <Select value={mode} onChange={(event) => setMode(event.target.value as SearchMode)}>
                <option value="all">综合查询</option>
                <option value="waybill">运单号</option>
                <option value="customer">客户信息</option>
                <option value="caller_id">CallerID / 电话</option>
              </Select>
            </Field>
            <Field label="关键词" hint="至少输入 2 个字符；支持运单、客户、电话、工单标题或工单号。">
              <Input autoFocus value={query} onChange={(event) => setQuery(event.target.value)} placeholder="输入运单、电话、客户名、邮箱或工单关键字" />
            </Field>
          </div>
        </CardBody>
      </Card>
      <div className="metrics-grid">
        <Card className="metric"><div className="metric-label">匹配结果</div><div className="metric-value">{enabled ? rows.length : '—'}</div></Card>
        <Card className="metric"><div className="metric-label">活动工单</div><div className="metric-value">{enabled ? grouped.active.length : '—'}</div></Card>
        <Card className="metric"><div className="metric-label">已关闭/解决</div><div className="metric-value">{enabled ? grouped.closed.length : '—'}</div></Card>
        <Card className="metric"><div className="metric-label">数据来源</div><div className="metric-value">Cases</div></Card>
      </div>
      <Card>
        <CardHeader title="查询结果" subtitle="点击结果进入工单中心继续处理；渠道上下文会在对应工作台继续展示。" />
        <CardBody>
          {!enabled ? <EmptyState title="请输入查询关键词" description="客服无需先进入某个渠道页面，可直接按运单号、手机号、CallerID 或客户关键字查找关联工单。" /> : null}
          {search.isLoading ? <Skeleton lines={6} /> : null}
          {search.isError ? <EmptyState title="查询失败" description="请检查网络或稍后重试。" /> : null}
          {enabled && !search.isLoading && !rows.length ? <EmptyState title="没有匹配结果" description="可以尝试换用完整运单号、手机号后四位、客户邮箱或工单标题关键字。" /> : null}
          <div className="list">
            {rows.map((item) => (
              <button key={item.id} type="button" className="queue-card" onClick={() => navigate({ to: '/workspace' })}>
                <div className="badges">
                  <Badge tone={statusTone(item.status)}>{labelize(item.status)}</Badge>
                  <Badge tone={priorityTone(item.priority)}>{labelize(item.priority)}</Badge>
                  <Badge>{marketLabel(item.market_code, item.country_code)}</Badge>
                  {item.conversation_state ? <Badge>{labelize(item.conversation_state)}</Badge> : null}
                </div>
                <div className="queue-card-title">#{item.id} · {sanitizeDisplayText(item.title)}</div>
                <div className="queue-card-meta">{sanitizeDisplayText(item.customer_name || '未填写客户')} · {sanitizeDisplayText(item.tracking_number || '无运单号')}</div>
                <div className="queue-card-meta">{sanitizeDisplayText(item.assignee_name || '未分配客服')} · 更新时间 {formatDateTime(item.updated_at)}</div>
              </button>
            ))}
          </div>
        </CardBody>
      </Card>
    </AppShell>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/customer-search',
  beforeLoad: () => { if (!getToken()) throw redirect({ to: '/login' }) },
  component: CustomerSearchPage,
})

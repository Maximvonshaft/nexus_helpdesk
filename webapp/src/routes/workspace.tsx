import { useEffect, useMemo, useRef, useState } from 'react'
import { createRoute, redirect } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import { formatDateTime, labelize, marketLabel, priorityTone, sanitizeDisplayText, severityTone, statusTone } from '@/lib/format'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Field, Input, Select, Textarea } from '@/components/ui/Field'
import { EmptyState } from '@/components/ui/EmptyState'
import { PageHeader } from '@/components/ui/PageHeader'
import { Toast } from '@/components/ui/Toast'
import { SegmentedControl, ToolbarAction } from '@/components/ui/SegmentedControl'
import { Skeleton } from '@/components/ui/Skeleton'
import { useAutoRefresh } from '@/hooks/useAutoRefresh'

function timelineTitle(item: Record<string, unknown>) {
  const sourceType = String(item.source_type || '')
  if (sourceType === 'comment') return '客户消息'
  if (sourceType === 'internal_note') return '内部备注'
  if (sourceType === 'outbound_message') return '回复发送'
  if (sourceType === 'ai_intake') return '智能提炼'
  if (sourceType === 'ticket_event') return '工单事件'
  if (sourceType === 'webchat_event') return 'WebChat 事件'
  return '时间线项目'
}

function timelineBody(item: Record<string, unknown>) {
  return sanitizeDisplayText(
    String(
      item.body
      || item.summary
      || item.note
      || item.event_type
      || item.classification
      || item.id
      || ''
    )
  )
}

function timelineItemKey(item: Record<string, unknown>, index: number) {
  const stableValue = item.id || item.created_at || item.event_type || item.source_type || index
  return `timeline-${String(stableValue)}-${index}`
}

function SyncCountdown({ onRefresh }: { onRefresh: () => void }) {
  const [countdown, setCountdown] = useState(10)
  const timerRef = useRef<number | null>(null)

  useEffect(() => {
    timerRef.current = window.setInterval(() => {
      setCountdown((prev) => {
        if (prev <= 1) {
          onRefresh()
          return 10
        }
        return prev - 1
      })
    }, 1000)
    return () => { if (timerRef.current) clearInterval(timerRef.current) }
  }, [onRefresh])

  return (
    <Button variant="secondary" onClick={() => { setCountdown(10); onRefresh(); }}>
      🔄 同步中 ({countdown}s)
    </Button>
  )
}

function WorkspacePage() {
  const client = useQueryClient()
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState('')
  const [market, setMarket] = useState('')
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const autoRefresh = useAutoRefresh(true)
  const [toast, setToast] = useState<{ message: string; tone?: 'default' | 'danger' | 'success' } | null>(null)

  const meta = useQuery({ queryKey: ['liteMeta'], queryFn: api.liteMeta, refetchInterval: autoRefresh.enabled ? 30000 : false })
  const cases = useQuery({
    queryKey: ['cases', query, status, market],
    queryFn: async () => {
      const rows = await api.cases({ q: query || undefined, status: status || undefined })
      if (!market) return rows
      return rows.filter((item) => (item.market_code || item.country_code || '') === market)
    },
    refetchInterval: autoRefresh.enabled ? 15000 : false,
  })
  const [isDirty, setIsDirty] = useState(false)
  const [editorId, setEditorId] = useState<number | null>(null)

  const detail = useQuery({
    queryKey: ['caseDetail', selectedId],
    queryFn: () => api.caseDetail(selectedId as number),
    enabled: !!selectedId,
    refetchInterval: autoRefresh.enabled && !isDirty ? 10000 : false,
  })
  const timeline = useQuery({
    queryKey: ['ticketTimeline', selectedId],
    queryFn: () => api.ticketTimeline(selectedId as number, { limit: 50 }),
    enabled: !!selectedId,
    refetchInterval: autoRefresh.enabled && !isDirty ? 10000 : false,
  })

  const refreshConversation = () => {
    if (selectedId) {
      client.invalidateQueries({ queryKey: ['caseDetail', selectedId] })
      client.invalidateQueries({ queryKey: ['ticketTimeline', selectedId] })
    }
  }

  useEffect(() => {
    if (!selectedId && cases.data?.length) setSelectedId(cases.data[0].id)
  }, [cases.data, selectedId])

  const [form, setForm] = useState({
    status: '',
    assignee_id: '',
    required_action: '',
    missing_fields: '',
    customer_update: '',
    resolution_summary: '',
    human_note: '',
    ai_summary: '',
    ai_case_type: '',
    ai_required_action: '',
    ai_missing_fields: '',
  })

  const handleSelectCase = (id: number) => {
    if (isDirty) {
      if (!window.confirm('当前工单有未保存的编辑，切换后将丢失。确定要切换吗？')) return
    }
    setSelectedId(id)
    setIsDirty(false)
  }

  useEffect(() => {
    const d = detail.data
    if (!d) return
    if (editorId === d.id && isDirty) return // Skip overwrite if editing same case
    setEditorId(d.id)
    setIsDirty(false)
    setForm({
      status: d.status || '',
      assignee_id: '',
      required_action: d.required_action || '',
      missing_fields: d.missing_fields || '',
      customer_update: d.customer_update || '',
      resolution_summary: d.resolution_summary || '',
      human_note: '',
      ai_summary: d.ai_summary || '',
      ai_case_type: d.ai_classification || '',
      ai_required_action: '',
      ai_missing_fields: d.last_customer_message || '',
    })
  }, [detail.data, editorId, isDirty])

  const saveMutation = useMutation({
    mutationFn: async () => {
      if (!selectedId) return
      const payload: Record<string, unknown> = {
        required_action: form.required_action,
        missing_fields: form.missing_fields,
        customer_update: form.customer_update,
        resolution_summary: form.resolution_summary,
      }
      if (form.status && form.status !== detail.data?.status) payload.status = form.status
      if (form.assignee_id) payload.assignee_id = Number(form.assignee_id)
      if (form.human_note.trim()) payload.human_note = form.human_note.trim()
      return api.workflowUpdate(selectedId, payload)
    },
    onSuccess: async (updated) => {
      setToast({ message: '工单已更新', tone: 'success' })
      await client.invalidateQueries({ queryKey: ['cases'] })
      await client.invalidateQueries({ queryKey: ['caseDetail', selectedId] })
      await client.invalidateQueries({ queryKey: ['ticketTimeline', selectedId] })
      if (updated?.id) { setSelectedId(updated.id); setIsDirty(false); }
    },
    onError: (err: Error) => setToast({ message: err.message || '更新工单失败', tone: 'danger' }),
  })

  const aiMutation = useMutation({
    mutationFn: async () => {
      if (!selectedId) return
      return api.aiIntake(selectedId, {
        ai_summary: form.ai_summary,
        case_type: form.ai_case_type,
        suggested_required_action: form.ai_required_action,
        missing_fields: form.ai_missing_fields,
        last_customer_message: detail.data?.last_customer_message || '',
      })
    },
    onSuccess: async () => {
      setToast({ message: '智能提炼已保存', tone: 'success' })
      setIsDirty(false)
      await client.invalidateQueries({ queryKey: ['caseDetail', selectedId] })
      await client.invalidateQueries({ queryKey: ['ticketTimeline', selectedId] })
    },
    onError: (err: Error) => setToast({ message: err.message || '保存智能提炼失败', tone: 'danger' }),
  })

  const activeCase = detail.data
  const timelineItems = timeline.data?.items ?? []
  const users = meta.data?.users ?? []
  const statuses = meta.data?.statuses ?? []
  const caseCount = cases.data?.length ?? 0
  const marketOptions = [...new Set((cases.data ?? []).map((item) => item.market_code || item.country_code).filter(Boolean) as string[])]

  const queueCards = useMemo(() => (cases.data ?? []).map((item) => (
    <button className={`queue-card ${selectedId === item.id ? 'selected' : ''}`} key={item.id} onClick={() => handleSelectCase(item.id)}>
      <div className="queue-card-top">
        <div className="badges">
          <Badge tone={statusTone(item.status)}>{labelize(item.status)}</Badge>
          <Badge tone={priorityTone(item.priority)}>{labelize(item.priority)}</Badge>
          <Badge tone="success">{marketLabel(item.market_code, item.country_code)}</Badge>
        </div>
      </div>
      <div className="queue-card-title">#{item.id} {sanitizeDisplayText(item.title)}</div>
      <div className="queue-card-meta">{sanitizeDisplayText(item.customer_name || '未填写客户姓名')} · {sanitizeDisplayText(item.assignee_name || '未分配客服')}</div>
      <div className="queue-card-meta">{labelize(item.conversation_state || 'no_conversation_state')} · {formatDateTime(item.updated_at)}</div>
    </button>
  )), [cases.data, selectedId])

  return (
    <AppShell>
      <PageHeader
        eyebrow="工单处理"
        title="客服处理工作台"
        description="把客户信息、最新消息、口径公告、附件证据和处理动作放在同一页，客服接单后能顺着页面往下处理。"
        actions={
          <div className="button-row">
            {autoRefresh.enabled && !isDirty && <SyncCountdown onRefresh={refreshConversation} />}
            <Button variant="secondary" onClick={() => autoRefresh.setEnabled(!autoRefresh.enabled)}>
              {autoRefresh.enabled ? '暂停自动刷新' : '恢复自动刷新'}
            </Button>
            <Button variant="secondary" onClick={() => client.invalidateQueries()}>
              立即刷新全部
            </Button>
          </div>
        }
      />

      <div className="workspace-toolbar">
        <Input placeholder="搜索工单、客户、运单号…" value={query} onChange={(e) => setQuery(e.target.value)} />
        <Select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">全部状态</option>
          {statuses.map((s) => <option key={s} value={s}>{labelize(s)}</option>)}
        </Select>
        <Select value={market} onChange={(e) => setMarket(e.target.value)}>
          <option value="">全部市场</option>
          {marketOptions.map((code) => <option key={code} value={code}>{code}</option>)}
        </Select>
        <SegmentedControl value={status || 'all'} onChange={(next) => setStatus(next === 'all' ? '' : next)} options={[
          { label: '全部', value: 'all' },
          { label: '处理中', value: 'in_progress' },
          { label: '待客户回复', value: 'waiting_customer' },
          { label: '已解决', value: 'resolved' },
        ]} />
        <div className="workspace-toolbar-meta">共 {caseCount} 个工单</div>
      </div>

      <Card className="soft">
        <CardHeader title="处理顺序提示" subtitle="让新同事也能按统一动作处理工单。" />
        <CardBody>
          <div className="guide-grid">
            <div className="guide-item"><strong>先看客户最新消息</strong><span>不要急着回，先确认客户最新诉求、运单号和附件证据。</span></div>
            <div className="guide-item"><strong>再看公告与证据</strong><span>有延误、清关或异常公告时，先按统一口径处理。</span></div>
            <div className="guide-item"><strong>最后保存处理结果</strong><span>把下一步动作、缺失信息和客户更新内容一次写完整。</span></div>
          </div>
        </CardBody>
      </Card>

      <div className="page-grid workspace">
        <div className="stack">
          <Card>
            <CardHeader title="工单列表" subtitle="按市场、状态、客服和最新更新时间快速定位要处理的工单。左侧选单，右侧连续处理。" />
            <CardBody>
              <div className="stack">
                <div className="button-row queue-inline-actions">
                  <ToolbarAction onClick={() => setSelectedId((id) => {
                    const rows = cases.data ?? []
                    if (!rows.length) return id
                    const idx = rows.findIndex((row) => row.id === id)
                    return rows[Math.max(0, idx - 1)]?.id ?? rows[0].id
                  })}>上一单</ToolbarAction>
                  <ToolbarAction onClick={() => setSelectedId((id) => {
                    const rows = cases.data ?? []
                    if (!rows.length) return id
                    const idx = rows.findIndex((row) => row.id === id)
                    return rows[Math.min(rows.length - 1, idx + 1)]?.id ?? rows[rows.length - 1].id
                  })}>下一单</ToolbarAction>
                </div>
                <div className="list">
                  {cases.isLoading ? <Skeleton lines={6} /> : queueCards}
                  {!queueCards.length && !cases.isLoading ? <EmptyState text="当前筛选条件下没有工单。" /> : null}
                </div>
              </div>
            </CardBody>
          </Card>
        </div>

        <div className="stack">
          <Card>
            <CardHeader title="工单详情" subtitle="客服最常用的信息集中在这里：先看客户说了什么，再看应该怎么回，再保存处理结果。" />
            <CardBody>
              {detail.isLoading && !activeCase ? <Skeleton lines={10} /> : null}
              {activeCase ? (
                <div className="stack">
                  <div className="hero-block">
                    <div>
                      <div className="hero-title">#{activeCase.id} · {sanitizeDisplayText(activeCase.issue_summary || activeCase.title)}</div>
                      <div className="section-subtitle">{sanitizeDisplayText(activeCase.customer_name || activeCase.customer?.name || '未填写客户姓名')} · {sanitizeDisplayText(activeCase.assignee_name || '未分配客服')} · 更新时间 {formatDateTime(activeCase.updated_at)}</div>
                    </div>
                    <div className="badges">
                      <Badge tone={statusTone(activeCase.status)}>{labelize(activeCase.status)}</Badge>
                      <Badge tone={priorityTone(activeCase.priority)}>{labelize(activeCase.priority)}</Badge>
                      <Badge tone="success">{marketLabel(activeCase.market_code, activeCase.country_code)}</Badge>
                      {activeCase.conversation_state ? <Badge>{labelize(activeCase.conversation_state)}</Badge> : null}
                    </div>
                  </div>

                  <div className="kv-grid kv-grid-three">
                    <div className="kv"><label>客户姓名</label><div>{sanitizeDisplayText(activeCase.customer_name || activeCase.customer?.name)}</div></div>
                    <div className="kv"><label>联系方式</label><div>{sanitizeDisplayText(activeCase.customer?.phone || activeCase.customer?.email || activeCase.preferred_reply_contact)}</div></div>
                    <div className="kv"><label>运单号</label><div>{sanitizeDisplayText(activeCase.tracking_number)}</div></div>
                    <div className="kv"><label>来源渠道</label><div>{labelize(activeCase.preferred_reply_channel)}</div></div>
                    <div className="kv"><label>回复路径</label><div>{sanitizeDisplayText(activeCase.preferred_reply_contact)}</div></div>
                    <div className="kv"><label>市场</label><div>{marketLabel(activeCase.market_code, activeCase.country_code)}</div></div>
                  </div>

                  <div className="page-grid split-grid">
                    <div className="stack">
                      <div className="section-title">问题摘要</div>
                      <div className="message">{sanitizeDisplayText(activeCase.issue_summary || activeCase.title)}</div>
                      <div className="section-title">客户诉求</div>
                      <div className="message" data-role="user">{sanitizeDisplayText(activeCase.customer_request)}</div>
                      <div className="section-title">客户最新消息</div>
                      <div className="message" data-role="user">{sanitizeDisplayText(activeCase.last_customer_message)}</div>
                    </div>
                    <Card className="soft">
                      <CardHeader title="处理上下文" subtitle="方便接手人快速判断下一步，不需要再翻多个页面。" />
                      <CardBody>
                        <div className="kv-grid">
                          <div className="kv"><label>当前客服</label><div>{sanitizeDisplayText(activeCase.assignee_name || '未分配')}</div></div>
                          <div className="kv"><label>所属团队</label><div>{sanitizeDisplayText(activeCase.team_name)}</div></div>
                          <div className="kv"><label>智能摘要</label><div>{sanitizeDisplayText(activeCase.ai_summary)}</div></div>
                          <div className="kv"><label>工单类型</label><div>{sanitizeDisplayText(activeCase.ai_classification)}</div></div>
                        </div>
                      </CardBody>
                    </Card>
                  </div>

                  <Card className="soft">
                    <CardHeader title="来信来源信息" subtitle="展示客户当前来信来源与最近同步时间。" />
                    <CardBody>
                      <div className="kv-grid">
                        <div className="kv"><label>来源状态</label><div>{activeCase.openclaw_conversation ? '已绑定来信来源' : '未绑定'}</div></div>
                        <div className="kv"><label>渠道</label><div>{sanitizeDisplayText(activeCase.openclaw_conversation?.channel)}</div></div>
                        <div className="kv"><label>联系对象</label><div>{sanitizeDisplayText(activeCase.openclaw_conversation?.recipient)}</div></div>
                        <div className="kv"><label>最近同步</label><div>{formatDateTime(activeCase.openclaw_conversation?.last_synced_at)}</div></div>
                      </div>
                    </CardBody>
                  </Card>

                  <div className="page-grid split-grid">
                    <Card>
                      <CardHeader title="客户消息记录" subtitle="客服处理时重点看这一段，按时间顺序看清客户说了什么。" />
                      <CardBody>
                        <div className="timeline">
                          {timelineItems.map((item, index) => (
                            <div key={timelineItemKey(item, index)} className="message" data-role={String(item.source_type) === 'comment' ? 'user' : 'agent'}>
                              <div className="message-head">
                                <strong>{timelineTitle(item)}</strong>
                                <span>{formatDateTime(String(item.created_at || ''))}</span>
                              </div>
                              <div>{timelineBody(item)}</div>
                            </div>
                          ))}
                          {!timelineItems.length && !timeline.isLoading ? <EmptyState text="当前还没有可展示的时间线记录。" /> : null}
                          {timeline.isLoading ? <Skeleton lines={4} /> : null}
                        </div>
                      </CardBody>
                    </Card>

                    <Card>
                      <CardHeader title="当前生效公告" subtitle="这单工单受哪些公告影响，一眼看清，避免回复口径不一致。" />
                      <CardBody>
                        <div className="list">
                          {(activeCase.active_market_bulletins ?? []).map((bulletin) => (
                            <div className="list-item" key={bulletin.id}>
                              <div className="badges">
                                <Badge>{labelize(bulletin.category || 'notice')}</Badge>
                                {bulletin.severity ? <Badge tone={severityTone(bulletin.severity)}>{labelize(bulletin.severity)}</Badge> : null}
                                {bulletin.auto_inject_to_ai ? <Badge tone="success">智能助手可引用</Badge> : null}
                              </div>
                              <div><strong>{sanitizeDisplayText(bulletin.title)}</strong></div>
                              <div className="section-subtitle">{sanitizeDisplayText(bulletin.summary || bulletin.body)}</div>
                            </div>
                          ))}
                          {!(activeCase.active_market_bulletins?.length) ? <EmptyState text="这单工单当前没有关联公告。" /> : null}
                        </div>
                      </CardBody>
                    </Card>
                  </div>

                  <Card>
                    <CardHeader title="附件与证据" subtitle="把系统上传附件和聊天侧证据都统一展示给客服。" />
                    <CardBody>
                      <div className="page-grid split-grid">
                        <div>
                          <div className="section-title">系统附件</div>
                          <div className="list compact">
                            {(activeCase.attachments ?? []).map((item) => (
                              <div className="list-item" key={item.id}>
                                <div><strong>{sanitizeDisplayText(item.file_name)}</strong></div>
                                <div className="section-subtitle">{sanitizeDisplayText(item.mime_type || '文件')} · {formatDateTime(item.created_at)}</div>
                              </div>
                            ))}
                            {!(activeCase.attachments?.length) ? <div className="empty">当前没有系统附件。</div> : null}
                          </div>
                        </div>
                        <div>
                          <div className="section-title">聊天证据</div>
                          <div className="list compact">
                            {(activeCase.openclaw_attachment_references ?? []).map((item) => (
                              <div className="list-item" key={item.id}>
                                <div><strong>{sanitizeDisplayText(item.filename || item.remote_attachment_id)}</strong></div>
                                <div className="section-subtitle">{sanitizeDisplayText(item.content_type || '未知类型')} · {sanitizeDisplayText(item.storage_status)}</div>
                              </div>
                            ))}
                            {!(activeCase.openclaw_attachment_references?.length) ? <div className="empty">当前没有聊天证据。</div> : null}
                          </div>
                        </div>
                      </div>
                    </CardBody>
                  </Card>
                </div>
              ) : (
                <EmptyState text="请先从左侧选择一条工单。" />
              )}
            </CardBody>
          </Card>
        </div>

        <div className="stack">
          <Card>
            <CardHeader title="处理动作" subtitle="客服常用动作集中在这里：更新状态、补充说明、保存客户更新。" />
            <CardBody>
              {detail.isLoading && !activeCase ? <Skeleton lines={10} /> : null}
              {activeCase ? (
                <div className="stack">
                  <Field label="工单状态">
                    <Select value={form.status} onChange={(e) => setForm((s) => ({ ...s, status: e.target.value }))}>
                      {statuses.map((s) => <option key={s} value={s}>{labelize(s)}</option>)}
                    </Select>
                  </Field>

                  <Field label="分配给">
                    <Select value={form.assignee_id} onChange={(e) => setForm((s) => ({ ...s, assignee_id: e.target.value }))}>
                      <option value="">保持当前分配</option>
                      {users.map((u) => <option key={u.id} value={u.id}>{u.display_name}</option>)}
                    </Select>
                  </Field>

                  <Field label="下一步动作" hint="例如：联系网点、催件、核实客户资料。">
                    <Textarea value={form.required_action} onChange={(e) => setForm((s) => ({ ...s, required_action: e.target.value }))} />
                  </Field>
                  <Field label="待补信息" hint="例如：缺运单照片、缺清关资料、缺客户电话。">
                    <Textarea value={form.missing_fields} onChange={(e) => setForm((s) => ({ ...s, missing_fields: e.target.value }))} />
                  </Field>
                  <Field label="给客户的更新说明">
                    <Textarea value={form.customer_update} onChange={(e) => setForm((s) => ({ ...s, customer_update: e.target.value }))} />
                  </Field>
                  <Field label="解决结果摘要">
                    <Textarea value={form.resolution_summary} onChange={(e) => setForm((s) => ({ ...s, resolution_summary: e.target.value }))} />
                  </Field>
                  <Field label="内部备注">
                    <Textarea value={form.human_note} onChange={(e) => setForm((s) => ({ ...s, human_note: e.target.value }))} />
                  </Field>

                  <div className="button-row">
                    <Button variant="primary" onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending}>
                      {saveMutation.isPending ? '保存中…' : '保存处理结果'}
                    </Button>
                  </div>

                  <Card className="soft">
                    <CardHeader title="智能提炼" subtitle="把客户消息沉淀成结构化摘要，方便下一位客服快速接手。" />
                    <CardBody>
                      <div className="stack">
                        <Field label="智能摘要">
                          <Textarea value={form.ai_summary} onChange={(e) => setForm((s) => ({ ...s, ai_summary: e.target.value }))} />
                        </Field>
                        <Field label="工单类型">
                          <Input value={form.ai_case_type} onChange={(e) => setForm((s) => ({ ...s, ai_case_type: e.target.value }))} placeholder="例如：延误、清关、签收异常" />
                        </Field>
                        <Field label="建议动作">
                          <Textarea value={form.ai_required_action} onChange={(e) => setForm((s) => ({ ...s, ai_required_action: e.target.value }))} />
                        </Field>
                        <Field label="补充上下文">
                          <Textarea value={form.ai_missing_fields} onChange={(e) => setForm((s) => ({ ...s, ai_missing_fields: e.target.value }))} />
                        </Field>
                        <Button variant="secondary" onClick={() => aiMutation.mutate()} disabled={aiMutation.isPending}>
                          {aiMutation.isPending ? '保存中…' : '保存智能提炼'}
                        </Button>
                      </div>
                    </CardBody>
                  </Card>
                </div>
              ) : (
                <EmptyState text="请选择一条工单后再执行处理动作。" />
              )}
            </CardBody>
          </Card>
        </div>
      </div>
      {toast ? <Toast message={toast.message} tone={toast.tone} onClose={() => setToast(null)} /> : null}
    </AppShell>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/workspace',
  beforeLoad: () => { if (!getToken()) throw redirect({ to: '/login' }) },
  component: WorkspacePage,
})

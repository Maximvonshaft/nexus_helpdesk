import { useEffect, useMemo, useState } from 'react'
import { createRoute, redirect, useNavigate } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import { canManageGovernanceReleases, canReadGovernanceReleases } from '@/lib/access'
import type { BadgeTone, GovernanceRelease, GovernanceReleaseCreate } from '@/lib/types'
import { formatDateTime, labelize, sanitizeDisplayText } from '@/lib/format'
import { useSession } from '@/hooks/useAuth'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { EmptyState } from '@/components/ui/EmptyState'
import { ErrorSummary } from '@/components/ui/ErrorSummary'
import { Field, Input, Select, Textarea } from '@/components/ui/Field'
import { GuidedWorkflow } from '@/components/ui/GuidedWorkflow'
import { PageHeader } from '@/components/ui/PageHeader'
import { SegmentedControl } from '@/components/ui/SegmentedControl'
import { TechnicalDetails } from '@/components/ui/TechnicalDetails'
import { Toast } from '@/components/ui/Toast'

type ReleaseAction = 'submit' | 'approve' | 'publish' | 'rollback' | 'reject'

const sourceOptions = ['ai_config', 'persona', 'knowledge', 'bulletin', 'channel_account', 'outbound_email', 'speedaf_action'] as const
const statusOptions = ['all', 'draft', 'pending_review', 'approved', 'published', 'rolled_back', 'rejected'] as const
const riskOptions = ['low', 'medium', 'high', 'critical'] as const

const actionMeta: Record<ReleaseAction, { label: string; description: string; tone?: 'default' | 'danger' }> = {
  submit: { label: '提交复核', description: '将草稿变更进入正式治理复核队列。' },
  approve: { label: '审批通过', description: '确认 diff、影响范围和回滚计划可接受。' },
  publish: { label: '标记已发布', description: '记录发布证据。实际业务对象仍通过各自已存在 API 发布。' },
  rollback: { label: '标记回滚', description: '记录回滚执行证据，并保留完整审计链。', tone: 'danger' },
  reject: { label: '拒绝发布', description: '终止本次治理发布请求。', tone: 'danger' },
}

function emptyForm() {
  return {
    source_type: 'ai_config',
    source_id: '',
    title: '',
    summary: '',
    release_type: 'publish',
    status: 'pending_review',
    risk_level: 'medium',
    impact_text: '{\n  "channels": ["webchat", "email"],\n  "customers_affected": "scoped"\n}',
    diff_text: '{\n  "before": {},\n  "after": {}\n}',
    rollback_plan: '',
  }
}

function parseJsonObject(value: string, fallback: Record<string, unknown>) {
  const trimmed = value.trim()
  if (!trimmed) return fallback
  const parsed = JSON.parse(trimmed)
  return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed as Record<string, unknown> : fallback
}

function releaseTone(value?: string | null): BadgeTone {
  if (value === 'published') return 'success'
  if (value === 'rolled_back' || value === 'rejected') return 'danger'
  if (value === 'pending_review' || value === 'approved') return 'warning'
  return 'default'
}

function riskTone(value?: string | null): BadgeTone {
  if (value === 'critical') return 'danger'
  if (value === 'high') return 'warning'
  if (value === 'low') return 'success'
  return 'default'
}

function allowedActions(item?: GovernanceRelease | null): ReleaseAction[] {
  if (!item) return []
  if (item.status === 'draft' || item.status === 'rejected') return ['submit']
  if (item.status === 'pending_review') return ['approve', 'reject']
  if (item.status === 'approved') return ['publish', 'reject']
  if (item.status === 'published') return ['rollback']
  return []
}

function metricValue(counts: Record<string, number> | undefined, key: string) {
  return counts?.[key] ?? 0
}

function selectedSourceLabel(item?: GovernanceRelease | null) {
  if (!item) return '—'
  return `${labelize(item.source_type)}${item.source_id ? ` #${item.source_id}` : ''}`
}

function GovernanceReleasesPage() {
  const session = useSession()
  const navigate = useNavigate()
  const client = useQueryClient()
  const canRead = canReadGovernanceReleases(session.data)
  const canManage = canManageGovernanceReleases(session.data)
  const [statusFilter, setStatusFilter] = useState('all')
  const [query, setQuery] = useState('')
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [form, setForm] = useState(emptyForm())
  const [jsonError, setJsonError] = useState<string | null>(null)
  const [toast, setToast] = useState<{ message: string; tone?: 'default' | 'danger' | 'success' } | null>(null)
  const [confirm, setConfirm] = useState<{ action: ReleaseAction; item: GovernanceRelease } | null>(null)

  useEffect(() => {
    if (session.data && !canRead) navigate({ to: '/' })
  }, [canRead, navigate, session.data])

  const releases = useQuery({
    queryKey: ['governance-releases', statusFilter, query],
    queryFn: () => api.governanceReleases({ status: statusFilter === 'all' ? undefined : statusFilter, q: query || undefined, limit: 100 }),
    enabled: Boolean(session.data && canRead),
  })

  const selected = useMemo(
    () => releases.data?.items.find((item) => item.id === selectedId) ?? releases.data?.items[0] ?? null,
    [releases.data?.items, selectedId],
  )

  const createMutation = useMutation({
    mutationFn: () => {
      setJsonError(null)
      let payload: GovernanceReleaseCreate
      try {
        payload = {
          source_type: form.source_type,
          source_id: form.source_id ? Number(form.source_id) : null,
          title: form.title,
          summary: form.summary,
          release_type: form.release_type,
          status: form.status as 'draft' | 'pending_review',
          risk_level: form.risk_level,
          impact_json: parseJsonObject(form.impact_text, {}),
          diff_json: parseJsonObject(form.diff_text, {}),
          rollback_plan: form.rollback_plan || null,
        }
      } catch {
        setJsonError('影响范围或 diff 必须是 JSON 对象。')
        throw new Error('影响范围或 diff 必须是 JSON 对象。')
      }
      return api.createGovernanceRelease(payload)
    },
    onSuccess: async (saved) => {
      setSelectedId(saved.id)
      setForm(emptyForm())
      setToast({ message: '治理发布请求已创建', tone: 'success' })
      await client.invalidateQueries({ queryKey: ['governance-releases'] })
    },
    onError: (err: Error) => setToast({ message: err.message || '创建治理发布请求失败', tone: 'danger' }),
  })

  const actionMutation = useMutation({
    mutationFn: ({ action, item }: { action: ReleaseAction; item: GovernanceRelease }) => {
      const note = `${actionMeta[action].label}: ${item.title}`
      if (action === 'submit') return api.submitGovernanceRelease(item.id, note)
      if (action === 'approve') return api.approveGovernanceRelease(item.id, note)
      if (action === 'publish') return api.publishGovernanceRelease(item.id, note)
      if (action === 'rollback') return api.rollbackGovernanceRelease(item.id, note)
      return api.rejectGovernanceRelease(item.id, note)
    },
    onSuccess: async (saved) => {
      setSelectedId(saved.id)
      setConfirm(null)
      setToast({ message: '治理动作已写入审计链', tone: 'success' })
      await client.invalidateQueries({ queryKey: ['governance-releases'] })
    },
    onError: (err: Error) => setToast({ message: err.message || '治理动作执行失败', tone: 'danger' }),
  })

  const items = releases.data?.items ?? []
  const selectedActions = allowedActions(selected)

  if (session.data && !canRead) {
    return (
      <AppShell>
        <Card>
          <CardHeader title="无权限访问" subtitle="发布治理队列需要 governance.release.read 或 governance.release.manage。" />
          <CardBody><div className="message" data-role="agent">请回到日常处理页面继续客服作业。</div></CardBody>
        </Card>
      </AppShell>
    )
  }

  return (
    <AppShell>
      <PageHeader
        eyebrow="Control Tower / Governance"
        title="发布治理队列"
        description="把 Persona、Knowledge、公告口径、发送线路和 Speedaf 高影响动作纳入同一个审批、发布、回滚证据链。"
        actions={<div className="button-row"><Button variant="secondary" onClick={() => releases.refetch()} disabled={releases.isFetching}>刷新</Button></div>}
      />

      <GuidedWorkflow steps={[
        { title: '提交变更', description: '记录来源、diff、影响范围与回滚计划。' },
        { title: '复核审批', description: '主管或管理员确认风险、窗口和影响面。' },
        { title: '发布落证', description: '发布动作写入事件流与后台审计。' },
        { title: '回滚复盘', description: '保留 request_id、回滚说明和审计链。' },
      ]} />

      <div className="metrics-grid metrics-grid-wide">
        <div className="metric-card"><div className="metric-label">待复核</div><div className="metric-value">{metricValue(releases.data?.status_counts, 'pending_review')}</div><div className="metric-hint">需要审批</div></div>
        <div className="metric-card"><div className="metric-label">已审批</div><div className="metric-value">{metricValue(releases.data?.status_counts, 'approved')}</div><div className="metric-hint">等待发布落证</div></div>
        <div className="metric-card"><div className="metric-label">已发布</div><div className="metric-value">{metricValue(releases.data?.status_counts, 'published')}</div><div className="metric-hint">可执行回滚记录</div></div>
        <div className="metric-card"><div className="metric-label">高风险</div><div className="metric-value">{metricValue(releases.data?.risk_counts, 'high') + metricValue(releases.data?.risk_counts, 'critical')}</div><div className="metric-hint">需要完整 diff 与回滚计划</div></div>
      </div>

      <div className="workspace-toolbar">
        <Input placeholder="搜索标题或摘要" value={query} onChange={(event) => setQuery(event.target.value)} />
        <SegmentedControl value={statusFilter} onChange={setStatusFilter} options={statusOptions.map((value) => ({ value, label: labelize(value) }))} />
        <div className="workspace-toolbar-meta">共 {releases.data?.total ?? 0} 条</div>
      </div>

      {releases.error ? <ErrorSummary title="发布治理队列加载失败" errors={[(releases.error as Error).message]} /> : null}

      <div className="page-grid split-grid-wide" data-testid="governance-release-queue">
        <Card>
          <CardHeader title="发布请求" subtitle="按照最新更新时间排序；每条记录都必须能解释来源、影响、diff 和回滚。" />
          <CardBody>
            <div className="list">
              {items.map((item) => (
                <button key={item.id} className={`queue-card ${selected?.id === item.id ? 'selected' : ''}`} onClick={() => setSelectedId(item.id)}>
                  <div className="badges">
                    <Badge tone={releaseTone(item.status)}>{labelize(item.status)}</Badge>
                    <Badge tone={riskTone(item.risk_level)}>{labelize(item.risk_level)}</Badge>
                    <Badge>{labelize(item.source_type)}</Badge>
                  </div>
                  <div className="queue-card-title">{sanitizeDisplayText(item.title)}</div>
                  <div className="queue-card-meta">{selectedSourceLabel(item)} · {formatDateTime(item.updated_at)}</div>
                  <div className="queue-card-meta">{sanitizeDisplayText(item.summary)}</div>
                </button>
              ))}
              {!items.length ? <EmptyState title="没有治理发布请求" text="当前筛选条件下没有待处理发布请求。" action={canManage ? '可在右侧创建新的审批发布请求。' : undefined} /> : null}
            </div>
          </CardBody>
        </Card>

        <div className="stack">
          <Card data-testid="governance-release-actions">
            <CardHeader title="发布动作" subtitle={selected ? selected.title : '选择一条发布请求查看可执行动作。'} />
            <CardBody>
              {selected ? (
                <div className="stack compact">
                  <div className="badges">
                    <Badge tone={releaseTone(selected.status)}>{labelize(selected.status)}</Badge>
                    <Badge tone={riskTone(selected.risk_level)}>{labelize(selected.risk_level)}</Badge>
                    <Badge>{labelize(selected.release_type)}</Badge>
                  </div>
                  <div className="kv-grid">
                    <div className="kv"><label>来源</label><div>{selectedSourceLabel(selected)}</div></div>
                    <div className="kv"><label>审计目标</label><div>{sanitizeDisplayText(selected.audit_target_type)} {selected.audit_target_id ? `#${selected.audit_target_id}` : ''}</div></div>
                    <div className="kv"><label>提交人</label><div>{sanitizeDisplayText(selected.requested_by)}</div></div>
                    <div className="kv"><label>更新时间</label><div>{formatDateTime(selected.updated_at)}</div></div>
                  </div>
                  <p className="message">{sanitizeDisplayText(selected.summary)}</p>
                  {canManage ? (
                    <div className="button-row">
                      {selectedActions.map((action) => (
                        <Button key={action} variant={actionMeta[action].tone === 'danger' ? 'danger' : 'primary'} disabled={actionMutation.isPending} onClick={() => setConfirm({ action, item: selected })}>
                          {actionMeta[action].label}
                        </Button>
                      ))}
                      {!selectedActions.length ? <Badge>当前状态无可执行动作</Badge> : null}
                    </div>
                  ) : <div className="message" data-role="agent">当前账号只能查看治理证据，不能审批、发布或回滚。</div>}
                </div>
              ) : <EmptyState text="请选择一条发布请求。" />}
            </CardBody>
          </Card>

          <Card data-testid="governance-release-evidence">
            <CardHeader title="证据链" subtitle="事件流保留 request_id，并与后台 AdminAuditLog 使用同一 release id 对齐。" />
            <CardBody>
              {selected ? (
                <div className="timeline-list">
                  {selected.events.map((event) => (
                    <div key={event.id} className="timeline-item">
                      <div className="timeline-title">{labelize(event.event_type)} · {formatDateTime(event.created_at)}</div>
                      <div className="timeline-body">{sanitizeDisplayText(event.note || event.request_id)}</div>
                    </div>
                  ))}
                  <TechnicalDetails title="高级发布契约" summary="/api/admin/governance-releases">
                    <pre>{JSON.stringify({ impact: selected.impact_json, diff: selected.diff_json, rollback_plan: selected.rollback_plan, events: selected.events }, null, 2)}</pre>
                  </TechnicalDetails>
                </div>
              ) : <EmptyState text="选择发布请求后显示事件证据。" />}
            </CardBody>
          </Card>
        </div>
      </div>

      <Card>
        <CardHeader title="创建治理发布请求" subtitle="用于把模板定义的 approval / release / rollback 形态落到真实后台审计链。" />
        <CardBody>
          {!canManage ? <div className="message" data-role="agent">当前账号无治理发布管理权限。</div> : null}
          {jsonError ? <ErrorSummary title="JSON 格式错误" errors={[jsonError]} /> : null}
          <div className="stack">
            <div className="form-grid">
              <Field label="来源类型">
                <Select value={form.source_type} onChange={(event) => setForm((state) => ({ ...state, source_type: event.target.value }))} disabled={!canManage}>
                  {sourceOptions.map((value) => <option key={value} value={value}>{labelize(value)}</option>)}
                </Select>
              </Field>
              <Field label="来源 ID" hint="留空表示手工治理项；填写后后端会校验真实来源是否存在。">
                <Input value={form.source_id} onChange={(event) => setForm((state) => ({ ...state, source_id: event.target.value.replace(/[^\d]/g, '') }))} disabled={!canManage} />
              </Field>
              <Field label="发布类型">
                <Select value={form.release_type} onChange={(event) => setForm((state) => ({ ...state, release_type: event.target.value }))} disabled={!canManage}>
                  {['change', 'new', 'publish', 'rollback', 'emergency', 'config_change'].map((value) => <option key={value} value={value}>{labelize(value)}</option>)}
                </Select>
              </Field>
              <Field label="风险等级">
                <Select value={form.risk_level} onChange={(event) => setForm((state) => ({ ...state, risk_level: event.target.value }))} disabled={!canManage}>
                  {riskOptions.map((value) => <option key={value} value={value}>{labelize(value)}</option>)}
                </Select>
              </Field>
            </div>
            <Field label="标题">
              <Input value={form.title} onChange={(event) => setForm((state) => ({ ...state, title: event.target.value }))} disabled={!canManage} />
            </Field>
            <Field label="摘要">
              <Textarea value={form.summary} onChange={(event) => setForm((state) => ({ ...state, summary: event.target.value }))} disabled={!canManage} />
            </Field>
            <Field label="回滚计划">
              <Textarea value={form.rollback_plan} onChange={(event) => setForm((state) => ({ ...state, rollback_plan: event.target.value }))} disabled={!canManage} />
            </Field>
            <TechnicalDetails title="影响范围与 Diff" summary="必须是 JSON 对象">
              <div className="form-grid">
                <Field label="影响范围 JSON"><Textarea value={form.impact_text} onChange={(event) => setForm((state) => ({ ...state, impact_text: event.target.value }))} disabled={!canManage} /></Field>
                <Field label="Diff JSON"><Textarea value={form.diff_text} onChange={(event) => setForm((state) => ({ ...state, diff_text: event.target.value }))} disabled={!canManage} /></Field>
              </div>
            </TechnicalDetails>
            <div className="button-row">
              <Button variant="primary" disabled={!canManage || createMutation.isPending || !form.title || !form.summary} onClick={() => createMutation.mutate()}>
                {createMutation.isPending ? '创建中...' : '创建发布请求'}
              </Button>
              {canManage ? <Button onClick={() => setForm(emptyForm())}>重置</Button> : null}
            </div>
          </div>
        </CardBody>
      </Card>

      <ConfirmDialog
        open={Boolean(confirm)}
        title={confirm ? actionMeta[confirm.action].label : '确认治理动作'}
        description={confirm ? actionMeta[confirm.action].description : ''}
        consequence="该动作会写入治理发布事件流和后台审计日志。"
        confirmLabel={confirm ? actionMeta[confirm.action].label : '确认'}
        tone={confirm ? actionMeta[confirm.action].tone : 'default'}
        pending={actionMutation.isPending}
        onConfirm={() => confirm && actionMutation.mutate(confirm)}
        onCancel={() => setConfirm(null)}
      />
      {toast ? <Toast message={toast.message} tone={toast.tone} onClose={() => setToast(null)} /> : null}
    </AppShell>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/governance-releases',
  beforeLoad: () => { if (!getToken()) throw redirect({ to: '/login' }) },
  component: GovernanceReleasesPage,
})

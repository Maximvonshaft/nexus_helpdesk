import { createRoute, redirect } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import { routeAccess } from '@/lib/rbac'
import { canManageQA, canReadQA } from '@/lib/access'
import { formatDateTime, labelize, priorityTone, sanitizeDisplayText } from '@/lib/format'
import type { QAQueueSample } from '@/lib/types'
import { useSession } from '@/hooks/useAuth'
import { RequireCapability } from '@/components/security/RequireCapability'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { EmptyState } from '@/components/ui/EmptyState'
import { ErrorSummary } from '@/components/ui/ErrorSummary'
import { Field, Input, Select, Textarea } from '@/components/ui/Field'
import { GuidedWorkflow } from '@/components/ui/GuidedWorkflow'
import { MetricCard } from '@/components/ui/MetricCard'
import { PageHeader } from '@/components/ui/PageHeader'
import { TechnicalDetails } from '@/components/ui/TechnicalDetails'
import { Toast } from '@/components/ui/Toast'

const channels = [
  { value: 'all', label: '全部渠道' },
  { value: 'web_chat', label: 'WebChat' },
  { value: 'email', label: 'Email' },
  { value: 'whatsapp', label: 'WhatsApp' },
  { value: 'sms', label: '短信' },
]

const statuses = [
  { value: 'all', label: '全部状态' },
  { value: 'needs_review', label: '待质检' },
  { value: 'ready', label: '低风险样本' },
  { value: 'reviewed', label: '已评分' },
]

const riskLabels: Record<string, string> = {
  sla_breach: 'SLA 已触发',
  low_ai_confidence: 'AI 置信度低',
  missing_customer_evidence: '客户证据缺失',
  missing_resolution_summary: '缺少解决摘要',
  agent_owner_missing: '缺少负责人',
  reply_not_evidenced: '缺少回复证据',
  handoff_review_required: 'Handoff 需复核',
  urgent_case_sampling: '紧急样本',
  missing_policy_citation: '缺少政策引用',
  knowledge_gap: '知识缺口',
}

function riskLabel(risk: string) {
  return riskLabels[risk] ?? labelize(risk)
}

function scoreTone(score: number) {
  if (score < 65) return 'danger' as const
  if (score < 82) return 'warning' as const
  return 'success' as const
}

function sampleStatusTone(status: string) {
  if (status === 'reviewed') return 'success' as const
  if (status === 'needs_review') return 'warning' as const
  return 'default' as const
}

function selectedOrFirst(samples: QAQueueSample[], selectedTicketId: number | null) {
  if (!samples.length) return null
  return samples.find((sample) => sample.ticket_id === selectedTicketId) ?? samples[0]
}

function parseRiskText(value: string) {
  const seen = new Set<string>()
  return value.split(/\n|,/).map((item) => item.trim().toLowerCase().replace(/\s+/g, '_')).filter((item) => {
    if (!item || seen.has(item)) return false
    seen.add(item)
    return true
  })
}

function Scorecard({
  sample,
  canManage,
  pending,
  onSubmit,
}: {
  sample: QAQueueSample | null
  canManage: boolean
  pending: boolean
  onSubmit: (payload: { finalScore: number; risks: string[]; feedback: string; knowledgeGap: string; appealStatus: string; createTrainingTask: boolean; coachingSummary: string }) => void
}) {
  const [finalScore, setFinalScore] = useState('80')
  const [riskText, setRiskText] = useState('')
  const [feedback, setFeedback] = useState('')
  const [knowledgeGap, setKnowledgeGap] = useState('')
  const [appealStatus, setAppealStatus] = useState('not_started')
  const [createTrainingTask, setCreateTrainingTask] = useState(true)
  const [coachingSummary, setCoachingSummary] = useState('')

  useEffect(() => {
    if (!sample) return
    setFinalScore(String(sample.ai_pre_score))
    setRiskText(sample.risks.join('\n'))
    setFeedback(sample.feedback || '')
    setKnowledgeGap(sample.knowledge_gap_summary || '')
    setAppealStatus(sample.appeal_status || 'not_started')
    setCoachingSummary('')
    setCreateTrainingTask(true)
  }, [sample])

  if (!sample) {
    return <EmptyState title="请选择 QA 样本" description="评分、feedback 和培训任务必须绑定到真实 ticket。" />
  }

  const parsedScore = Number(finalScore)
  const disabledReason = canManage ? undefined : '当前账号缺少 qa.manage，只能查看 QA queue 和培训任务。'
  const submitDisabled = !canManage || pending || !feedback.trim() || Number.isNaN(parsedScore)

  return (
    <div data-testid="qa-scorecard">
      <div className="section-title">{sanitizeDisplayText(sample.ticket_no || `Ticket #${sample.ticket_id}`)}</div>
      <div className="section-subtitle">{sanitizeDisplayText(sample.title)}</div>
      <div className="button-row" style={{ marginTop: 12 }}>
        <Badge>{labelize(sample.sample_channel)}</Badge>
        <Badge tone={sampleStatusTone(sample.status)}>{labelize(sample.status)}</Badge>
        <Badge tone={scoreTone(sample.ai_pre_score)}>AI 预评分 {sample.ai_pre_score}</Badge>
      </div>
      <div className="form-grid" style={{ marginTop: 16 }}>
        <Field label="最终评分" required disabledReason={disabledReason}>
          <Input type="number" min={0} max={100} value={finalScore} disabled={!canManage} onChange={(event) => setFinalScore(event.target.value)} />
        </Field>
        <Field label="Appeal 状态" disabledReason={disabledReason}>
          <Select value={appealStatus} disabled={!canManage} onChange={(event) => setAppealStatus(event.target.value)}>
            <option value="not_started">未申诉</option>
            <option value="appeal_open">客服申诉中</option>
            <option value="accepted">申诉通过</option>
            <option value="rejected">申诉驳回</option>
          </Select>
        </Field>
      </div>
      <Field label="风险标签" hint="每行一个标签；保存后进入 QA review 证据。" disabledReason={disabledReason}>
        <Textarea rows={4} value={riskText} disabled={!canManage} onChange={(event) => setRiskText(event.target.value)} />
      </Field>
      <Field label="Coaching feedback" required disabledReason={disabledReason}>
        <Textarea rows={5} value={feedback} disabled={!canManage} onChange={(event) => setFeedback(event.target.value)} />
      </Field>
      <Field label="知识缺口" hint="填写后会生成 knowledge_gap 类型 training task。" disabledReason={disabledReason}>
        <Textarea rows={4} value={knowledgeGap} disabled={!canManage} onChange={(event) => setKnowledgeGap(event.target.value)} />
      </Field>
      <Field label="培训任务摘要" disabledReason={disabledReason}>
        <Textarea rows={3} value={coachingSummary} disabled={!canManage} onChange={(event) => setCoachingSummary(event.target.value)} />
      </Field>
      <label className="checkbox-row">
        <input type="checkbox" checked={createTrainingTask} disabled={!canManage} onChange={(event) => setCreateTrainingTask(event.target.checked)} />
        <span>保存评分时生成 coaching / knowledge-gap 培训任务</span>
      </label>
      <div className="button-row" style={{ marginTop: 12 }}>
        <Button disabled={submitDisabled} onClick={() => onSubmit({ finalScore: Math.max(0, Math.min(100, parsedScore)), risks: parseRiskText(riskText), feedback, knowledgeGap, appealStatus, createTrainingTask, coachingSummary })}>
          {pending ? '保存中…' : '保存评分并生成培训任务'}
        </Button>
      </div>
    </div>
  )
}

function QATrainingPage() {
  const session = useSession()
  const queryClient = useQueryClient()
  const [channel, setChannel] = useState('all')
  const [status, setStatus] = useState('all')
  const [selectedTicketId, setSelectedTicketId] = useState<number | null>(null)
  const [toast, setToast] = useState<{ message: string; tone?: 'default' | 'danger' | 'success' } | null>(null)
  const canRead = canReadQA(session.data)
  const canManage = canManageQA(session.data)

  const queue = useQuery({
    queryKey: ['qaTrainingQueue', channel, status],
    queryFn: () => api.qaTrainingQueue({ channel, status, limit: 60 }),
    enabled: canRead,
  })
  const tasks = useQuery({
    queryKey: ['qaTrainingTasks'],
    queryFn: () => api.qaTrainingTasks({ status: 'open', limit: 20 }),
    enabled: canRead,
  })

  const samples = useMemo(() => queue.data?.samples ?? [], [queue.data?.samples])
  const selectedSample = useMemo(() => selectedOrFirst(samples, selectedTicketId), [samples, selectedTicketId])
  useEffect(() => {
    if (!selectedTicketId && samples[0]) setSelectedTicketId(samples[0].ticket_id)
  }, [samples, selectedTicketId])

  const reviewMutation = useMutation({
    mutationFn: async (payload: { finalScore: number; risks: string[]; feedback: string; knowledgeGap: string; appealStatus: string; createTrainingTask: boolean; coachingSummary: string }) => {
      if (!selectedSample) throw new Error('请选择 QA 样本')
      return api.createQAReview({
        ticket_id: selectedSample.ticket_id,
        final_score: payload.finalScore,
        risks: payload.risks,
        feedback: payload.feedback.trim(),
        knowledge_gap_summary: payload.knowledgeGap.trim() || null,
        appeal_status: payload.appealStatus,
        create_training_task: payload.createTrainingTask,
        coaching_summary: payload.coachingSummary.trim() || null,
      })
    },
    onSuccess: async (review) => {
      setToast({ message: 'QA 评分已写入 timeline / audit，并生成培训闭环', tone: 'success' })
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['qaTrainingQueue'] }),
        queryClient.invalidateQueries({ queryKey: ['qaTrainingTasks'] }),
        queryClient.invalidateQueries({ queryKey: ['ticketTimeline', review.ticket_id] }),
      ])
    },
    onError: (err: Error) => setToast({ message: err.message || '保存 QA 评分失败', tone: 'danger' }),
  })

  const summary = queue.data?.summary
  const openTasks = tasks.data ?? []

  return (
    <AppShell>
      <RequireCapability requirement={routeAccess['/qa-training']}>
        <PageHeader
          eyebrow="运营管理"
          title="QA / Training Loop"
          description="把 WebChat、WebCall、Email 样本抽检、AI 预评分、coaching feedback、客服申诉和知识缺口闭环到真实 ticket timeline 与审计。"
          actions={<Button variant="secondary" onClick={() => { void queryClient.invalidateQueries({ queryKey: ['qaTrainingQueue'] }); void queryClient.invalidateQueries({ queryKey: ['qaTrainingTasks'] }) }}>刷新 QA 队列</Button>}
        />

        <div className="metrics-grid">
          <MetricCard label="样本数" value={summary?.total_samples ?? '—'} hint="来自真实 ticket queue" />
          <MetricCard label="待质检" value={summary?.needs_review ?? '—'} hint="有风险且未评分" />
          <MetricCard label="AI 平均预评分" value={summary?.average_ai_pre_score ?? '—'} hint="按 ticket 风险推导" />
          <MetricCard label="开放培训任务" value={summary?.open_training_tasks ?? openTasks.length} hint="coaching / knowledge gap" />
        </div>

        <Card className="soft">
          <CardHeader title="Training / Knowledge Gap Closed Loop" subtitle="客户问题先进入 QA 样本；评分后生成 coaching 或知识缺口任务，后续交给 AI Ops 发布和黄金测试。" />
          <CardBody>
            <GuidedWorkflow steps={[
              { title: '客户问题', description: '从真实 WebChat、WebCall、Email ticket 抽样。', status: 'done' },
              { title: '标记知识缺口', description: '低分或缺政策引用时沉淀 gap summary。', status: selectedSample?.knowledge_gap_summary ? 'done' : 'active' },
              { title: 'AI Ops 审核', description: '把 gap 转成知识草稿、Persona 或业务规则。' },
              { title: '黄金测试', description: '用 retrieval / persona 测试确认答案可复现。' },
              { title: '发布', description: '发布后进入客户回复和 AI runtime。' },
              { title: '命中监控', description: '观察后续 QA 样本是否仍出现同类风险。' },
            ]} />
          </CardBody>
        </Card>

        <div className="page-grid split-grid">
          <Card>
            <CardHeader title="QA Queue / Scorecard" subtitle="按渠道、风险和客服聚合；选择样本后在右侧评分。" />
            <CardBody>
              <div className="form-grid">
                <Field label="渠道">
                  <Select value={channel} onChange={(event) => setChannel(event.target.value)}>
                    {channels.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                  </Select>
                </Field>
                <Field label="状态">
                  <Select value={status} onChange={(event) => setStatus(event.target.value)}>
                    {statuses.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                  </Select>
                </Field>
              </div>
              {queue.isError ? <ErrorSummary errors={[queue.error?.message || '无法加载 QA queue']} /> : null}
              <div className="stack" data-testid="qa-training-queue">
                {samples.map((sample) => (
                  <button
                    key={sample.ticket_id}
                    className="list-card"
                    data-active={selectedSample?.ticket_id === sample.ticket_id ? 'true' : 'false'}
                    onClick={() => setSelectedTicketId(sample.ticket_id)}
                  >
                    <div className="list-card-main">
                      <strong>{sanitizeDisplayText(sample.ticket_no || `Ticket #${sample.ticket_id}`)}</strong>
                      <span>{sanitizeDisplayText(sample.title)}</span>
                      <small>{sanitizeDisplayText(sample.customer_name)} · {sanitizeDisplayText(sample.agent_name || '未分配')} · {formatDateTime(sample.updated_at)}</small>
                    </div>
                    <div className="list-card-meta">
                      <Badge>{labelize(sample.sample_channel)}</Badge>
                      <Badge tone={priorityTone(sample.priority)}>{labelize(sample.priority)}</Badge>
                      <Badge tone={sampleStatusTone(sample.status)}>{labelize(sample.status)}</Badge>
                      <Badge tone={scoreTone(sample.ai_pre_score)}>{sample.ai_pre_score}</Badge>
                    </div>
                    <div className="tag-row">
                      {sample.risks.slice(0, 4).map((risk) => <Badge key={risk} tone="warning">{riskLabel(risk)}</Badge>)}
                    </div>
                  </button>
                ))}
                {!samples.length && !queue.isLoading ? <EmptyState title="没有 QA 样本" description="当前筛选下没有需要质检的真实 ticket。" reason="可以切换渠道或状态，也可以等待新工单进入。" /> : null}
              </div>
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Scorecard / Coaching Feedback" subtitle="保存后写入 QA review、training task、ticket timeline 和 admin audit。" />
            <CardBody>
              {reviewMutation.isError ? <ErrorSummary errors={[reviewMutation.error?.message || '保存 QA review 失败']} /> : null}
              <Scorecard sample={selectedSample} canManage={canManage} pending={reviewMutation.isPending} onSubmit={(payload) => reviewMutation.mutate(payload)} />
              {!canManage ? <EmptyState title="只读 QA 模式" description="当前账号可以审计 QA 队列和培训任务，但不能创建评分。" reason="需要创建 scorecard 或 training task 时，请开通 qa.manage。" /> : null}
            </CardBody>
          </Card>
        </div>

        <div className="page-grid split-grid">
          <Card>
            <CardHeader title="Training Tasks" subtitle="由 QA review 生成的 coaching / knowledge-gap 待办。" />
            <CardBody>
              <div className="stack" data-testid="qa-training-tasks">
                {openTasks.map((task) => (
                  <div className="list-card" key={task.id}>
                    <div className="list-card-main">
                      <strong>{labelize(task.task_type)} · Ticket #{task.ticket_id}</strong>
                      <span>{sanitizeDisplayText(task.summary)}</span>
                      <small>{labelize(task.status)} · 到期 {formatDateTime(task.due_at)}</small>
                    </div>
                    {task.knowledge_gap_summary ? <div className="message" data-role="agent">{sanitizeDisplayText(task.knowledge_gap_summary)}</div> : null}
                  </div>
                ))}
                {!openTasks.length && !tasks.isLoading ? <EmptyState title="没有开放培训任务" description="保存 QA 评分并勾选生成任务后，这里会出现 coaching 或 knowledge-gap 待办。" /> : null}
              </div>
            </CardBody>
          </Card>
          <Card>
            <CardHeader title="Backend Evidence" subtitle="页面只接统一 API client；写入链路由后端契约测试覆盖。" />
            <CardBody>
              <TechnicalDetails title="QA 后端契约" summary="真实 API / RBAC / timeline / audit">
                <ul className="plain-list">
                  <li>GET /api/admin/qa-training/queue：qa.read 或 qa.manage。</li>
                  <li>POST /api/admin/qa-training/reviews：qa.manage，写 QAReview、QATrainingTask、TicketEvent、AdminAuditLog。</li>
                  <li>GET /api/admin/qa-training/training-tasks：qa.read 或 qa.manage。</li>
                </ul>
              </TechnicalDetails>
            </CardBody>
          </Card>
        </div>

        {toast ? <Toast message={toast.message} tone={toast.tone} onClose={() => setToast(null)} /> : null}
      </RequireCapability>
    </AppShell>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/qa-training',
  beforeLoad: () => { if (!getToken()) throw redirect({ to: '/login' }) },
  component: QATrainingPage,
})

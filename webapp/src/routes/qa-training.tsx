import { useState } from 'react'
import { createRoute, redirect, useNavigate } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import { routeAccess } from '@/lib/rbac'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { PageHeader } from '@/components/ui/PageHeader'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { DataTable } from '@/components/ui/DataTable'
import { EmptyState } from '@/components/ui/EmptyState'
import { Skeleton } from '@/components/ui/Skeleton'
import { ErrorSummary } from '@/components/ui/ErrorSummary'
import { Input } from '@/components/ui/Field'
import { MetricCard } from '@/components/ui/MetricCard'
import { RequireCapability } from '@/components/security/RequireCapability'
import { useAutoRefresh } from '@/hooks/useAutoRefresh'
import { formatDateTime, labelize, sanitizeDisplayText } from '@/lib/format'
import type { BadgeTone } from '@/lib/types'

function safeTone(value: string | null | undefined): BadgeTone {
  return value === 'danger' || value === 'warning' || value === 'success' ? value : 'default'
}

function scoreTone(score: number): BadgeTone {
  if (score < 70) return 'danger'
  if (score < 86) return 'warning'
  return 'success'
}

function blockTone(status: string): BadgeTone {
  if (status === 'implemented') return 'success'
  if (status === 'linked') return 'warning'
  if (status === 'not_implemented') return 'danger'
  return 'default'
}

function QATrainingPage() {
  const navigate = useNavigate()
  const client = useQueryClient()
  const autoRefresh = useAutoRefresh()
  const [appealReasons, setAppealReasons] = useState<Record<string, string>>({})
  const qa = useQuery({
    queryKey: ['qaTraining'],
    queryFn: api.qaTraining,
    refetchInterval: autoRefresh.enabled ? 30000 : false,
  })
  const appeal = useMutation({
    mutationFn: api.submitQATrainingAppeal,
    onSuccess: async () => {
      setAppealReasons({})
      await refresh()
    },
  })

  const goTarget = (href: string) => {
    if (href === '/workspace') navigate({ to: '/workspace' })
    else if (href === '/ai-control') navigate({ to: '/ai-control' })
    else navigate({ to: '/qa-training' })
  }

  const refresh = async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: ['qaTraining'] }),
      client.invalidateQueries({ queryKey: ['todayWorkbench'] }),
      client.invalidateQueries({ queryKey: ['controlTower'] }),
    ])
  }

  const submitAppeal = (item: NonNullable<typeof qa.data>['qa_queue'][number]) => {
    const reason = appealReasons[item.key]?.trim() || item.feedback || 'Agent disputes QA score and requests lead review.'
    appeal.mutate({
      sample_key: item.key,
      ticket_id: item.ticket_id,
      channel: item.channel,
      sample: item.sample || item.ticket_no || null,
      current_score: item.ai_pre_score,
      requested_score: Math.min(100, item.ai_pre_score + 10),
      reason,
      evidence: item.evidence || [],
    })
  }

  return (
    <AppShell>
      <PageHeader
        eyebrow="QA / Training"
        title="QA / Training / Knowledge Gap Loop"
        description="主管从真实通话、聊天、邮件和工单证据里抽样质检，形成培训任务和知识缺口闭环。"
        actions={<div className="button-row"><Button variant="secondary" onClick={() => autoRefresh.setEnabled(!autoRefresh.enabled)}>{autoRefresh.enabled ? '暂停刷新' : '恢复刷新'}</Button><Button onClick={() => void refresh()} disabled={qa.isFetching}>刷新</Button></div>}
      />

      <RequireCapability requirement={routeAccess['/qa-training']}>
        {qa.isLoading ? <Skeleton lines={6} /> : null}
        {qa.isError ? <ErrorSummary title="QA / Training 加载失败" errors={[qa.error instanceof Error ? qa.error.message : '请稍后重试']} action={<Button variant="secondary" onClick={() => void refresh()}>重试</Button>} /> : null}
        {appeal.isError ? <ErrorSummary title="Agent 申诉提交失败" errors={[appeal.error instanceof Error ? appeal.error.message : '请检查权限或稍后重试']} /> : null}
        {qa.data ? (
          <div className="stack" data-testid="qa-training-template-blocks">
            <div className="metrics-grid metrics-grid-wide" data-testid="qa-training-real-kpis">
              {qa.data.kpis.map((item) => (
                <div className="stack" key={item.key}>
                  <MetricCard label={item.label} value={item.value} hint={item.hint} />
                  <Badge tone={safeTone(item.tone)}>{labelize(item.tone)}</Badge>
                </div>
              ))}
            </div>

            <Card className="soft" data-testid="qa-training-queue">
              <CardHeader title="QA Queue / Scorecard Samples" subtitle="样本来自 WebCall、WebChat、Email 和工单 AI 质量字段，不使用前端 fixture。" />
              <CardBody>
                <DataTable
                  columns={['渠道', '样本', '客户 / Agent', 'AI 预评分', '风险', '反馈动作', '申诉']}
                  rows={qa.data.qa_queue.map((item) => [
                    <Badge>{sanitizeDisplayText(item.channel)}</Badge>,
                    <Button variant="secondary" onClick={() => goTarget(item.href)}>{sanitizeDisplayText(item.sample || item.ticket_no || `#${item.ticket_id}`)}</Button>,
                    <div className="stack"><span>{sanitizeDisplayText(item.customer_name || '-')}</span><small>{sanitizeDisplayText(item.agent_name || '未分配')}</small></div>,
                    <Badge tone={scoreTone(item.ai_pre_score)}>{item.ai_pre_score}</Badge>,
                    sanitizeDisplayText(item.risk),
                    sanitizeDisplayText(item.feedback),
                    item.appeal_status && item.appeal_status !== 'available'
                      ? <Badge tone={item.appeal_status === 'not_applicable' ? 'default' : 'warning'}>{sanitizeDisplayText(item.agent_appeal)}</Badge>
                      : <div className="stack" data-testid="qa-training-agent-appeal">
                          <Input
                            aria-label={`申诉理由 ${item.key}`}
                            value={appealReasons[item.key] || ''}
                            onChange={(event) => setAppealReasons((current) => ({ ...current, [item.key]: event.target.value }))}
                            placeholder="申诉理由"
                          />
                          <Button variant="secondary" disabled={appeal.isPending} onClick={() => submitAppeal(item)}>提交申诉</Button>
                        </div>,
                  ])}
                  empty={<EmptyState title="暂无 QA 样本" description="当前可见范围内没有需要质检的通话、聊天、邮件或工单样本。" />}
                />
              </CardBody>
            </Card>

            <div className="page-grid split-grid-wide">
              <Card data-testid="qa-training-scorecard">
                <CardHeader title="Scorecard" subtitle="按身份核验、证据引用、AI 质量、Email 发送和 timeline/audit 聚合。" />
                <CardBody>
                  <DataTable
                    columns={['项目', '分数', '证据来源', '下一步']}
                    rows={qa.data.scorecard.map((item) => [
                      sanitizeDisplayText(item.criterion),
                      <Badge tone={safeTone(item.tone)}>{item.score}</Badge>,
                      sanitizeDisplayText(item.evidence),
                      sanitizeDisplayText(item.next),
                    ])}
                  />
                </CardBody>
              </Card>

              <Card data-testid="qa-training-coaching-tasks">
                <CardHeader title="Coaching / Training Tasks" subtitle="读取 operator_tasks 中的培训任务，并从低分样本派生下一步。" />
                <CardBody>
                  <DataTable
                    columns={['任务', '负责人', '优先级', '状态', '来源', '下一步']}
                    rows={qa.data.training_tasks.map((item) => [
                      <Button variant="secondary" disabled={!item.enabled} onClick={() => goTarget(item.href)}>{sanitizeDisplayText(item.title)}</Button>,
                      sanitizeDisplayText(item.owner),
                      <Badge tone={item.priority <= 30 ? 'danger' : item.priority <= 70 ? 'warning' : 'default'}>{item.priority}</Badge>,
                      <Badge tone={item.status === 'pending' ? 'warning' : item.status === 'derived' ? 'default' : 'success'}>{labelize(item.status)}</Badge>,
                      sanitizeDisplayText(item.source),
                      sanitizeDisplayText(item.next),
                    ])}
                    empty={<EmptyState title="暂无培训任务" description="高风险样本出现后会在这里生成 coaching 下一步。" />}
                  />
                </CardBody>
              </Card>
            </div>

            <div className="page-grid split-grid-wide">
              <Card data-testid="qa-training-knowledge-gaps">
                <CardHeader title="Knowledge Gap Loop" subtitle="把错误回答、缺证据、缺政策引用转成 AI Ops 可处理的知识草稿或缺口。" />
                <CardBody>
                  <DataTable
                    columns={['缺口', '来源', '状态', 'Owner', '证据', '入口']}
                    rows={qa.data.knowledge_gaps.map((item) => [
                      sanitizeDisplayText(item.title),
                      sanitizeDisplayText(item.source),
                      <Badge tone={blockTone(item.status === 'draft' || item.status === 'sampled' ? 'linked' : item.status)}>{labelize(item.status)}</Badge>,
                      sanitizeDisplayText(item.owner),
                      sanitizeDisplayText(item.evidence),
                      <Button variant="secondary" onClick={() => goTarget(item.href)}>打开</Button>,
                    ])}
                    empty={<EmptyState title="暂无知识缺口" description="当前没有草稿知识或样本派生的缺口。" />}
                  />
                </CardBody>
              </Card>

              <Card data-testid="qa-training-loop-steps">
                <CardHeader title="训练闭环步骤" subtitle="客户问题、知识缺口、AI Ops 审核、黄金测试、发布和命中监控。" />
                <CardBody>
                  <DataTable
                    columns={['步骤', 'Owner', 'Artifact', '状态', '入口']}
                    rows={qa.data.loop_steps.map((item) => [
                      sanitizeDisplayText(item.step),
                      sanitizeDisplayText(item.owner),
                      sanitizeDisplayText(item.artifact),
                      <Badge tone={blockTone(item.status)}>{labelize(item.status)}</Badge>,
                      <Button variant="secondary" disabled={!item.enabled} onClick={() => goTarget(item.href)}>{item.enabled ? '打开' : '无权限'}</Button>,
                    ])}
                  />
                </CardBody>
              </Card>
            </div>

            <Card data-testid="qa-training-template-closure">
              <CardHeader title="v1.7.8 QA / Training 模板块落地状态" subtitle="明确哪些块已接真实后端 read-model，哪些仍缺 write endpoint。" />
              <CardBody>
                <DataTable
                  columns={['模板块', '后端契约', '状态', '证据', '入口']}
                  rows={qa.data.template_blocks.map((item) => [
                    sanitizeDisplayText(item.label),
                    sanitizeDisplayText(item.backend_contract),
                    <Badge tone={blockTone(item.status)}>{labelize(item.status)}</Badge>,
                    sanitizeDisplayText(item.evidence),
                    <Button variant="secondary" onClick={() => goTarget(item.href)}>查看</Button>,
                  ])}
                />
                <div className="section-subtitle" style={{ marginTop: 12 }}>Generated {formatDateTime(qa.data.generated_at)} · {sanitizeDisplayText(String(qa.data.facts.agent_appeal_write_endpoint))}</div>
              </CardBody>
            </Card>
          </div>
        ) : null}
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

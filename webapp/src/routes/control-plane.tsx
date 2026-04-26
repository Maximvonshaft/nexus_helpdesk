import { createRoute, redirect, useNavigate } from '@tanstack/react-router'
import { useQueries } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import { canManageAIConfig, canManageChannels, canViewControlPlane } from '@/lib/access'
import { useSession } from '@/hooks/useAuth'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { DataTable } from '@/components/ui/DataTable'
import { EmptyState } from '@/components/ui/EmptyState'
import { PageHeader } from '@/components/ui/PageHeader'
import { formatDateTime, labelize, sanitizeDisplayText } from '@/lib/format'

function ControlPlanePage() {
  const session = useSession()
  const navigate = useNavigate()
  const canSeeControlPlane = canViewControlPlane(session.data)
  const canSeeAI = canManageAIConfig(session.data)
  const canSeeChannels = canManageChannels(session.data)

  const [personas, knowledge, tasks] = useQueries({
    queries: [
      { queryKey: ['control-plane-personas'], queryFn: api.personaProfiles, enabled: canSeeAI },
      { queryKey: ['control-plane-knowledge'], queryFn: api.knowledgeItems, enabled: canSeeAI },
      { queryKey: ['control-plane-channel-tasks'], queryFn: api.channelOnboardingTasks, enabled: canSeeChannels },
    ],
  })

  if (session.data && !canSeeControlPlane) {
    navigate({ to: '/' })
    return null
  }

  const personaRows = personas.data?.profiles ?? []
  const knowledgeRows = knowledge.data?.items ?? []
  const taskRows = tasks.data?.tasks ?? []
  const publishedPersonaCount = personaRows.filter((item) => item.published_version > 0).length
  const activeKnowledgeCount = knowledgeRows.filter((item) => item.status === 'active' && item.published_version > 0).length
  const openTaskCount = taskRows.filter((item) => !['completed', 'cancelled'].includes(item.status)).length

  return (
    <AppShell>
      <PageHeader
        eyebrow="控制面"
        title="AI 与渠道治理总览"
        description="先把 persona、knowledge、channel-control 三条线从 PR #6 的大补丁拆成可审查、可回滚、可验证的小能力。当前页面只做治理总览，不触发真实客户通道操作。"
      />

      {!canSeeControlPlane ? (
        <Card>
          <CardHeader title="无权限访问" subtitle="控制面只对具备 AI 配置、渠道或运营治理权限的账号开放。" />
          <CardBody><div className="message" data-role="agent">请回到工单处理页面继续客服作业。</div></CardBody>
        </Card>
      ) : (
        <>
          <div className="metrics-grid metrics-grid-wide">
            <div className="metric-card"><div className="metric-label">Persona 总数</div><div className="metric-value">{canSeeAI ? personaRows.length : '—'}</div><div className="metric-hint">已发布 {canSeeAI ? publishedPersonaCount : '—'} 项</div></div>
            <div className="metric-card"><div className="metric-label">Knowledge 总数</div><div className="metric-value">{canSeeAI ? knowledgeRows.length : '—'}</div><div className="metric-hint">生效 {canSeeAI ? activeKnowledgeCount : '—'} 项</div></div>
            <div className="metric-card"><div className="metric-label">Channel 任务</div><div className="metric-value">{canSeeChannels ? taskRows.length : '—'}</div><div className="metric-hint">待处理 {canSeeChannels ? openTaskCount : '—'} 项</div></div>
            <div className="metric-card"><div className="metric-label">能力边界</div><div className="metric-value">只读</div><div className="metric-hint">本页不执行发布、绑定或发送</div></div>
          </div>

          <Card className="soft">
            <CardHeader title="治理边界" subtitle="这不是客服一线页面，而是主管/管理员用来确认配置状态的控制面入口。" />
            <CardBody>
              <div className="guide-grid">
                <div className="guide-item"><strong>Persona</strong><span>定义 AI 的语气、升级原则和市场差异。</span></div>
                <div className="guide-item"><strong>Knowledge</strong><span>沉淀公告、FAQ、规则与可检索知识。</span></div>
                <div className="guide-item"><strong>Channel-control</strong><span>记录渠道接入任务，不直接调用真实 OpenClaw 或客户通道。</span></div>
              </div>
            </CardBody>
          </Card>

          <div className="page-grid split-grid-wide">
            <Card>
              <CardHeader title="Persona Profiles" subtitle="AI 回复人格与风格配置。" />
              <CardBody>
                {canSeeAI ? (
                  <DataTable
                    columns={['Key', '名称', '渠道', '语言', '状态', '版本']}
                    rows={personaRows.slice(0, 12).map((item) => [
                      sanitizeDisplayText(item.profile_key),
                      sanitizeDisplayText(item.name),
                      sanitizeDisplayText(item.channel),
                      sanitizeDisplayText(item.language),
                      item.is_active ? '启用' : '停用',
                      item.published_version > 0 ? `v${item.published_version}` : '未发布',
                    ])}
                  />
                ) : <EmptyState text="当前账号无 AI 配置治理权限。" />}
              </CardBody>
            </Card>

            <Card>
              <CardHeader title="Knowledge Items" subtitle="已沉淀的知识项、FAQ 和规则。" />
              <CardBody>
                {canSeeAI ? (
                  <DataTable
                    columns={['Key', '标题', '状态', '渠道', '受众', '版本']}
                    rows={knowledgeRows.slice(0, 12).map((item) => [
                      sanitizeDisplayText(item.item_key),
                      sanitizeDisplayText(item.title),
                      labelize(item.status),
                      sanitizeDisplayText(item.channel),
                      labelize(item.audience_scope),
                      item.published_version > 0 ? `v${item.published_version}` : '未发布',
                    ])}
                  />
                ) : <EmptyState text="当前账号无 AI 配置治理权限。" />}
              </CardBody>
            </Card>
          </div>

          <Card>
            <CardHeader title="Channel Onboarding Tasks" subtitle="渠道接入与 OpenClaw 账号绑定的任务台账。这里只展示治理状态，不做真实绑定。" />
            <CardBody>
              {canSeeChannels ? (
                <DataTable
                  columns={['Provider', '状态', '目标槽位', '显示名', 'OpenClaw账号', '更新时间']}
                  rows={taskRows.slice(0, 16).map((item) => [
                    labelize(item.provider),
                    labelize(item.status),
                    sanitizeDisplayText(item.target_slot),
                    sanitizeDisplayText(item.desired_display_name),
                    sanitizeDisplayText(item.openclaw_account_id),
                    formatDateTime(item.updated_at),
                  ])}
                />
              ) : <EmptyState text="当前账号无渠道治理权限。" />}
            </CardBody>
          </Card>
        </>
      )}
    </AppShell>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/control-plane',
  beforeLoad: () => { if (!getToken()) throw redirect({ to: '/login' }) },
  component: ControlPlanePage,
})

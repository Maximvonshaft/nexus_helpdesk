import { PropsWithChildren, useEffect, useMemo, useState } from 'react'
import { Link, Outlet, useNavigate, useRouterState } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { useSession, useLogout } from '@/hooks/useAuth'
import { useAutoRefresh } from '@/hooks/useAutoRefresh'
import { CommandPalette } from '@/components/ui/CommandPalette'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { labelize } from '@/lib/format'
import { canViewOps, roleWorkspaceHint } from '@/lib/access'
import { canAccess, routeAccess, type AccessRequirement } from '@/lib/rbac'
import { NoAccessCard } from '@/components/security/RequireCapability'
import { OperationsShell } from '@/shared/layout/OperationsShell'
import type { BadgeTone } from '@/lib/types'

type NavItem = {
  to: string
  label: string
  hint: string
  access?: AccessRequirement
  attention?: 'runtime'
}

type NavGroup = {
  label: string
  items: string[]
}

type WorkflowSignal = {
  label: string
  value: string
  tone?: BadgeTone
}

type WorkflowContext = {
  eyebrow: string
  title: string
  description: string
  signals: WorkflowSignal[]
  nextAction: string
}

const nav: NavItem[] = [
  { to: '/', label: '今日工作台', hint: '今日待办、SLA 与公告' },
  { to: '/workspace', label: '工单 / 客户 / 运单查询', hint: '处理工单、查单、客户上下文与闭环' },
  { to: '/webchat', label: 'WebChat 收件箱', hint: '聊天接管与客户回复' },
  { to: '/email', label: 'Email 工作台', hint: '邮件队列与回复草稿', access: routeAccess['/email'] },
  { to: '/webcall', label: 'WebCall 工作台', hint: '来电队列与通话处理', access: routeAccess['/webcall'] },
  { to: '/control-tower', label: '运营报表', hint: '主管队列、SLA、治理动作', access: routeAccess['/control-tower'] },
  { to: '/qa-training', label: 'QA / Training', hint: '质检样本、培训与知识缺口', access: routeAccess['/qa-training'] },
  { to: '/accounts', label: '发送线路', hint: '账号与兜底线路', access: routeAccess['/accounts'] },
  { to: '/outbound-email', label: 'Email 账号配置', hint: 'SMTP 配置与测试发送', access: routeAccess['/outbound-email'] },
  { to: '/provider-credentials', label: 'Provider 授权', hint: '云端授权管理', access: routeAccess['/provider-credentials'] },
  { to: '/knowledge-studio', label: '知识库', hint: '知识发布、检索与冲突', access: routeAccess['/knowledge-studio'] },
  { to: '/bulletins', label: '公告口径', hint: '统一客服话术', access: routeAccess['/bulletins'] },
  { to: '/ai-control', label: 'AI 规则', hint: '助手口径治理', access: routeAccess['/ai-control'] },
  { to: '/persona-builder', label: 'AI Persona', hint: '人格、匹配与发布证据', access: routeAccess['/persona-builder'] },
  { to: '/runtime', label: '运行恢复', hint: 'dead/requeue 自助处理', access: routeAccess['/runtime'], attention: 'runtime' },
  { to: '/control-plane', label: '控制面', hint: '高级治理入口', access: routeAccess['/control-plane'] },
  { to: '/webcall-ai-demo', label: 'WebCall AI Demo', hint: '内部语音 AI 沙盒', access: routeAccess['/webcall-ai-demo'] },
  { to: '/users', label: '账号权限', hint: '人员与权限', access: routeAccess['/users'] },
  { to: '/security', label: '权限与审计', hint: '只读矩阵与审计', access: routeAccess['/security'] },
]

const navGroups: NavGroup[] = [
  { label: 'Operations', items: ['/', '/workspace', '/webchat', '/email', '/webcall', '/control-tower', '/qa-training'] },
  { label: 'Channels', items: ['/accounts', '/outbound-email', '/provider-credentials'] },
  { label: 'Knowledge', items: ['/knowledge-studio', '/bulletins'] },
  { label: 'AI Governance', items: ['/ai-control', '/persona-builder'] },
  { label: 'Runtime', items: ['/runtime', '/control-plane', '/webcall-ai-demo'] },
  { label: 'Admin', items: ['/users', '/security'] },
]

const legacyNavigationAliases = ['今日总览', '日常处理', '渠道与授权', '治理与运维', '处理工单', 'WebChat 收件箱', 'WebCall 工作台', '客户 / 运单查询']
const legacyNavigationRouteAliases = { '渠道与授权': '/outbound-email' }
void legacyNavigationAliases
void legacyNavigationRouteAliases

function isActiveNavPath(pathname: string, target: string) {
  return pathname === target || (target !== '/' && pathname.startsWith(`${target}/`))
}

function routeRequirementForPath(pathname: string): AccessRequirement | undefined {
  return Object.entries(routeAccess)
    .sort((a, b) => b[0].length - a[0].length)
    .find(([route]) => pathname === route || pathname.startsWith(`${route}/`))?.[1]
}

function workflowContextForPath(pathname: string, runtimeNeedsAttention: boolean, runtimeAttentionCount: number): WorkflowContext {
  if (pathname.startsWith('/webchat')) {
    return {
      eyebrow: 'Unified Agent Inbox',
      title: 'WebChat / WhatsApp 会话模型',
      description: '围绕接管、释放、AI 监控、未读和安全门组织对客会话。',
      signals: [
        { label: '队列', value: 'requested / mine / AI active', tone: 'default' },
        { label: '证据', value: 'thread + action audit', tone: 'success' },
        { label: '安全', value: 'reply safety gate', tone: 'warning' },
      ],
      nextAction: '下一阶段把 WhatsApp conversation state 接入同一收件箱模型。',
    }
  }
  if (pathname.startsWith('/workspace')) {
    return {
      eyebrow: 'Case Workspace',
      title: '客户、工单、运单处理台',
      description: '目标是一单内完成事实核验、客户回复、内部备注和闭环动作。',
      signals: [
        { label: '事实', value: 'ticket + shipment evidence', tone: 'success' },
        { label: '记忆', value: 'customer context target', tone: 'default' },
        { label: '动作', value: 'safe next action', tone: 'default' },
      ],
      nextAction: '后续把客户记忆面板和 AI 建议面板收敛到右侧上下文区。',
    }
  }
  if (pathname.startsWith('/knowledge-studio')) {
    return {
      eyebrow: 'Knowledge + QMD',
      title: '知识与检索诊断',
      description: '保留上传、发布、检索、冲突和 golden test，并扩展 QMD 诊断面。',
      signals: [
        { label: 'QMD', value: 'query and memory diagnostics', tone: 'default' },
        { label: 'Ontology', value: 'Ontology / Status Dictionary', tone: 'default' },
        { label: '质量', value: 'conflict + golden test', tone: 'warning' },
      ],
      nextAction: '后续新增 scope、index、shadow 结果的显式诊断视图。',
    }
  }
  if (pathname.startsWith('/persona-builder') || pathname.startsWith('/ai-control')) {
    return {
      eyebrow: 'AI Governance',
      title: 'Persona、规则、记忆配置治理',
      description: '把 persona、SOP、policy、memory config 和发布闸门组织成同一治理流。',
      signals: [
        { label: '形态', value: 'business form first', tone: 'success' },
        { label: '验证', value: 'runtime evidence', tone: 'default' },
        { label: '发布', value: 'review / publish / rollback', tone: 'default' },
      ],
      nextAction: '后续把 JSON 高级模式继续保留，但默认展示业务表单和测试证据。',
    }
  }
  if (pathname.startsWith('/accounts') || pathname.startsWith('/outbound-email') || pathname.startsWith('/provider-credentials')) {
    return {
      eyebrow: 'Channel Admin',
      title: '渠道账号和发送 readiness',
      description: '统一展示 WebChat、WhatsApp、Email、WebCall 相关账号和发送线路健康。',
      signals: [
        { label: 'WhatsApp', value: 'QR + sidecar target', tone: 'success' },
        { label: 'Email', value: 'SMTP readiness', tone: 'default' },
        { label: '授权', value: 'provider credentials', tone: 'warning' },
      ],
      nextAction: '后续把 WhatsApp QR 绑定状态和 smoke 结果纳入同一个渠道面板。',
    }
  }
  if (pathname.startsWith('/runtime') || pathname.startsWith('/control-plane')) {
    return {
      eyebrow: 'Runtime Observatory',
      title: runtimeNeedsAttention ? '运行状态需要处理' : '运行状态观察',
      description: '把 release metadata、readyz、队列、dead job、provider runtime 和 rollback 聚合展示。',
      signals: [
        { label: 'dead', value: `${runtimeAttentionCount}`, tone: runtimeNeedsAttention ? 'danger' : 'success' },
        { label: '恢复', value: 'requeue audited', tone: 'default' },
        { label: '发布', value: 'healthz / readyz target', tone: 'warning' },
      ],
      nextAction: '后续把 candidate smoke 和 last known-good release 放进运维观察页。',
    }
  }
  return {
    eyebrow: 'Operations Cockpit',
    title: '客服运营控制台',
    description: '统一客服处理、渠道状态、知识治理、AI 治理和运行观测。',
    signals: [
      { label: 'Handle', value: 'case + conversation', tone: 'default' },
      { label: 'Govern', value: 'AI + knowledge + ontology', tone: 'default' },
      { label: 'Observe', value: 'runtime + release', tone: runtimeNeedsAttention ? 'danger' : 'success' },
    ],
    nextAction: '优先从 Unified Agent Inbox 和 Case Workspace 继续落地。',
  }
}

function Sidebar({
  availableNav,
  location,
  userLabel,
  workspaceHint,
  runtimeNeedsAttention,
  runtimeAttentionCount,
  onOpenCommand,
  onLogout,
}: {
  availableNav: NavItem[]
  location: { pathname: string }
  userLabel: string
  workspaceHint: string
  runtimeNeedsAttention: boolean
  runtimeAttentionCount: number
  onOpenCommand: () => void
  onLogout: () => void
}) {
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-kicker">客服协同中心</div>
        <h1>客服工作台</h1>
        <div className="subtle">按真实客服工作流组织：先处理渠道，再跟进工单，再查询客户与运单。</div>
      </div>
      <nav className="nav" data-testid="operator-primary-navigation" aria-label="客服运营后台主导航">
        {navGroups.map((group) => {
          const groupItems = group.items
            .map((to) => availableNav.find((item) => item.to === to))
            .filter(Boolean) as NavItem[]
          if (!groupItems.length) return null
          return (
            <div className="nav-group" key={group.label}>
              <div className="nav-group-label">{group.label}</div>
              {groupItems.map((item) => {
                const active = isActiveNavPath(location.pathname, item.to)
                const showRuntimeAttention = item.attention === 'runtime' && runtimeNeedsAttention
                return (
                  <Link key={item.to} to={item.to} data-active={active ? 'true' : 'false'} aria-current={active ? 'page' : undefined}>
                    <span>{item.label}</span>
                    <small>{item.hint}</small>
                    {showRuntimeAttention ? <Badge tone="danger">需处理 {runtimeAttentionCount}</Badge> : null}
                  </Link>
                )
              })}
            </div>
          )
        })}
      </nav>
      <div className="card soft sidebar-card">
        <div className="section-title">当前账号</div>
        <div className="section-subtitle">{userLabel}</div>
        <div className="sidebar-helper">{workspaceHint}</div>
        <div className="button-row" style={{ marginTop: 12 }}>
          <Button variant="secondary" onClick={onOpenCommand}>快捷操作</Button>
          <Button variant="ghost" onClick={onLogout}>退出登录</Button>
        </div>
      </div>
    </aside>
  )
}

function Topbar({
  canSeeOps,
  runtimeNeedsAttention,
  runtimeWarnings,
  autoRefreshEnabled,
  onToggleRefresh,
  onOpenCommand,
}: {
  canSeeOps: boolean
  runtimeNeedsAttention: boolean
  runtimeWarnings: number
  autoRefreshEnabled: boolean
  onToggleRefresh: () => void
  onOpenCommand: () => void
}) {
  return (
    <div className="topbar">
      <div>
        <div className="section-title">客服运营台</div>
        <div className="section-subtitle">先处理 WebChat / WebCall / Email，再跟进工单；客户或运单查询请走工单与查询入口。</div>
      </div>
      <div className="button-row topbar-status">
        <Badge tone={!canSeeOps ? 'default' : runtimeNeedsAttention ? 'danger' : runtimeWarnings ? 'warning' : 'success'}>
          {!canSeeOps ? '客服模式' : runtimeNeedsAttention ? '运行需处理' : runtimeWarnings ? '需要关注' : '运行正常'}
        </Badge>
        <Badge>{autoRefreshEnabled ? '自动刷新已开启' : '自动刷新已暂停'}</Badge>
        <Button variant="secondary" onClick={onToggleRefresh}>{autoRefreshEnabled ? '暂停刷新' : '恢复刷新'}</Button>
        <Button variant="secondary" onClick={onOpenCommand}>快捷键 ⌘/Ctrl + K</Button>
      </div>
    </div>
  )
}

function OperationsContextPanel({ context }: { context: WorkflowContext }) {
  return (
    <div className="ops-context-card">
      <div className="page-eyebrow">{context.eyebrow}</div>
      <h2>{context.title}</h2>
      <p>{context.description}</p>
      <div className="ops-context-list">
        {context.signals.map((signal) => (
          <div className="ops-context-item" key={`${signal.label}-${signal.value}`}>
            <span>{signal.label}</span>
            <Badge tone={signal.tone ?? 'default'}>{signal.value}</Badge>
          </div>
        ))}
      </div>
      <div className="ops-context-next">
        <strong>下一步</strong>
        <span>{context.nextAction}</span>
      </div>
    </div>
  )
}

function OperationsEventDock({
  canSeeOps,
  runtimeNeedsAttention,
  runtimeAttentionCount,
  runtimeWarnings,
  deadJobs,
  deadOutbound,
  autoRefreshEnabled,
}: {
  canSeeOps: boolean
  runtimeNeedsAttention: boolean
  runtimeAttentionCount: number
  runtimeWarnings: number
  deadJobs: number
  deadOutbound: number
  autoRefreshEnabled: boolean
}) {
  return (
    <>
      <div className="ops-event-item">
        <span>模式</span>
        <strong>{canSeeOps ? 'Ops observable' : 'Agent focused'}</strong>
      </div>
      <div className="ops-event-item">
        <span>运行</span>
        <strong>{runtimeNeedsAttention ? `需处理 ${runtimeAttentionCount}` : runtimeWarnings ? `警告 ${runtimeWarnings}` : '正常'}</strong>
      </div>
      <div className="ops-event-item">
        <span>队列</span>
        <strong>jobs {deadJobs} / outbound {deadOutbound}</strong>
      </div>
      <div className="ops-event-item">
        <span>刷新</span>
        <strong>{autoRefreshEnabled ? '自动' : '暂停'}</strong>
      </div>
    </>
  )
}

export function AppShell({ children }: PropsWithChildren) {
  const { location } = useRouterState()
  const navigate = useNavigate()
  const session = useSession()
  const logout = useLogout()
  const [commandOpen, setCommandOpen] = useState(false)
  const autoRefresh = useAutoRefresh(true)
  const canSeeOps = canViewOps(session.data)

  const runtime = useQuery({
    queryKey: ['runtimeHealth-shell'],
    queryFn: api.runtimeHealth,
    refetchInterval: autoRefresh.enabled ? 15000 : false,
    enabled: !!session.data && canSeeOps,
  })
  const queue = useQuery({
    queryKey: ['queueSummary-shell'],
    queryFn: api.queueSummary,
    refetchInterval: autoRefresh.enabled ? 15000 : false,
    enabled: !!session.data && canSeeOps,
  })

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        setCommandOpen((s) => !s)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  useEffect(() => {
    if (location.pathname !== '/login' && !session.isLoading && !session.isFetching && !session.data) {
      navigate({ to: '/login', replace: true })
    }
  }, [location.pathname, navigate, session.data, session.isFetching, session.isLoading])

  const userLabel = useMemo(
    () => session.data ? `${session.data.display_name} · ${labelize(session.data.role)}` : '未登录',
    [session.data],
  )
  const availableNav = useMemo(
    () => nav.filter((item) => item.access ? canAccess(session.data, item.access) : true),
    [session.data],
  )
  const routeRequirement = useMemo(() => routeRequirementForPath(location.pathname), [location.pathname])
  const routeDenied = Boolean(session.data && routeRequirement && !canAccess(session.data, routeRequirement))
  const runtimeWarnings = runtime.data?.warnings?.length ?? 0
  const deadJobs = queue.data?.dead_jobs ?? 0
  const deadOutbound = queue.data?.dead_outbound ?? 0
  const runtimeNeedsAttention = Boolean(canSeeOps && (
    runtimeWarnings > 0 ||
    deadJobs > 0 ||
    deadOutbound > 0 ||
    (runtime.data?.dead_sync_jobs ?? 0) > 0 ||
    (runtime.data?.dead_attachment_jobs ?? 0) > 0
  ))
  const runtimeAttentionCount = deadJobs + deadOutbound + (runtime.data?.dead_sync_jobs ?? 0) + (runtime.data?.dead_attachment_jobs ?? 0)
  const workflowContext = useMemo(
    () => workflowContextForPath(location.pathname, runtimeNeedsAttention, runtimeAttentionCount),
    [location.pathname, runtimeAttentionCount, runtimeNeedsAttention],
  )

  if (!session.data && (session.isLoading || session.isFetching)) {
    return <div className="auth-shell"><div className="auth-card" role="status" aria-live="polite">正在确认登录状态…</div></div>
  }
  if (!session.data) return null

  const shellContent = routeDenied ? (
    <NoAccessCard
      title="当前账号无权访问此页面"
      description="该入口需要额外 capability；左侧菜单已自动隐藏无权限入口，直接 URL 访问会在这里被拦截。"
      action="请联系主管或管理员开通对应权限，或返回今日工作台继续处理可见任务。"
    />
  ) : (children ?? <Outlet />)

  return (
    <>
      <OperationsShell
        sidebar={(
          <Sidebar
            availableNav={availableNav}
            location={location}
            userLabel={userLabel}
            workspaceHint={roleWorkspaceHint(session.data)}
            runtimeNeedsAttention={runtimeNeedsAttention}
            runtimeAttentionCount={runtimeAttentionCount}
            onOpenCommand={() => setCommandOpen(true)}
            onLogout={() => {
              logout()
              navigate({ to: '/login', replace: true })
            }}
          />
        )}
        topbar={(
          <Topbar
            canSeeOps={canSeeOps}
            runtimeNeedsAttention={runtimeNeedsAttention}
            runtimeWarnings={runtimeWarnings}
            autoRefreshEnabled={autoRefresh.enabled}
            onToggleRefresh={() => autoRefresh.setEnabled(!autoRefresh.enabled)}
            onOpenCommand={() => setCommandOpen(true)}
          />
        )}
        contextPanel={<OperationsContextPanel context={workflowContext} />}
        eventDock={(
          <OperationsEventDock
            canSeeOps={canSeeOps}
            runtimeNeedsAttention={runtimeNeedsAttention}
            runtimeAttentionCount={runtimeAttentionCount}
            runtimeWarnings={runtimeWarnings}
            deadJobs={deadJobs}
            deadOutbound={deadOutbound}
            autoRefreshEnabled={autoRefresh.enabled}
          />
        )}
      >
        {shellContent}
      </OperationsShell>
      <CommandPalette open={commandOpen} onClose={() => setCommandOpen(false)} />
    </>
  )
}

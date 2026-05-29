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
import { canAccess, routeAccess } from '@/lib/rbac'

const nav = [
  { to: '/', label: '今日总览', hint: '异常与优先入口' },
  { to: '/workspace', label: '处理工单', hint: '回复、分配、闭环' },
  { to: '/webchat', label: 'WebChat 收件箱', hint: '客户实时来信' },
  { to: '/webcall', label: 'WebCall 工作台', hint: '来电、接管与 AI 建议', access: routeAccess['/webcall'] },
  { to: '/email', label: 'Email 工作台', hint: '邮件队列、草稿与发送', access: routeAccess['/email'] },
  { to: '/runtime', label: '运行恢复', hint: 'dead/requeue 自助处理', access: routeAccess['/runtime'], attention: 'runtime' },
  { to: '/webcall-ai-demo', label: 'WebCall AI Demo', hint: '内部语音 AI 沙盒', access: routeAccess['/webcall-ai-demo'] },
  { to: '/webcall-ai-monitor', label: 'WebCall AI Monitor', hint: 'AI 通话健康与会话', access: routeAccess['/webcall-ai-monitor'] },
  { to: '/provider-credentials', label: 'Code X 授权', hint: '云端授权与 Token 托管', access: routeAccess['/provider-credentials'] },
  { to: '/accounts', label: '发送线路', hint: '账号与兜底线路', access: routeAccess['/accounts'] },
  { to: '/outbound-email', label: 'Email 账号', hint: 'SMTP 配置与测试发送', access: routeAccess['/outbound-email'] },
  { to: '/bulletins', label: '公告口径', hint: '统一客服话术', permission: 'bulletins' },
  { to: '/ai-control', label: 'AI 规则', hint: '助手口径治理', access: routeAccess['/ai-control'] },
  { to: '/control-plane', label: '控制面', hint: '高级治理入口', access: routeAccess['/control-plane'] },
  { to: '/users', label: '账号权限', hint: '人员与权限', access: routeAccess['/users'] },
]

const navGroups = [
  { label: '日常处理', items: ['/', '/workspace', '/webchat', '/webcall', '/email', '/bulletins'] },
  { label: '渠道与授权', items: ['/accounts', '/outbound-email', '/provider-credentials'] },
  { label: '治理与运维', items: ['/runtime', '/ai-control', '/control-plane', '/users', '/webcall-ai-demo', '/webcall-ai-monitor'] },
]

function isActiveNavPath(pathname: string, target: string) {
  return pathname === target || (target !== '/' && pathname.startsWith(`${target}/`))
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

  const userLabel = useMemo(() => {
    if (!session.data) return '未登录'
    return `${session.data.display_name} · ${labelize(session.data.role)}`
  }, [session.data])

  const availableNav = useMemo(() => nav.filter((item) => {
    if ('access' in item && item.access) return canAccess(session.data, item.access)
    if (item.permission === 'bulletins') return true
    return true
  }), [session.data])

  const runtimeNeedsAttention = Boolean(
    canSeeOps
    && ((runtime.data?.warnings?.length ?? 0) > 0
      || (queue.data?.dead_jobs ?? 0) > 0
      || (queue.data?.dead_outbound ?? 0) > 0
      || (runtime.data?.dead_sync_jobs ?? 0) > 0
      || (runtime.data?.dead_attachment_jobs ?? 0) > 0)
  )
  const runtimeAttentionCount = (queue.data?.dead_jobs ?? 0) + (queue.data?.dead_outbound ?? 0) + (runtime.data?.dead_sync_jobs ?? 0) + (runtime.data?.dead_attachment_jobs ?? 0)

  if (!session.data && (session.isLoading || session.isFetching)) {
    return <div className="auth-shell"><div className="auth-card">正在确认登录状态…</div></div>
  }

  if (!session.data) return null

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-kicker">客服协同中心</div>
          <h1>客服工作台</h1>
          <div className="subtle">工单、客户消息、公告与渠道统一处理</div>
        </div>
        <nav className="nav" data-testid="operator-primary-navigation">
          {navGroups.map((group) => {
            const groupItems = group.items.map((to) => availableNav.find((item) => item.to === to)).filter(Boolean) as typeof nav
            if (!groupItems.length) return null
            return (
              <div className="nav-group" key={group.label}>
                <div className="nav-group-label">{group.label}</div>
                {groupItems.map((item) => {
                  const active = isActiveNavPath(location.pathname, item.to)
                  const showRuntimeAttention = item.attention === 'runtime' && runtimeNeedsAttention
                  return (
                    <Link key={item.to} to={item.to} data-active={active ? 'true' : 'false'}>
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
          <div className="sidebar-helper">{roleWorkspaceHint(session.data)}</div>
          <div className="button-row" style={{ marginTop: 12 }}>
            <Button variant="secondary" onClick={() => setCommandOpen(true)}>快捷操作</Button>
            <Button variant="ghost" onClick={() => { logout(); navigate({ to: '/login', replace: true }) }}>退出登录</Button>
          </div>
        </div>
      </aside>
      <main>
        <div className="topbar">
          <div>
            <div className="section-title">客服运营台</div>
            <div className="section-subtitle">先看今日总览，再进工单/WebChat；出现 dead 或同步异常时直接进入运行恢复。</div>
          </div>
          <div className="button-row topbar-status">
            <Badge tone={!canSeeOps ? 'default' : runtimeNeedsAttention ? 'danger' : runtime.data?.warnings?.length ? 'warning' : 'success'}>{!canSeeOps ? '客服模式' : runtimeNeedsAttention ? '运行需处理' : runtime.data?.warnings?.length ? '需要关注' : '运行正常'}</Badge>
            <Badge>{autoRefresh.enabled ? '自动刷新已开启' : '自动刷新已暂停'}</Badge>
            <Button variant="secondary" onClick={() => autoRefresh.setEnabled(!autoRefresh.enabled)}>{autoRefresh.enabled ? '暂停刷新' : '恢复刷新'}</Button>
            <Button variant="secondary" onClick={() => setCommandOpen(true)}>快捷键 ⌘/Ctrl + K</Button>
          </div>
        </div>
        <div className="content">
          {children ?? <Outlet />}
        </div>
      </main>
      <CommandPalette open={commandOpen} onClose={() => setCommandOpen(false)} />
    </div>
  )
}

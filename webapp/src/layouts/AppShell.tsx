import { PropsWithChildren, useEffect, useMemo, useState } from 'react'
import { Link, Outlet, useNavigate, useRouterState } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { useSession, useLogout } from '@/hooks/useAuth'
import { useAutoRefresh } from '@/hooks/useAutoRefresh'
import { CommandPalette } from '@/components/ui/CommandPalette'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { PermissionDeniedState } from '@/components/ui/StateViews'
import { labelize } from '@/lib/format'
import { canViewOps, roleWorkspaceHint } from '@/lib/access'
import { canAccess } from '@/lib/rbac'
import { getVisibleNavigation, navigationGroups } from '@/navigation/navigationRegistry'
import { routeRequirementFor } from '@/navigation/routePermissionMap'

function routePath(value: string) {
  return value.split('?')[0] || value
}

function isActiveNavPath(pathname: string, target: string) {
  const targetPath = routePath(target)
  return pathname === targetPath || (targetPath !== '/' && pathname.startsWith(`${targetPath}/`))
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

  const availableNav = useMemo(() => getVisibleNavigation(session.data), [session.data])
  const routeRequirement = useMemo(() => routeRequirementFor(location.pathname), [location.pathname])
  const directUrlDenied = Boolean(session.data && routeRequirement && !canAccess(session.data, routeRequirement))

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
    return <div className="auth-shell"><div className="auth-card" role="status" aria-live="polite">正在确认登录状态…</div></div>
  }

  if (!session.data) return null

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-kicker">NexusDesk</div>
          <h1>客服运营后台</h1>
          <div className="subtle">按客服/运营工作流组织：工作台、工单、客户/运单、报表、知识、配置和系统管理。</div>
        </div>
        <nav className="nav" data-testid="operator-primary-navigation" aria-label="客服运营后台主导航">
          {navigationGroups.map((group) => {
            const groupItems = availableNav.filter((item) => item.group === group.label)
            if (!groupItems.length) return null
            return (
              <div className="nav-group" key={group.label}>
                <div className="nav-group-label">{group.label}</div>
                {groupItems.map((item) => {
                  const active = isActiveNavPath(location.pathname, item.to)
                  const showRuntimeAttention = item.attention === 'runtime' && runtimeNeedsAttention
                  return (
                    <Link key={`${item.group}:${item.label}:${item.to}`} to={routePath(item.to) as '/'} data-active={active ? 'true' : 'false'} aria-current={active ? 'page' : undefined}>
                      <span>{item.label}</span>
                      <small>{item.description}</small>
                      <span className="badges" aria-hidden="true">
                        {item.risk === 'high' ? <Badge tone="warning">高风险</Badge> : null}
                        {showRuntimeAttention ? <Badge tone="danger">需处理 {runtimeAttentionCount}</Badge> : null}
                      </span>
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
            <div className="section-subtitle">任务完成效率优先；出现权限、运行或渠道异常时在页面内直接给出下一步。</div>
          </div>
          <div className="button-row topbar-status">
            <Badge tone={!canSeeOps ? 'default' : runtimeNeedsAttention ? 'danger' : runtime.data?.warnings?.length ? 'warning' : 'success'}>{!canSeeOps ? '客服模式' : runtimeNeedsAttention ? '运行需处理' : runtime.data?.warnings?.length ? '需要关注' : '运行正常'}</Badge>
            <Badge>{autoRefresh.enabled ? '自动刷新已开启' : '自动刷新已暂停'}</Badge>
            <Button variant="secondary" onClick={() => autoRefresh.setEnabled(!autoRefresh.enabled)}>{autoRefresh.enabled ? '暂停刷新' : '恢复刷新'}</Button>
            <Button variant="secondary" onClick={() => setCommandOpen(true)}>快捷键 ⌘/Ctrl + K</Button>
          </div>
        </div>
        <div className="content">
          {directUrlDenied ? (
            <PermissionDeniedState
              route={location.pathname}
              requirement={routeRequirement}
              currentRole={session.data.role}
            />
          ) : (children ?? <Outlet />)}
        </div>
      </main>
      <CommandPalette open={commandOpen} onClose={() => setCommandOpen(false)} />
    </div>
  )
}

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
import { canManageAIConfig, canManageChannels, canManageUsers, canViewControlPlane, canViewOps, roleWorkspaceHint } from '@/lib/access'

const nav = [
  { to: '/', label: '首页总览' },
  { to: '/workspace', label: '工单处理' },
  { to: '/webchat', label: '网站聊天' },
  { to: '/bulletins', label: '通知公告', permission: 'bulletins' },
  { to: '/ai-control', label: 'AI规则', permission: 'ai' },
  { to: '/control-plane', label: '控制面', permission: 'control-plane' },
  { to: '/accounts', label: '发送线路', permission: 'channels' },
  { to: '/users', label: '账号管理', permission: 'users' },
  { to: '/runtime', label: '运营保障', permission: 'ops' },
]

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
    if (item.permission === 'ops') return canViewOps(session.data)
    if (item.permission === 'channels') return canManageChannels(session.data)
    if (item.permission === 'ai') return canManageAIConfig(session.data)
    if (item.permission === 'control-plane') return canViewControlPlane(session.data)
    if (item.permission === 'users') return canManageUsers(session.data)
    if (item.permission === 'bulletins') return true
    return true
  }), [session.data])

  if (session.isLoading || session.isFetching) {
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
        <nav className="nav">
          {availableNav.map((item) => (
            <Link key={item.to} to={item.to} data-active={location.pathname === item.to || (item.to !== '/' && location.pathname.startsWith(item.to)) ? 'true' : 'false'}>
              <span>{item.label}</span>
            </Link>
          ))}
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
            <div className="section-subtitle">先看客户诉求，再看处理建议，再看运营保障；客服无需理解后台技术名词。</div>
          </div>
          <div className="button-row topbar-status">
            <Badge tone={!canSeeOps ? 'default' : runtime.data?.warnings?.length ? 'warning' : 'success'}>{!canSeeOps ? '客服模式' : runtime.data?.warnings?.length ? '需要关注' : '运行正常'}</Badge>
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

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

type NavItem = { to: string; label: string; hint: string; access?: AccessRequirement; attention?: 'runtime' }
const nav: NavItem[] = [
  { to: '/', label: '今日工作台', hint: '今日待办、SLA 与公告' },
  { to: '/webchat', label: 'WebChat 收件箱', hint: '聊天接管与客户回复' },
  { to: '/webcall', label: 'WebCall 工作台', hint: '来电队列与通话处理', access: routeAccess['/webcall'] },
  { to: '/email', label: 'Email 工作台', hint: '邮件队列与回复草稿', access: routeAccess['/email'] },
  { to: '/workspace', label: '工单 / 客户 / 运单查询', hint: '处理工单、查单、客户上下文与闭环' },
  { to: '/control-tower', label: '运营报表', hint: '主管队列、SLA、治理动作', access: routeAccess['/control-tower'] },
  { to: '/qa-training', label: 'QA / Training', hint: '质检样本、培训与知识缺口', access: routeAccess['/qa-training'] },
  { to: '/bulletins', label: '公告口径', hint: '统一客服话术', access: routeAccess['/bulletins'] },
  { to: '/knowledge-studio', label: '知识库', hint: '知识发布、检索与冲突', access: routeAccess['/knowledge-studio'] },
  { to: '/ai-control', label: 'AI 规则', hint: '助手口径治理', access: routeAccess['/ai-control'] },
  { to: '/persona-builder', label: 'AI Persona', hint: '人格、匹配与发布证据', access: routeAccess['/persona-builder'] },
  { to: '/accounts', label: '发送线路', hint: '账号与兜底线路', access: routeAccess['/accounts'] },
  { to: '/outbound-email', label: 'Email 账号配置', hint: 'SMTP 配置与测试发送', access: routeAccess['/outbound-email'] },
  { to: '/provider-credentials', label: 'Provider 授权', hint: '云端授权管理', access: routeAccess['/provider-credentials'] },
  { to: '/runtime', label: '运行恢复', hint: 'dead/requeue 自助处理', access: routeAccess['/runtime'], attention: 'runtime' },
  { to: '/users', label: '账号权限', hint: '人员与权限', access: routeAccess['/users'] },
  { to: '/security', label: '权限与审计', hint: '只读矩阵与审计', access: routeAccess['/security'] },
  { to: '/control-plane', label: '控制面', hint: '高级治理入口', access: routeAccess['/control-plane'] },
  { to: '/webcall-ai-demo', label: 'WebCall AI Demo', hint: '内部语音 AI 沙盒', access: routeAccess['/webcall-ai-demo'] },
]
const navGroups = [
  { label: '工作台', items: ['/', '/webchat', '/webcall', '/email'] },
  { label: '工单与查询', items: ['/workspace'] },
  { label: '运营与质量', items: ['/control-tower', '/qa-training'] },
  { label: '配置管理', items: ['/bulletins', '/knowledge-studio', '/ai-control', '/persona-builder', '/accounts', '/outbound-email', '/provider-credentials'] },
  { label: '系统管理', items: ['/runtime', '/users', '/security', '/control-plane', '/webcall-ai-demo'] },
]
const legacyNavigationAliases = ['今日总览', '日常处理', '渠道与授权', '治理与运维', '处理工单', 'WebChat 收件箱', 'WebCall 工作台', '客户 / 运单查询']
const legacyNavigationRouteAliases = { '渠道与授权': '/outbound-email' }
void legacyNavigationAliases
void legacyNavigationRouteAliases
function isActiveNavPath(pathname: string, target: string) { return pathname === target || (target !== '/' && pathname.startsWith(`${target}/`)) }
function routeRequirementForPath(pathname: string): AccessRequirement | undefined { return Object.entries(routeAccess).sort((a, b) => b[0].length - a[0].length).find(([route]) => pathname === route || pathname.startsWith(`${route}/`))?.[1] }
export function AppShell({ children }: PropsWithChildren) {
  const { location } = useRouterState(); const navigate = useNavigate(); const session = useSession(); const logout = useLogout(); const [commandOpen, setCommandOpen] = useState(false); const autoRefresh = useAutoRefresh(true); const canSeeOps = canViewOps(session.data)
  const runtime = useQuery({ queryKey: ['runtimeHealth-shell'], queryFn: api.runtimeHealth, refetchInterval: autoRefresh.enabled ? 15000 : false, enabled: !!session.data && canSeeOps })
  const queue = useQuery({ queryKey: ['queueSummary-shell'], queryFn: api.queueSummary, refetchInterval: autoRefresh.enabled ? 15000 : false, enabled: !!session.data && canSeeOps })
  useEffect(() => { const onKey = (event: KeyboardEvent) => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); setCommandOpen((s) => !s) } }; window.addEventListener('keydown', onKey); return () => window.removeEventListener('keydown', onKey) }, [])
  useEffect(() => { if (location.pathname !== '/login' && !session.isLoading && !session.isFetching && !session.data) navigate({ to: '/login', replace: true }) }, [location.pathname, navigate, session.data, session.isFetching, session.isLoading])
  const userLabel = useMemo(() => session.data ? `${session.data.display_name} · ${labelize(session.data.role)}` : '未登录', [session.data])
  const availableNav = useMemo(() => nav.filter((item) => item.access ? canAccess(session.data, item.access) : true), [session.data])
  const routeRequirement = useMemo(() => routeRequirementForPath(location.pathname), [location.pathname])
  const routeDenied = Boolean(session.data && routeRequirement && !canAccess(session.data, routeRequirement))
  const runtimeNeedsAttention = Boolean(canSeeOps && ((runtime.data?.warnings?.length ?? 0) > 0 || (queue.data?.dead_jobs ?? 0) > 0 || (queue.data?.dead_outbound ?? 0) > 0 || (runtime.data?.dead_sync_jobs ?? 0) > 0 || (runtime.data?.dead_attachment_jobs ?? 0) > 0))
  const runtimeAttentionCount = (queue.data?.dead_jobs ?? 0) + (queue.data?.dead_outbound ?? 0) + (runtime.data?.dead_sync_jobs ?? 0) + (runtime.data?.dead_attachment_jobs ?? 0)
  if (!session.data && (session.isLoading || session.isFetching)) return <div className="auth-shell"><div className="auth-card" role="status" aria-live="polite">正在确认登录状态…</div></div>
  if (!session.data) return null
  return <div className="app-shell"><aside className="sidebar"><div className="brand"><div className="brand-kicker">客服协同中心</div><h1>客服工作台</h1><div className="subtle">按真实客服工作流组织：先处理渠道，再跟进工单，再查询客户与运单。</div></div><nav className="nav" data-testid="operator-primary-navigation" aria-label="客服运营后台主导航">{navGroups.map((group) => { const groupItems = group.items.map((to) => availableNav.find((item) => item.to === to)).filter(Boolean) as NavItem[]; if (!groupItems.length) return null; return <div className="nav-group" key={group.label}><div className="nav-group-label">{group.label}</div>{groupItems.map((item) => { const active = isActiveNavPath(location.pathname, item.to); const showRuntimeAttention = item.attention === 'runtime' && runtimeNeedsAttention; return <Link key={item.to} to={item.to} data-active={active ? 'true' : 'false'} aria-current={active ? 'page' : undefined}><span>{item.label}</span><small>{item.hint}</small>{showRuntimeAttention ? <Badge tone="danger">需处理 {runtimeAttentionCount}</Badge> : null}</Link> })}</div> })}</nav><div className="card soft sidebar-card"><div className="section-title">当前账号</div><div className="section-subtitle">{userLabel}</div><div className="sidebar-helper">{roleWorkspaceHint(session.data)}</div><div className="button-row" style={{ marginTop: 12 }}><Button variant="secondary" onClick={() => setCommandOpen(true)}>快捷操作</Button><Button variant="ghost" onClick={() => { logout(); navigate({ to: '/login', replace: true }) }}>退出登录</Button></div></div></aside><main role="main"><div className="topbar"><div><div className="section-title">客服运营台</div><div className="section-subtitle">先处理 WebChat / WebCall / Email，再跟进工单；客户或运单查询请走工单与查询入口。</div></div><div className="button-row topbar-status"><Badge tone={!canSeeOps ? 'default' : runtimeNeedsAttention ? 'danger' : runtime.data?.warnings?.length ? 'warning' : 'success'}>{!canSeeOps ? '客服模式' : runtimeNeedsAttention ? '运行需处理' : runtime.data?.warnings?.length ? '需要关注' : '运行正常'}</Badge><Badge>{autoRefresh.enabled ? '自动刷新已开启' : '自动刷新已暂停'}</Badge><Button variant="secondary" onClick={() => autoRefresh.setEnabled(!autoRefresh.enabled)}>{autoRefresh.enabled ? '暂停刷新' : '恢复刷新'}</Button><Button variant="secondary" onClick={() => setCommandOpen(true)}>快捷键 ⌘/Ctrl + K</Button></div></div><div className="content">{routeDenied ? <NoAccessCard title="当前账号无权访问此页面" description="该入口需要额外 capability；左侧菜单已自动隐藏无权限入口，直接 URL 访问会在这里被拦截。" action="请联系主管或管理员开通对应权限，或返回今日工作台继续处理可见任务。" /> : (children ?? <Outlet />)}</div></main><CommandPalette open={commandOpen} onClose={() => setCommandOpen(false)} /></div>
}

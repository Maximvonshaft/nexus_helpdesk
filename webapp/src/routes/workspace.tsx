import { lazy, Suspense, useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { createRoute, redirect, useNavigate } from '@tanstack/react-router'
import { Route as RootRoute } from './root'
import { AppShell } from '@/app/AppShell'
import '@/app/app-shell.css'
import { Button } from '@/components/ui/Button'
import { ErrorSummary } from '@/components/ui/ErrorSummary'
import { useLogout, useSession } from '@/hooks/useAuth'
import { getSupportToken } from '@/lib/supportApi'
import { loadWorkspaceScope, operatorWorkspaceApi, saveWorkspaceScope } from '@/lib/operatorWorkspaceApi'
import {
  workspaceScopeFromAuthorized,
  workspaceScopeKey,
} from '@/lib/operatorWorkspaceTypes'
import type { AuthorizedWorkspaceScope } from '@/lib/operatorWorkspaceTypes'

const LazyOperatorWorkspacePage = lazy(() => import('@/features/operator-workspace/lazy'))

function WorkspaceLoading() {
  return (
    <main className="operator-workspace" aria-busy="true">
      <section className="operator-session-state" role="status" aria-live="polite">
        <strong>正在加载操作员工作台…</strong>
        <p>正在载入统一队列、案例状态和受控动作界面。</p>
      </section>
    </main>
  )
}

function LegacyWorkspaceFallback() {
  return (
    <Suspense fallback={<WorkspaceLoading />}>
      <LazyOperatorWorkspacePage />
    </Suspense>
  )
}

function authorizedScopeKey(scope: AuthorizedWorkspaceScope) {
  return workspaceScopeKey(workspaceScopeFromAuthorized(scope))
}

function AuthorizedWorkspaceRoutePage() {
  const navigate = useNavigate()
  const logout = useLogout()
  const session = useSession()
  const capabilities = useMemo(() => new Set(session.data?.capabilities ?? []), [session.data?.capabilities])
  const storedScope = useMemo(() => loadWorkspaceScope(), [])
  const [requestedScopeKey, setRequestedScopeKey] = useState<string | null>(null)
  const [appliedScopeKey, setAppliedScopeKey] = useState<string | null>(null)

  const scopes = useQuery({
    queryKey: ['operatorWorkspaceAuthorizedScopes'],
    queryFn: ({ signal }) => operatorWorkspaceApi.currentScopes({ signal }),
    enabled: Boolean(session.data),
    retry: false,
    staleTime: 30_000,
  })

  const authorizedScopes = scopes.data?.items ?? []
  const storedKey = workspaceScopeKey(storedScope)
  const selectedScope = authorizedScopes.find((scope) => authorizedScopeKey(scope) === requestedScopeKey)
    ?? authorizedScopes.find((scope) => authorizedScopeKey(scope) === storedKey)
    ?? authorizedScopes[0]
    ?? null
  const selectedKey = selectedScope ? authorizedScopeKey(selectedScope) : null

  useEffect(() => {
    if (!selectedScope || !selectedKey) {
      setAppliedScopeKey(null)
      return
    }
    saveWorkspaceScope(workspaceScopeFromAuthorized(selectedScope))
    setAppliedScopeKey(selectedKey)
  }, [selectedKey, selectedScope])

  const handleLogout = () => {
    logout()
    navigate({ to: '/login', replace: true })
  }

  if (!session.data && (session.isLoading || !session.isError)) {
    return <WorkspaceLoading />
  }

  if (session.isError) {
    return (
      <main className="nd-app-boundary-state">
        <ErrorSummary
          title="无法读取当前账号"
          errors={['登录状态可能已失效，请重新登录。']}
          action={<Button onClick={handleLogout}>返回登录</Button>}
        />
      </main>
    )
  }

  if (scopes.isLoading) {
    return <WorkspaceLoading />
  }

  // Compatibility boundary for rolling deployments. The accepted release ships
  // the endpoint and frontend together; older backend profiles retain the
  // current fail-closed ScopeEditor until the release-image gate proves parity.
  if (scopes.isError) {
    return <LegacyWorkspaceFallback />
  }

  if (!selectedScope) {
    if (scopes.data?.requires_explicit_admin_scope && session.data?.role === 'admin') {
      return <LegacyWorkspaceFallback />
    }
    return (
      <AppShell
        activeRoute="workspace"
        capabilities={capabilities}
        userLabel={session.data?.display_name || session.data?.username || '操作员'}
        onLogout={handleLogout}
      >
        <main className="nd-app-boundary-state">
          <section className="empty-state" role="status" aria-labelledby="workspace-no-scope-title">
            <h1 id="workspace-no-scope-title">当前账号没有可用工作范围</h1>
            <p>请联系管理员为账号分配国家和渠道。系统不会自动猜测或扩大访问范围。</p>
          </section>
        </main>
      </AppShell>
    )
  }

  return (
    <AppShell
      activeRoute="workspace"
      capabilities={capabilities}
      userLabel={session.data?.display_name || session.data?.username || '操作员'}
      scopes={authorizedScopes}
      selectedScope={selectedScope}
      onScopeChange={(scope) => {
        setAppliedScopeKey(null)
        setRequestedScopeKey(authorizedScopeKey(scope))
      }}
      onLogout={handleLogout}
    >
      {appliedScopeKey === selectedKey ? (
        <Suspense fallback={<WorkspaceLoading />}>
          <LazyOperatorWorkspacePage key={selectedKey} />
        </Suspense>
      ) : <WorkspaceLoading />}
    </AppShell>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/workspace',
  beforeLoad: () => {
    if (!getSupportToken()) throw redirect({ to: '/login' })
  },
  component: AuthorizedWorkspaceRoutePage,
})

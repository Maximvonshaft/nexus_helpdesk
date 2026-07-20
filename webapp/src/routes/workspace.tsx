import { Alert, Box, Button, Typography } from '@mui/material'
import { lazy, Suspense, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { createRoute, redirect, useNavigate } from '@tanstack/react-router'
import { Route as RootRoute } from './root'
import { AppShell } from '@/app/AppShell'
import {
  OperatorLoadingState,
  OperatorPageBoundary,
  RouteLoadingState,
} from '@/app/OperatorPresentation'
import { usePasswordRecoveryGuard } from '@/app/usePasswordRecoveryGuard'
import { useLogout, useSession } from '@/hooks/useAuth'
import { getSupportToken } from '@/lib/supportApi'
import { operatorWorkspaceApi } from '@/lib/operatorWorkspaceApi'
import { workspaceScopeFromAuthorized, workspaceScopeKey } from '@/lib/operatorWorkspaceTypes'
import type { AuthorizedWorkspaceScope } from '@/lib/operatorWorkspaceTypes'

const LazyOperatorWorkspacePage = lazy(() => import('@/features/operator-workspace/lazy'))

function authorizedScopeKey(scope: AuthorizedWorkspaceScope) {
  return workspaceScopeKey(workspaceScopeFromAuthorized(scope))
}

function AuthorizedWorkspaceRoutePage() {
  const navigate = useNavigate()
  const logout = useLogout()
  const session = useSession()
  const capabilities = useMemo(() => new Set(session.data?.capabilities ?? []), [session.data?.capabilities])
  const passwordRecoveryRequired = usePasswordRecoveryGuard(session.data?.must_change_password, 'workspace')
  const [requestedScopeKey, setRequestedScopeKey] = useState<string | null>(null)

  const scopes = useQuery({
    queryKey: ['operatorWorkspaceAuthorizedScopes'],
    queryFn: ({ signal }) => operatorWorkspaceApi.currentScopes({ signal }),
    enabled: Boolean(session.data) && !passwordRecoveryRequired,
    retry: false,
    staleTime: 30_000,
  })

  const authorizedScopes = scopes.data?.items ?? []
  const selectedScope = authorizedScopes.find((scope) => authorizedScopeKey(scope) === requestedScopeKey)
    ?? authorizedScopes[0]
    ?? null
  const selectedKey = selectedScope ? authorizedScopeKey(selectedScope) : null

  const handleLogout = () => {
    logout()
    navigate({ to: '/login', replace: true })
  }

  if (!session.data && (session.isLoading || !session.isError)) {
    return (
      <OperatorPageBoundary busy>
        <OperatorLoadingState label="正在登录…" minHeight={0} />
      </OperatorPageBoundary>
    )
  }

  if (session.isError) {
    return (
      <OperatorPageBoundary>
        <Alert
          severity="error"
          variant="outlined"
          sx={{ maxWidth: 560, width: '100%' }}
          action={<Button color="inherit" onClick={handleLogout}>返回登录</Button>}
        >
          <Typography variant="subtitle1">无法读取账号</Typography>
          <Typography variant="body2">请重新登录。</Typography>
        </Alert>
      </OperatorPageBoundary>
    )
  }

  if (passwordRecoveryRequired) {
    return (
      <OperatorPageBoundary busy>
        <OperatorLoadingState label="正在进入凭据恢复…" minHeight={0} />
      </OperatorPageBoundary>
    )
  }

  if (scopes.isLoading) {
    return (
      <OperatorPageBoundary busy>
        <OperatorLoadingState label="正在读取工作范围…" minHeight={0} />
      </OperatorPageBoundary>
    )
  }

  if (scopes.isError) {
    return (
      <AppShell
        activeRoute="workspace"
        capabilities={capabilities}
        userLabel={session.data?.display_name || session.data?.username || '操作员'}
        onLogout={handleLogout}
      >
        <Box component="main" sx={{ p: { xs: 2, md: 4 } }}>
          <Alert
            severity="error"
            variant="outlined"
            action={<Button color="inherit" onClick={() => scopes.refetch()}>重新加载</Button>}
          >
            <Typography variant="subtitle1">无法读取工作范围</Typography>
            <Typography variant="body2">请重新加载。</Typography>
          </Alert>
        </Box>
      </AppShell>
    )
  }

  if (!selectedScope || !selectedKey) {
    return (
      <AppShell
        activeRoute="workspace"
        capabilities={capabilities}
        userLabel={session.data?.display_name || session.data?.username || '操作员'}
        onLogout={handleLogout}
      >
        <Box component="main" sx={{ p: { xs: 2, md: 4 } }}>
          <Alert severity="warning" variant="outlined" aria-labelledby="workspace-no-scope-title">
            <Typography id="workspace-no-scope-title" component="h1" variant="h3">
              未分配工作范围
            </Typography>
            <Typography variant="body2" sx={{ mt: 0.5 }}>
              请联系管理员。
            </Typography>
          </Alert>
        </Box>
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
      onScopeChange={(scope) => setRequestedScopeKey(authorizedScopeKey(scope))}
      onLogout={handleLogout}
    >
      <Suspense fallback={<RouteLoadingState label="正在加载案例处理…" />}>
        <LazyOperatorWorkspacePage
          key={selectedKey}
          scope={workspaceScopeFromAuthorized(selectedScope)}
        />
      </Suspense>
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

import { Alert, Box, Button, CircularProgress, Stack, Typography } from '@mui/material'
import { lazy, Suspense, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { createRoute, redirect, useNavigate } from '@tanstack/react-router'
import { Route as RootRoute } from './root'
import { AppShell } from '@/app/AppShell'
import { useLogout, useSession } from '@/hooks/useAuth'
import { getSupportToken } from '@/lib/supportApi'
import { operatorWorkspaceApi } from '@/lib/operatorWorkspaceApi'
import { workspaceScopeFromAuthorized, workspaceScopeKey } from '@/lib/operatorWorkspaceTypes'
import type { AuthorizedWorkspaceScope } from '@/lib/operatorWorkspaceTypes'

const LazyOperatorWorkspacePage = lazy(() => import('@/features/operator-workspace/lazy'))

function WorkspaceLoading() {
  return (
    <Box sx={{ alignItems: 'center', display: 'flex', justifyContent: 'center', minHeight: '52vh', p: 3 }} aria-busy="true">
      <Stack role="status" spacing={2} aria-live="polite" sx={{
        alignItems: "center"
      }}>
        <CircularProgress size={34} />
        <Typography variant="subtitle1">正在加载…</Typography>
      </Stack>
    </Box>
  );
}

function authorizedScopeKey(scope: AuthorizedWorkspaceScope) {
  return workspaceScopeKey(workspaceScopeFromAuthorized(scope))
}

function AuthorizedWorkspaceRoutePage() {
  const navigate = useNavigate()
  const logout = useLogout()
  const session = useSession()
  const capabilities = useMemo(() => new Set(session.data?.capabilities ?? []), [session.data?.capabilities])
  const [requestedScopeKey, setRequestedScopeKey] = useState<string | null>(null)

  const scopes = useQuery({
    queryKey: ['operatorWorkspaceAuthorizedScopes'],
    queryFn: ({ signal }) => operatorWorkspaceApi.currentScopes({ signal }),
    enabled: Boolean(session.data),
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

  if (!session.data && (session.isLoading || !session.isError)) return <WorkspaceLoading />

  if (session.isError) {
    return (
      <Box component="main" sx={{ alignItems: 'center', display: 'flex', justifyContent: 'center', minHeight: '100dvh', p: 3 }}>
        <Alert
          severity="error"
          variant="outlined"
          sx={{ maxWidth: 560, width: '100%' }}
          action={<Button color="inherit" onClick={handleLogout}>返回登录</Button>}
        >
          <Typography variant="subtitle1">无法读取账号</Typography>
          <Typography variant="body2">请重新登录。</Typography>
        </Alert>
      </Box>
    )
  }

  if (scopes.isLoading) return <WorkspaceLoading />

  if (scopes.isError) {
    return (
      <AppShell
        activeRoute="workspace"
        capabilities={capabilities}
        userLabel={session.data?.display_name || session.data?.username || '操作员'}
        onLogout={handleLogout}
      >
        <Box sx={{ p: { xs: 2, md: 4 } }}>
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
        <Box sx={{ p: { xs: 2, md: 4 } }}>
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
      <Suspense fallback={<WorkspaceLoading />}>
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

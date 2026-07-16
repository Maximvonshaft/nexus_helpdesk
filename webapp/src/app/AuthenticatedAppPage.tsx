import { Alert, Box, Button, CircularProgress, Stack, Typography } from '@mui/material'
import type { ReactNode } from 'react'
import { useMemo } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { useLogout, useSession } from '@/hooks/useAuth'
import { AppShell } from './AppShell'
import type { AppRouteKey } from './navigation'

function FullPageBoundary({ children, busy = false }: { children: ReactNode; busy?: boolean }) {
  return (
    <Box
      component="main"
      aria-busy={busy || undefined}
      sx={{ alignItems: 'center', display: 'flex', justifyContent: 'center', minHeight: '100dvh', p: 3 }}
    >
      {children}
    </Box>
  )
}

export function AuthenticatedAppPage({
  activeRoute,
  requiredAny,
  children,
}: {
  activeRoute: AppRouteKey
  requiredAny: string[]
  children: ReactNode
}) {
  const navigate = useNavigate()
  const logout = useLogout()
  const session = useSession()
  const capabilities = useMemo(() => new Set(session.data?.capabilities ?? []), [session.data?.capabilities])

  const handleLogout = () => {
    logout()
    navigate({ to: '/login', replace: true })
  }

  if (session.isLoading || !session.data) {
    if (session.isError) {
      return (
        <FullPageBoundary>
          <Alert
            severity="error"
            variant="outlined"
            sx={{ maxWidth: 560, width: '100%' }}
            action={<Button color="inherit" onClick={handleLogout}>返回登录</Button>}
          >
            <Typography variant="subtitle1">无法读取当前账号</Typography>
            <Typography variant="body2">登录状态可能已失效，请重新登录。</Typography>
          </Alert>
        </FullPageBoundary>
      )
    }
    return (
      <FullPageBoundary busy>
        <Stack role="status" alignItems="center" spacing={2} aria-live="polite">
          <CircularProgress size={32} />
          <Typography variant="subtitle1">正在验证账号和权限…</Typography>
        </Stack>
      </FullPageBoundary>
    )
  }

  const allowed = requiredAny.some((capability) => capabilities.has(capability))

  return (
    <AppShell
      activeRoute={activeRoute}
      capabilities={capabilities}
      userLabel={session.data.display_name || session.data.username || '操作员'}
      onLogout={handleLogout}
    >
      {allowed ? children : (
        <Box sx={{ p: { xs: 2, md: 4 } }}>
          <Alert severity="warning" variant="outlined" aria-labelledby={`${activeRoute}-forbidden-title`}>
            <Typography id={`${activeRoute}-forbidden-title`} component="h1" variant="h3">
              当前账号无权访问此页面
            </Typography>
            <Typography variant="body2" sx={{ mt: 0.5 }}>
              页面权限由账号角色和服务端授权决定。请联系管理员核对访问范围。
            </Typography>
          </Alert>
        </Box>
      )}
    </AppShell>
  )
}

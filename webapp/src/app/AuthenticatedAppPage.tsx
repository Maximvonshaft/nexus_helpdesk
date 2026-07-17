import { Alert, Box, Button, Typography } from '@mui/material'
import type { ReactNode } from 'react'
import { useMemo } from 'react'
import { useNavigate } from '@tanstack/react-router'
import {
  OperatorLoadingState,
  OperatorPageBoundary,
} from '@/app/OperatorPresentation'
import { useLogout, useSession } from '@/hooks/useAuth'
import { AppShell } from './AppShell'
import type { AppRouteKey } from './navigation'

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
    return (
      <OperatorPageBoundary busy>
        <OperatorLoadingState label="正在登录…" minHeight={0} />
      </OperatorPageBoundary>
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
        <Box component="main" sx={{ p: { xs: 2, md: 4 } }}>
          <Alert severity="warning" variant="outlined" aria-labelledby={`${activeRoute}-forbidden-title`}>
            <Typography id={`${activeRoute}-forbidden-title`} component="h1" variant="h3">
              无权访问此页面
            </Typography>
            <Typography variant="body2" sx={{ mt: 0.5 }}>
              请联系管理员开通权限。
            </Typography>
          </Alert>
        </Box>
      )}
    </AppShell>
  )
}

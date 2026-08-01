import { Alert, Box, Button, Typography } from '@mui/material'
import { useQueryClient } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useEffect, useMemo, useRef } from 'react'
import { useNavigate } from '@tanstack/react-router'
import {
  OperatorLoadingState,
  OperatorPageBoundary,
} from '@/app/OperatorPresentation'
import { useLogout, useSession } from '@/hooks/useAuth'
import { getUiLocale, synchronizeAuthenticatedUiLocale } from '@/i18n/runtime'
import { uiPreferenceApi } from '@/lib/uiPreferenceApi'
import type { AuthUser } from '@/lib/types'
import { AppShell } from './AppShell'
import type { AppRouteKey } from './navigation'
import { usePasswordRecoveryGuard } from './usePasswordRecoveryGuard'

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
  const queryClient = useQueryClient()
  const logout = useLogout()
  const session = useSession()
  const localeAdoptionStarted = useRef(false)
  const capabilities = useMemo(() => new Set(session.data?.capabilities ?? []), [session.data?.capabilities])
  const passwordRecoveryRequired = usePasswordRecoveryGuard(session.data?.must_change_password, activeRoute)

  useEffect(() => {
    const currentUser = session.data
    if (!currentUser?.ui_locale) return

    if (currentUser.ui_locale_configured === false) {
      if (localeAdoptionStarted.current) return
      localeAdoptionStarted.current = true
      const selectedLocale = getUiLocale()
      void uiPreferenceApi.updateLocale(selectedLocale)
        .then((response) => {
          queryClient.setQueryData<AuthUser>(['session'], (cached) => cached
            ? {
                ...cached,
                ui_locale: response.ui_locale,
                ui_locale_configured: true,
              }
            : cached)
        })
        .catch(() => {
          // Authentication remains valid. The account stays on its safe server
          // default and adoption is retried on a later authenticated navigation.
          localeAdoptionStarted.current = false
        })
      return
    }

    if (synchronizeAuthenticatedUiLocale(currentUser.ui_locale)) {
      window.location.reload()
    }
  }, [queryClient, session.data])

  const handleLogout = () => {
    logout()
    navigate({ to: '/login', replace: true })
  }

  if (session.isLoading || !session.data || passwordRecoveryRequired) {
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
        <OperatorLoadingState label={passwordRecoveryRequired ? '正在进入凭据恢复…' : '正在登录…'} minHeight={0} />
      </OperatorPageBoundary>
    )
  }

  const allowed = requiredAny.length === 0 || requiredAny.some((capability) => capabilities.has(capability))

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

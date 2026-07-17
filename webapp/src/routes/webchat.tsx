import { Box, CircularProgress, Stack, Typography } from '@mui/material'
import { useEffect } from 'react'
import { createRoute, redirect, useNavigate } from '@tanstack/react-router'
import { Route as RootRoute } from './root'
import { getSupportToken } from '@/lib/supportApi'

function replaceWithWorkspace(sessionKey?: string | null) {
  const destination = sessionKey ? `/workspace?session=${encodeURIComponent(sessionKey)}` : '/workspace'
  window.location.replace(destination)
}

function WebchatCompatibilityRedirect() {
  const navigate = useNavigate()

  useEffect(() => {
    let active = true
    const params = new URLSearchParams(window.location.search)
    const tab = params.get('tab')
    if (tab === 'knowledge') {
      navigate({ to: '/knowledge', replace: true })
      return () => { active = false }
    }
    if (tab === 'channels') {
      navigate({ to: '/channels', replace: true })
      return () => { active = false }
    }
    if (tab === 'runtime') {
      navigate({ to: '/runtime', replace: true })
      return () => { active = false }
    }

    const legacySession = params.get('session')
    if (!legacySession) {
      navigate({ to: '/workspace', replace: true })
      return () => { active = false }
    }

    if (active) replaceWithWorkspace(legacySession)

    return () => { active = false }
  }, [navigate])

  return (
    <Box component="main" aria-busy="true" sx={{ alignItems: 'center', display: 'flex', justifyContent: 'center', minHeight: '100dvh', p: 3 }}>
      <Stack role="status" aria-live="polite" spacing={1.5} sx={{
        alignItems: "center"
      }}>
        <CircularProgress size={30} />
        <Typography variant="subtitle1">正在跳转…</Typography>
      </Stack>
    </Box>
  );
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/webchat',
  beforeLoad: () => {
    if (!getSupportToken()) throw redirect({ to: '/login' })
  },
  component: WebchatCompatibilityRedirect,
})

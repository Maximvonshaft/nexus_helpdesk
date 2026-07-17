import { useEffect } from 'react'
import { createRoute, redirect, useNavigate } from '@tanstack/react-router'
import { Route as RootRoute } from './root'
import {
  OperatorLoadingState,
  OperatorPageBoundary,
} from '@/app/OperatorPresentation'
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
    <OperatorPageBoundary busy>
      <OperatorLoadingState label="正在跳转…" minHeight={0} />
    </OperatorPageBoundary>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/webchat',
  beforeLoad: () => {
    if (!getSupportToken()) throw redirect({ to: '/login' })
  },
  component: WebchatCompatibilityRedirect,
})

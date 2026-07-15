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

    // The canonical workspace resolves the session after its tenant, country,
    // and channel scope is authoritative.
    if (active) replaceWithWorkspace(legacySession)

    return () => { active = false }
  }, [navigate])

  return (
    <main className="content" aria-busy="true">
      <section className="empty-state" role="status" aria-live="polite">
        <strong>正在进入新的工作页面…</strong>
        <p>旧客服后台入口已合并到统一操作员后台。</p>
      </section>
    </main>
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

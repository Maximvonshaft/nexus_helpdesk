import { lazy, Suspense } from 'react'
import { createRoute, redirect } from '@tanstack/react-router'
import { Route as RootRoute } from './root'
import { AuthenticatedAppPage } from '@/app/AuthenticatedAppPage'
import { getSupportToken } from '@/lib/supportApi'

const LazyKnowledgePage = lazy(() => import('@/features/knowledge/lazy'))

function KnowledgeRoutePage() {
  return (
    <AuthenticatedAppPage activeRoute="knowledge" requiredAny={['ai_config.read', 'ai_config.manage']}>
      <Suspense fallback={<main className="nd-app-boundary-state" aria-busy="true"><section className="empty-state" role="status"><strong>正在加载知识维护…</strong></section></main>}>
        <LazyKnowledgePage />
      </Suspense>
    </AuthenticatedAppPage>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/knowledge',
  beforeLoad: () => {
    if (!getSupportToken()) throw redirect({ to: '/login' })
  },
  component: KnowledgeRoutePage,
})

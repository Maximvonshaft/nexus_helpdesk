import { lazy, Suspense } from 'react'
import { createRoute, redirect } from '@tanstack/react-router'
import { Route as RootRoute } from './root'
import { AuthenticatedAppPage } from '@/app/AuthenticatedAppPage'
import { useSession } from '@/hooks/useAuth'
import { getSupportToken } from '@/lib/supportApi'

const LazyKnowledgePage = lazy(() => import('@/features/knowledge/lazy'))
const LazyKnowledgeReadOnlyPage = lazy(async () => {
  const module = await import('@/features/knowledge/KnowledgeReadOnlyPage')
  return { default: module.KnowledgeReadOnlyPage }
})

function KnowledgeCapabilityPage() {
  const session = useSession()
  const canManage = Boolean(session.data?.capabilities?.includes('ai_config.manage'))
  return canManage ? <LazyKnowledgePage /> : <LazyKnowledgeReadOnlyPage />
}

function KnowledgeRoutePage() {
  return (
    <AuthenticatedAppPage activeRoute="knowledge" requiredAny={['ai_config.read', 'ai_config.manage']}>
      <Suspense fallback={<main className="nd-app-boundary-state" aria-busy="true"><section className="empty-state" role="status"><strong>正在加载知识页面…</strong></section></main>}>
        <KnowledgeCapabilityPage />
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

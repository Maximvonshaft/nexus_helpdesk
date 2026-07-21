import { lazy, Suspense } from 'react'
import { createRoute, redirect } from '@tanstack/react-router'
import { Route as RootRoute } from './root'
import { AuthenticatedAppPage } from '@/app/AuthenticatedAppPage'
import { RouteLoadingState } from '@/app/OperatorPresentation'
import { useSession } from '@/hooks/useAuth'
import { getSupportToken } from '@/lib/supportApi'

const LazyKnowledgePage = lazy(() => import('@/features/knowledge/lazy'))

function KnowledgeCapabilityPage() {
  const session = useSession()
  const canManage = Boolean(session.data?.capabilities?.includes('ai_config.manage'))
  return <LazyKnowledgePage canManage={canManage} />
}

function KnowledgeRoutePage() {
  return (
    <AuthenticatedAppPage activeRoute="knowledge" requiredAny={['ai_config.read', 'ai_config.manage']}>
      <Suspense fallback={<RouteLoadingState label="正在加载知识库…" />}>
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

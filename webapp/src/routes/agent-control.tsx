import { lazy, Suspense } from 'react'
import { createRoute, redirect } from '@tanstack/react-router'
import { Route as RootRoute } from './root'
import { AuthenticatedAppPage } from '@/app/AuthenticatedAppPage'
import { RouteLoadingState } from '@/app/OperatorPresentation'
import { useSession } from '@/hooks/useAuth'
import { getSupportToken } from '@/lib/supportApi'

const LazyAgentControlPage = lazy(() => import('@/features/agent-control/lazy'))

function AgentControlCapabilityPage() {
  const session = useSession()
  const canManage = Boolean(session.data?.capabilities?.includes('ai_config.manage'))
  return <LazyAgentControlPage canManage={canManage} />
}

function AgentControlRoutePage() {
  return (
    <AuthenticatedAppPage
      activeRoute="agent-control"
      requiredAny={['ai_config.read', 'ai_config.manage', 'runtime.manage']}
    >
      <Suspense fallback={<RouteLoadingState label="正在加载 Agent 控制面…" />}>
        <AgentControlCapabilityPage />
      </Suspense>
    </AuthenticatedAppPage>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/agent-control',
  beforeLoad: () => {
    if (!getSupportToken()) throw redirect({ to: '/login' })
  },
  component: AgentControlRoutePage,
})

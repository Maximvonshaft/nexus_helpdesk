import { createRoute, redirect } from '@tanstack/react-router'
import { Route as RootRoute } from './root'
import { AuthenticatedAppPage } from '@/app/AuthenticatedAppPage'
import { KnowledgePage } from '@/features/knowledge/KnowledgePage'
import { getSupportToken } from '@/lib/supportApi'

function KnowledgeRoutePage() {
  return (
    <AuthenticatedAppPage activeRoute="knowledge" requiredAny={['ai_config.read', 'ai_config.manage']}>
      <KnowledgePage />
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

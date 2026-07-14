import { createRoute, redirect } from '@tanstack/react-router'
import { Route as RootRoute } from './root'
import { AuthenticatedAppPage } from '@/app/AuthenticatedAppPage'
import { RuntimePage } from '@/features/runtime/RuntimePage'
import { getSupportToken } from '@/lib/supportApi'

function RuntimeRoutePage() {
  return (
    <AuthenticatedAppPage activeRoute="runtime" requiredAny={['runtime.manage', 'audit.read']}>
      <RuntimePage />
    </AuthenticatedAppPage>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/runtime',
  beforeLoad: () => {
    if (!getSupportToken()) throw redirect({ to: '/login' })
  },
  component: RuntimeRoutePage,
})

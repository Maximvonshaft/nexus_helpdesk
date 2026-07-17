import { lazy, Suspense } from 'react'
import { createRoute, redirect } from '@tanstack/react-router'
import { Route as RootRoute } from './root'
import { AuthenticatedAppPage } from '@/app/AuthenticatedAppPage'
import { RouteLoadingState } from '@/app/OperatorPresentation'
import { getSupportToken } from '@/lib/supportApi'

const LazyControlTowerPage = lazy(() => import('@/features/control-tower/lazy'))

const CONTROL_TOWER_CAPABILITIES = [
  'ticket.assign',
  'bulletin.manage',
  'channel_account.manage',
  'runtime.manage',
  'ai_config.read',
  'ai_config.manage',
  'user.manage',
]

function ControlTowerRoutePage() {
  return (
    <AuthenticatedAppPage activeRoute="control-tower" requiredAny={CONTROL_TOWER_CAPABILITIES}>
      <Suspense fallback={<RouteLoadingState label="正在加载运营监控…" />}>
        <LazyControlTowerPage />
      </Suspense>
    </AuthenticatedAppPage>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/control-tower',
  beforeLoad: () => {
    if (!getSupportToken()) throw redirect({ to: '/login' })
  },
  component: ControlTowerRoutePage,
})

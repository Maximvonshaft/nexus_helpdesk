import { lazy, Suspense } from 'react'
import { createRoute, redirect } from '@tanstack/react-router'
import { Route as RootRoute } from './root'
import { AuthenticatedAppPage } from '@/app/AuthenticatedAppPage'
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
      <Suspense fallback={<main className="nd-app-boundary-state" aria-busy="true"><section className="empty-state" role="status"><strong>正在加载运营总览…</strong></section></main>}>
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

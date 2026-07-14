import { createRoute, redirect } from '@tanstack/react-router'
import { Route as RootRoute } from './root'
import { AuthenticatedAppPage } from '@/app/AuthenticatedAppPage'
import { ControlTowerPage } from '@/features/control-tower/ControlTowerPage'
import { getSupportToken } from '@/lib/supportApi'

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
      <ControlTowerPage />
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

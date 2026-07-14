import { createRoute, redirect } from '@tanstack/react-router'
import { Route as RootRoute } from './root'
import { AuthenticatedAppPage } from '@/app/AuthenticatedAppPage'
import { ChannelsPage } from '@/features/channels/ChannelsPage'
import { getSupportToken } from '@/lib/supportApi'

function ChannelsRoutePage() {
  return (
    <AuthenticatedAppPage activeRoute="channels" requiredAny={['channel_account.manage']}>
      <ChannelsPage />
    </AuthenticatedAppPage>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/channels',
  beforeLoad: () => {
    if (!getSupportToken()) throw redirect({ to: '/login' })
  },
  component: ChannelsRoutePage,
})

import { lazy, Suspense } from 'react'
import { createRoute, redirect } from '@tanstack/react-router'
import { Route as RootRoute } from './root'
import { AuthenticatedAppPage } from '@/app/AuthenticatedAppPage'
import { RouteLoadingState } from '@/app/OperatorPresentation'
import { getSupportToken } from '@/lib/supportApi'

const LazyChannelsPage = lazy(() => import('@/features/channels/lazy'))

function ChannelsRoutePage() {
  return (
    <AuthenticatedAppPage activeRoute="channels" requiredAny={['channel_account.manage']}>
      <Suspense fallback={<RouteLoadingState label="正在加载渠道管理…" />}>
        <LazyChannelsPage />
      </Suspense>
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

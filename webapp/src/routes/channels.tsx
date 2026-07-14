import { lazy, Suspense } from 'react'
import { createRoute, redirect } from '@tanstack/react-router'
import { Route as RootRoute } from './root'
import { AuthenticatedAppPage } from '@/app/AuthenticatedAppPage'
import { getSupportToken } from '@/lib/supportApi'

const LazyChannelsPage = lazy(() => import('@/features/channels/lazy'))

function ChannelsRoutePage() {
  return (
    <AuthenticatedAppPage activeRoute="channels" requiredAny={['channel_account.manage']}>
      <Suspense fallback={<main className="nd-app-boundary-state" aria-busy="true"><section className="empty-state" role="status"><strong>正在加载渠道管理…</strong></section></main>}>
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

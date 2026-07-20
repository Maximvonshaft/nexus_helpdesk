import { lazy, Suspense } from 'react'
import { createRoute, redirect } from '@tanstack/react-router'
import { Route as RootRoute } from './root'
import { AuthenticatedAppPage } from '@/app/AuthenticatedAppPage'
import { RouteLoadingState } from '@/app/OperatorPresentation'
import { getSupportToken } from '@/lib/supportApi'

const LazyAccountPage = lazy(() => import('@/features/account/lazy'))

function AccountRoutePage() {
  return (
    <AuthenticatedAppPage activeRoute="account" requiredAny={[]}>
      <Suspense fallback={<RouteLoadingState label="正在加载账户设置…" />}>
        <LazyAccountPage />
      </Suspense>
    </AuthenticatedAppPage>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/account',
  beforeLoad: () => {
    if (!getSupportToken()) throw redirect({ to: '/login' })
  },
  component: AccountRoutePage,
})

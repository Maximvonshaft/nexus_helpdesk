import { lazy, Suspense } from 'react'
import { createRoute, redirect } from '@tanstack/react-router'
import { Route as RootRoute } from './root'
import { AuthenticatedAppPage } from '@/app/AuthenticatedAppPage'
import { RouteLoadingState } from '@/app/OperatorPresentation'
import { getSupportToken } from '@/lib/supportApi'

const LazyRuntimePage = lazy(() => import('@/features/runtime/lazy'))

function RuntimeRoutePage() {
  return (
    <AuthenticatedAppPage activeRoute="runtime" requiredAny={['runtime.manage', 'audit.read']}>
      <Suspense fallback={<RouteLoadingState label="正在加载系统运行…" />}>
        <LazyRuntimePage />
      </Suspense>
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

import { lazy, Suspense } from 'react'
import { createRoute, redirect } from '@tanstack/react-router'
import { Route as RootRoute } from './root'
import { AuthenticatedAppPage } from '@/app/AuthenticatedAppPage'
import { getSupportToken } from '@/lib/supportApi'

const LazyRuntimePage = lazy(() => import('@/features/runtime/lazy'))

function RuntimeRoutePage() {
  return (
    <AuthenticatedAppPage activeRoute="runtime" requiredAny={['runtime.manage', 'audit.read']}>
      <Suspense fallback={<main className="nd-app-boundary-state" aria-busy="true"><section className="empty-state" role="status"><strong>正在加载运行状态…</strong></section></main>}>
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

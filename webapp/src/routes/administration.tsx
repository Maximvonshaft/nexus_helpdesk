import { lazy, Suspense } from 'react'
import { createRoute, redirect } from '@tanstack/react-router'
import { Route as RootRoute } from './root'
import { AuthenticatedAppPage } from '@/app/AuthenticatedAppPage'
import { RouteLoadingState } from '@/app/OperatorPresentation'
import { getSupportToken } from '@/lib/supportApi'

const LazyAdministrationPage = lazy(() => import('@/features/administration/lazy'))

const ADMINISTRATION_CAPABILITIES = ['user.manage', 'security.read', 'audit.read']

function AdministrationRoutePage() {
  return (
    <AuthenticatedAppPage activeRoute="administration" requiredAny={ADMINISTRATION_CAPABILITIES}>
      <Suspense fallback={<RouteLoadingState label="正在加载管理控制台…" />}>
        <LazyAdministrationPage />
      </Suspense>
    </AuthenticatedAppPage>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/administration',
  beforeLoad: () => {
    if (!getSupportToken()) throw redirect({ to: '/login' })
  },
  component: AdministrationRoutePage,
})

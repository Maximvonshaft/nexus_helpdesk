import { lazy, Suspense } from 'react'
import { createRoute, redirect } from '@tanstack/react-router'
import { Route as RootRoute } from './root'
import { getSupportToken } from '@/lib/supportApi'

const LazySystemPage = lazy(() => import('@/features/service-admin/SystemPage').then((module) => ({ default: module.SystemPage })))

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/system',
  beforeLoad: () => { if (!getSupportToken()) throw redirect({ to: '/login' }) },
  component: () => <Suspense fallback={<main className="service-entry-state">正在加载系统保障…</main>}><LazySystemPage /></Suspense>,
})

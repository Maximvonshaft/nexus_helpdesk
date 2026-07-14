import { lazy, Suspense } from 'react'
import { createRoute, redirect } from '@tanstack/react-router'
import { Route as RootRoute } from './root'
import { getSupportToken } from '@/lib/supportApi'

const LazyChannelsPage = lazy(() => import('@/features/service-admin/ChannelsPage').then((module) => ({ default: module.ChannelsPage })))

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/channels',
  beforeLoad: () => { if (!getSupportToken()) throw redirect({ to: '/login' }) },
  component: () => <Suspense fallback={<main className="service-entry-state">正在加载渠道状态…</main>}><LazyChannelsPage /></Suspense>,
})

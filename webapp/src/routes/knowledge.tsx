import { lazy, Suspense } from 'react'
import { createRoute, redirect } from '@tanstack/react-router'
import { Route as RootRoute } from './root'
import { getSupportToken } from '@/lib/supportApi'

const LazyKnowledgePage = lazy(() => import('@/features/service-admin/KnowledgePage').then((module) => ({ default: module.KnowledgePage })))

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/knowledge',
  beforeLoad: () => { if (!getSupportToken()) throw redirect({ to: '/login' }) },
  component: () => <Suspense fallback={<main className="service-entry-state">正在加载知识与规则…</main>}><LazyKnowledgePage /></Suspense>,
})

import { lazy, Suspense } from 'react'
import { createRoute, redirect } from '@tanstack/react-router'
import { Route as RootRoute } from './root'
import { getSupportToken } from '@/lib/supportApi'

const LazySupportConsolePage = lazy(() => import('@/features/support-console/lazy'))

function SupportConsoleLoading() {
  return (
    <main className="content" aria-busy="true">
      <section className="empty-state" role="status" aria-live="polite">
        <strong>加载运营工作台中…</strong>
        <p>正在载入已授权的工作界面。</p>
      </section>
    </main>
  )
}

function WebchatRoutePage() {
  return (
    <Suspense fallback={<SupportConsoleLoading />}>
      <LazySupportConsolePage />
    </Suspense>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/webchat',
  beforeLoad: () => { if (!getSupportToken()) throw redirect({ to: '/login' }) },
  component: WebchatRoutePage,
})

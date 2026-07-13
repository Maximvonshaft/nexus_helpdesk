import { lazy, Suspense } from 'react'
import { createRoute, redirect } from '@tanstack/react-router'
import { Route as RootRoute } from './root'
import { getSupportToken } from '@/lib/supportApi'

const LazyOperatorWorkspacePage = lazy(() => import('@/features/operator-workspace/lazy'))

function WorkspaceLoading() {
  return (
    <main className="operator-workspace" aria-busy="true">
      <section className="operator-session-state" role="status" aria-live="polite">
        <strong>正在加载操作员工作台…</strong>
        <p>正在载入统一队列、案例状态和受控动作界面。</p>
      </section>
    </main>
  )
}

function WorkspaceRoutePage() {
  return (
    <Suspense fallback={<WorkspaceLoading />}>
      <LazyOperatorWorkspacePage />
    </Suspense>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/workspace',
  beforeLoad: () => {
    if (!getSupportToken()) throw redirect({ to: '/login' })
  },
  component: WorkspaceRoutePage,
})

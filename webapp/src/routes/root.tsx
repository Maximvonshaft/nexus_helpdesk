import { createRootRoute, Link, Outlet } from '@tanstack/react-router'
import { getSupportToken } from '@/lib/supportApi'

export function NotFoundBoundary() {
  return (
    <main className="content">
      <section className="empty-state" data-testid="unknown-route-boundary">
        <strong>当前入口不存在</strong>
        <p>操作员工作已收敛到统一案例工作台；旧 WebChat 入口仅保留兼容访问。</p>
        <Link className="button primary" to={getSupportToken() ? '/workspace' : '/login'}>进入操作员工作台</Link>
      </section>
    </main>
  )
}

export const Route = createRootRoute({
  component: () => <Outlet />,
  notFoundComponent: NotFoundBoundary,
})

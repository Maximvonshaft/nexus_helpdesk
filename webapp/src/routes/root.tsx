import { createRootRoute, Link, Outlet } from '@tanstack/react-router'
import { getSupportToken } from '@/lib/supportApi'

export function NotFoundBoundary() {
  return (
    <main className="service-entry-state">
      <section className="nd-empty-state" data-testid="unknown-route-boundary">
        <strong>这个页面不存在</strong>
        <p>请返回客服工作台继续处理客户案例。</p>
        <Link className="nd-button nd-button--primary nd-button--md" to={getSupportToken() ? '/workspace' : '/login'}>返回客服工作台</Link>
      </section>
    </main>
  )
}

export const Route = createRootRoute({
  component: () => <Outlet />,
  notFoundComponent: NotFoundBoundary,
})

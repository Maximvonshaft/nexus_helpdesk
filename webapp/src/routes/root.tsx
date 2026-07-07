import { createRootRoute, Link, Outlet } from '@tanstack/react-router'
import { getSupportToken } from '@/lib/supportApi'

export function NotFoundBoundary() {
  return (
    <main className="support-console">
      <section className="support-panel support-not-found" data-testid="legacy-route-retired">
        <div className="support-eyebrow">Nexus Support</div>
        <h1>旧入口已下线</h1>
        <p>当前生产后台已收敛到客服工作台。</p>
        <Link className="button primary" to={getSupportToken() ? '/webchat' : '/login'}>进入客服工作台</Link>
      </section>
    </main>
  )
}

export const Route = createRootRoute({
  component: () => <Outlet />,
  notFoundComponent: NotFoundBoundary,
})

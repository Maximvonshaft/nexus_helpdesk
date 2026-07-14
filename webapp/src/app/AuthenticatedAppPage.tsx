import type { ReactNode } from 'react'
import { useMemo } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { Button } from '@/components/ui/Button'
import { ErrorSummary } from '@/components/ui/ErrorSummary'
import { useLogout, useSession } from '@/hooks/useAuth'
import { AppShell } from './AppShell'
import type { AppRouteKey } from './navigation'

export function AuthenticatedAppPage({
  activeRoute,
  requiredAny,
  children,
}: {
  activeRoute: AppRouteKey
  requiredAny: string[]
  children: ReactNode
}) {
  const navigate = useNavigate()
  const logout = useLogout()
  const session = useSession()
  const capabilities = useMemo(() => new Set(session.data?.capabilities ?? []), [session.data?.capabilities])

  const handleLogout = () => {
    logout()
    navigate({ to: '/login', replace: true })
  }

  if (session.isLoading || !session.data) {
    if (session.isError) {
      return (
        <main className="nd-app-boundary-state">
          <ErrorSummary
            title="无法读取当前账号"
            errors={['登录状态可能已失效，请重新登录。']}
            action={<Button onClick={handleLogout}>返回登录</Button>}
          />
        </main>
      )
    }
    return (
      <main className="nd-app-boundary-state" aria-busy="true">
        <section className="empty-state" role="status">
          <strong>正在验证账号和权限…</strong>
        </section>
      </main>
    )
  }

  const allowed = requiredAny.some((capability) => capabilities.has(capability))

  return (
    <AppShell
      activeRoute={activeRoute}
      capabilities={capabilities}
      userLabel={session.data.display_name || session.data.username || '操作员'}
      onLogout={handleLogout}
    >
      {allowed ? children : (
        <main className="nd-app-boundary-state">
          <section className="empty-state" role="status" aria-labelledby={`${activeRoute}-forbidden-title`}>
            <h1 id={`${activeRoute}-forbidden-title`}>当前账号无权访问此页面</h1>
            <p>页面权限由账号角色和服务端授权决定。请联系管理员核对访问范围。</p>
          </section>
        </main>
      )}
    </AppShell>
  )
}

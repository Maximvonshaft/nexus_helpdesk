import { useEffect, useState } from 'react'
import { createRoute, useNavigate } from '@tanstack/react-router'
import { Route as RootRoute } from './root'
import { useLogin, useSession } from '@/hooks/useAuth'
import { Button } from '@/components/ui/Button'
import { Field, Input } from '@/components/ui/Field'
import { PageHeader } from '@/components/ui/PageHeader'

function LoginPage() {
  const navigate = useNavigate()
  const session = useSession()
  const login = useLogin()
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')

  useEffect(() => { document.title = '登录 · 客服工作台' }, [])

  useEffect(() => {
    if (session.data) navigate({ to: '/', replace: true })
  }, [navigate, session.data])

  return (
    <div className="auth-shell">
      <div className="auth-card">
        <PageHeader eyebrow="登录" title="客服工作台" description="为客服与运营同事设计的统一处理界面。" />
        <div className="stack">
          <Field label="账号">
            <Input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" />
          </Field>
          <Field label="密码">
            <Input value={password} onChange={(e) => setPassword(e.target.value)} type="password" autoComplete="current-password" />
          </Field>
          {login.error ? <div className="inline-error">{String(login.error.message || login.error)}</div> : null}
          <div className="auth-helper">请使用已开通的客服账号登录。登录状态只保存在当前浏览器会话中。</div>
          <Button
            variant="primary"
            disabled={login.isPending}
            onClick={async () => {
              try {
                const res = await login.mutateAsync({ username, password })
                if (res.access_token) navigate({ to: '/', replace: true })
              } catch {
                // React Query keeps the login error in mutation state for display above.
              }
            }}
          >
            {login.isPending ? '登录中…' : '登录'}
          </Button>
        </div>
      </div>
    </div>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/login',
  component: LoginPage,
})

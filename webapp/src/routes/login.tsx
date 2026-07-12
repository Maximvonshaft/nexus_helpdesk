import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
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
  const [showPassword, setShowPassword] = useState(false)
  const errorRef = useRef<HTMLDivElement>(null)

  useEffect(() => { document.title = '登录 · Nexus OSR' }, [])

  useEffect(() => {
    if (session.data) navigate({ to: '/', replace: true })
  }, [navigate, session.data])

  useEffect(() => {
    if (login.error) errorRef.current?.focus()
  }, [login.error])

  const clearLoginError = () => {
    if (login.error) login.reset()
  }

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (login.isPending || !username.trim() || !password) return

    try {
      const response = await login.mutateAsync({ username: username.trim(), password })
      if (response.access_token) navigate({ to: '/webchat', replace: true })
    } catch {
      // React Query retains the error state; the bounded message below receives focus.
    }
  }

  return (
    <main className="auth-shell">
      <div className="auth-frame">
        <section className="auth-context" aria-labelledby="auth-context-title">
          <div>
            <div className="auth-context__brand">
              <p className="auth-context__kicker">Nexus OSR · Operations Service Runtime</p>
              <h2 id="auth-context-title">从可信事实到可验证结案</h2>
              <p className="auth-context__description">
                面向多国家物流客服与运营团队的案例解决驾驶舱。每一步都保留事实、权限和结果边界。
              </p>
            </div>

            <ol className="auth-sequence" aria-label="Nexus OSR 处理原则">
              <li>
                <span className="auth-sequence__index">01</span>
                <div>
                  <strong>事实</strong>
                  <span>先确认权威来源，再判断下一步。</span>
                </div>
              </li>
              <li>
                <span className="auth-sequence__index">02</span>
                <div>
                  <strong>受控动作</strong>
                  <span>所有操作遵守权限、策略和审计边界。</span>
                </div>
              </li>
              <li>
                <span className="auth-sequence__index">03</span>
                <div>
                  <strong>安全结案</strong>
                  <span>技术完成不等于业务结果，结案必须有证据。</span>
                </div>
              </li>
            </ol>
          </div>

          <p className="auth-context__boundary">
            当前入口仅建立操作员身份。队列、案例和业务动作仍由后端权限与运行时策略决定。
          </p>
        </section>

        <form className="auth-card" onSubmit={handleSubmit} noValidate>
          <PageHeader
            eyebrow="操作员登录"
            title="进入运营工作台"
            description="使用已开通的客服或运营账号登录。系统将根据角色和范围加载可访问的工作内容。"
            headingLevel={1}
          />

          <div className="auth-form">
            <Field label="账号" required>
              <Input
                name="username"
                value={username}
                onChange={(event) => {
                  setUsername(event.target.value)
                  clearLoginError()
                }}
                autoComplete="username"
                inputMode="text"
              />
            </Field>

            <div className="auth-password-row">
              <Field label="密码" required>
                <Input
                  id="login-password"
                  name="password"
                  value={password}
                  onChange={(event) => {
                    setPassword(event.target.value)
                    clearLoginError()
                  }}
                  type={showPassword ? 'text' : 'password'}
                  autoComplete="current-password"
                />
              </Field>
              <Button
                variant="ghost"
                size="md"
                aria-controls="login-password"
                aria-pressed={showPassword}
                onClick={() => setShowPassword((current) => !current)}
              >
                {showPassword ? '隐藏密码' : '显示密码'}
              </Button>
            </div>

            {login.error ? (
              <div ref={errorRef} className="auth-error" role="alert" tabIndex={-1}>
                无法登录。请检查账号和密码后重试。
              </div>
            ) : null}

            <div className="auth-helper">
              请使用已开通的客服账号登录。登录状态只保存在当前浏览器会话中。
            </div>

            <Button
              className="auth-submit"
              variant="primary"
              size="lg"
              type="submit"
              loading={login.isPending}
              loadingLabel="正在验证账号…"
              disabled={!username.trim() || !password}
            >
              登录运营工作台
            </Button>
          </div>
        </form>
      </div>
    </main>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/login',
  component: LoginPage,
})

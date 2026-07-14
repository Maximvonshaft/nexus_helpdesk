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
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const errorRef = useRef<HTMLDivElement>(null)

  useEffect(() => { document.title = '登录 · Nexus OSR' }, [])

  useEffect(() => {
    if (session.data && !login.isPending && !login.isSuccess) {
      navigate({ to: '/', replace: true })
    }
  }, [login.isPending, login.isSuccess, navigate, session.data])

  useEffect(() => {
    if (login.error) errorRef.current?.focus()
  }, [login.error])

  const clearLoginError = () => {
    if (login.error) login.reset()
  }

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const normalizedUsername = username.trim()
    if (login.isPending || !normalizedUsername || !password) return

    try {
      await login.mutateAsync({ username: normalizedUsername, password })
      navigate({ to: '/', replace: true })
    } catch {
      // React Query retains failure state; the bounded alert below receives focus.
    }
  }

  return (
    <main className="auth-shell">
      <div className="auth-frame">
        <section className="auth-context" aria-labelledby="auth-context-title">
          <div className="auth-context__brand">
            <p className="auth-context__product" translate="no">Nexus OSR</p>
            <h2 id="auth-context-title">客服与运营工作台</h2>
            <p className="auth-context__description">
              用于处理客户会话、客服工单和运营任务。
            </p>
          </div>

          <dl className="auth-context__facts">
            <div>
              <dt>登录后</dt>
              <dd>进入统一任务队列，查看案例信息并完成允许的处理动作。</dd>
            </div>
            <div>
              <dt>访问范围</dt>
              <dd>可见国家、渠道和操作权限由当前账号决定。</dd>
            </div>
          </dl>

          <p className="auth-context__boundary">
            无法登录或看不到应有任务时，请联系系统管理员检查账号和权限。
          </p>
        </section>

        <form className="auth-card" onSubmit={handleSubmit}>
          <PageHeader
            eyebrow="账号登录"
            title="登录客服与运营工作台"
            description="使用内部账号继续。系统会按你的权限加载可访问的工作内容。"
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
                spellCheck={false}
                required
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
                  required
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
              登录状态只保存在当前浏览器会话中。请勿在共享设备上保存密码。
            </div>

            <Button
              className="auth-submit"
              variant="primary"
              size="lg"
              type="submit"
              loading={login.isPending}
              loadingLabel="正在验证账号…"
            >
              登录
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

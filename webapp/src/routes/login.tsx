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

  useEffect(() => { document.title = '登录 · Nexus 客服中心' }, [])

  useEffect(() => {
    if (session.data && !login.isPending && !login.isSuccess) navigate({ to: '/', replace: true })
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
      // The error panel below receives focus.
    }
  }

  return (
    <main className="auth-shell">
      <div className="auth-frame">
        <section className="auth-context" aria-labelledby="auth-context-title">
          <div>
            <div className="auth-context__brand">
              <p className="auth-context__kicker" translate="no">Nexus Customer Service</p>
              <p id="auth-context-title" className="auth-context__title">把客户问题处理到结果</p>
              <p className="auth-context__description">
                面向物流客服团队的统一案例工作台。先理解客户诉求，再核实事实、执行处理、回复客户并确认结果。
              </p>
            </div>

            <ol className="auth-sequence" aria-label="客服处理原则">
              <li><span className="auth-sequence__index">01</span><div><strong>听懂客户</strong><span>先确认客户真正需要解决的问题。</span></div></li>
              <li><span className="auth-sequence__index">02</span><div><strong>核实事实</strong><span>以运单和运营记录为准，不凭猜测承诺。</span></div></li>
              <li><span className="auth-sequence__index">03</span><div><strong>跟到结果</strong><span>提交动作后继续确认运营结果和客户通知。</span></div></li>
            </ol>
          </div>

          <p className="auth-context__boundary">
            登录后，系统会根据账号权限加载客户待办和可执行动作。
          </p>
        </section>

        <form className="auth-card" onSubmit={handleSubmit}>
          <PageHeader
            eyebrow="客服登录"
            title="进入客服工作台"
            description="使用已开通的客服或主管账号登录。"
            headingLevel={1}
          />

          <div className="auth-form">
            <Field label="账号" required>
              <Input name="username" value={username} onChange={(event) => { setUsername(event.target.value); clearLoginError() }} autoComplete="username" autoCapitalize="none" spellCheck={false} required />
            </Field>

            <div className="auth-password-row">
              <Field label="密码" required>
                <Input id="login-password" name="password" value={password} onChange={(event) => { setPassword(event.target.value); clearLoginError() }} type={showPassword ? 'text' : 'password'} autoComplete="current-password" required />
              </Field>
              <Button variant="ghost" size="md" aria-controls="login-password" aria-pressed={showPassword} onClick={() => setShowPassword((current) => !current)}>
                {showPassword ? '隐藏密码' : '显示密码'}
              </Button>
            </div>

            {login.error ? <div ref={errorRef} className="auth-error" role="alert" tabIndex={-1}>无法登录。请检查账号和密码后重试。</div> : null}
            <div className="auth-helper">登录状态只保存在当前浏览器会话中。离开公共电脑前请退出登录。</div>
            <Button className="auth-submit" variant="primary" size="lg" type="submit" loading={login.isPending} loadingLabel="正在验证账号…">登录客服工作台</Button>
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

import type { ReactNode } from 'react'
import { Link, useNavigate } from '@tanstack/react-router'
import { Button } from '@/components/ui/Button'

export type ServiceNavKey = 'workspace' | 'knowledge' | 'channels' | 'system'

type SupportedRoute = '/workspace' | '/knowledge' | '/channels' | '/system'

type NavItem = {
  key: ServiceNavKey
  label: string
  description: string
  to: SupportedRoute
  capabilities: string[]
}

const navItems: NavItem[] = [
  {
    key: 'workspace',
    label: '客服工作台',
    description: '处理客户案例与待办',
    to: '/workspace',
    capabilities: ['operator_queue.read', 'ticket.read'],
  },
  {
    key: 'knowledge',
    label: '知识与规则',
    description: '维护客服可用的事实与流程',
    to: '/knowledge',
    capabilities: ['ai_config.read', 'ai_config.manage'],
  },
  {
    key: 'channels',
    label: '渠道状态',
    description: '查看客户联系渠道是否可用',
    to: '/channels',
    capabilities: ['channel_account.manage'],
  },
  {
    key: 'system',
    label: '系统保障',
    description: '查看服务是否正常运行',
    to: '/system',
    capabilities: ['runtime.manage'],
  },
]

function hasAnyCapability(capabilities: Set<string>, required: string[]) {
  return required.some((value) => capabilities.has(value))
}

export function ServiceAppShell({
  active,
  userName,
  capabilities,
  title,
  description,
  meta,
  children,
  onLogout,
  onNavigateRequest,
}: {
  active: ServiceNavKey
  userName: string
  capabilities: Set<string>
  title: string
  description: string
  meta?: ReactNode
  children: ReactNode
  onLogout: () => void
  onNavigateRequest?: (proceed: () => void) => void
}) {
  const navigate = useNavigate()
  const visibleItems = navItems.filter((item) => hasAnyCapability(capabilities, item.capabilities))

  const requestNavigation = (to: SupportedRoute) => {
    const proceed = () => navigate({ to })
    if (onNavigateRequest) onNavigateRequest(proceed)
    else proceed()
  }

  return (
    <main className="service-app">
      <header className="service-app__header">
        <div className="service-app__brand">
          <span className="service-app__brand-mark" aria-hidden="true">N</span>
          <div>
            <strong>Nexus 客服中心</strong>
            <small>客户案例处理系统</small>
          </div>
        </div>

        <nav className="service-app__nav" aria-label="主导航">
          {visibleItems.map((item) => (
            <Link
              key={item.key}
              to={item.to}
              className={item.key === active ? 'is-active' : ''}
              aria-current={item.key === active ? 'page' : undefined}
              onClick={(event) => {
                if (!onNavigateRequest || item.key === active) return
                event.preventDefault()
                requestNavigation(item.to)
              }}
            >
              <span>{item.label}</span>
              <small>{item.description}</small>
            </Link>
          ))}
        </nav>

        <div className="service-app__user">
          <span>{userName || '客服'}</span>
          <Button
            variant="ghost"
            onClick={() => {
              const proceed = () => {
                onLogout()
                navigate({ to: '/login', replace: true })
              }
              if (onNavigateRequest) onNavigateRequest(proceed)
              else proceed()
            }}
          >
            退出
          </Button>
        </div>
      </header>

      <section className="service-app__page-head">
        <div>
          <p>客服作业</p>
          <h1>{title}</h1>
          <span>{description}</span>
        </div>
        {meta ? <div className="service-app__page-meta">{meta}</div> : null}
      </section>

      <div className="service-app__content">{children}</div>
    </main>
  )
}

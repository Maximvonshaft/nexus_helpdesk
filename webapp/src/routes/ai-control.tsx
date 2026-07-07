import { createRoute } from '@tanstack/react-router'
import { Route as RootRoute } from './root'

function AIControlPage() {
  return <main>智能助手规则与知识配置</main>
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/ai-control',
  component: AIControlPage,
})

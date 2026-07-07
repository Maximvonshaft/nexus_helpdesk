import { createRoute, redirect } from '@tanstack/react-router'
import { Route as RootRoute } from './root'
import { getSupportToken } from '@/lib/supportApi'
import { SupportConsolePage } from '@/features/support-console'
import '@/features/support-console/support-console.css'

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/webchat',
  beforeLoad: () => { if (!getSupportToken()) throw redirect({ to: '/login' }) },
  component: SupportConsolePage,
})

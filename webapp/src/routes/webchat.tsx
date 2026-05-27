import { createRoute, redirect } from '@tanstack/react-router'
import { Route as RootRoute } from './root'
import { getToken } from '@/lib/api'
import { WebchatInboxV5Page } from '@/features/webchat-inbox-v5'
import '@/features/webchat-inbox-v5/webchat-inbox-v5.css'

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/webchat',
  beforeLoad: () => { if (!getToken()) throw redirect({ to: '/login' }) },
  component: WebchatInboxV5Page,
})

import { createRoute } from '@tanstack/react-router'
import { Route as RootRoute } from './root'
import { WebCallPage } from '@/features/webcall/WebCallPage'

function WebCallRoutePage() {
  const { voiceSessionId } = Route.useParams()
  return <WebCallPage voiceSessionId={voiceSessionId} />
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/webcall/$voiceSessionId',
  component: WebCallRoutePage,
})

import { createRoute } from '@tanstack/react-router'
import { Route as RootRoute } from './root'
import { WebCallOperatorContext } from '@/features/webcall/WebCallOperatorContext'
import { WebCallPage } from '@/features/webcall/WebCallPage'

function WebCallRoutePage() {
  const { voiceSessionId } = Route.useParams()
  return (
    <>
      <WebCallOperatorContext voiceSessionId={voiceSessionId} />
      <WebCallPage voiceSessionId={voiceSessionId} />
    </>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/webcall/$voiceSessionId',
  component: WebCallRoutePage,
})

import { lazy, Suspense } from 'react'
import { createRoute } from '@tanstack/react-router'
import { Route as RootRoute } from './root'
import { RouteLoadingState } from '@/app/OperatorPresentation'

const LazyWebCallRouteContent = lazy(() => import('@/features/webcall/lazy'))

function WebCallRoutePage() {
  const { voiceSessionId } = Route.useParams()
  return (
    <Suspense fallback={<RouteLoadingState label="正在加载语音通话…" />}>
      <LazyWebCallRouteContent voiceSessionId={voiceSessionId} />
    </Suspense>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/webcall/$voiceSessionId',
  component: WebCallRoutePage,
})

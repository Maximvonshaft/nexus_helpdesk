import { WebCallOperatorContext } from './WebCallOperatorContext'
import { WebCallPage } from './WebCallPage'

export default function LazyWebCallRouteContent({ voiceSessionId }: { voiceSessionId: string }) {
  return (
    <>
      <WebCallOperatorContext voiceSessionId={voiceSessionId} />
      <WebCallPage voiceSessionId={voiceSessionId} />
    </>
  )
}

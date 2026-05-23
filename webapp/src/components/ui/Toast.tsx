import { useEffect } from 'react'
import { Button } from './Button'

export function Toast({
  message,
  tone = 'default',
  durationMs = 4000,
  persist,
  action,
  onClose,
}: {
  message: string
  tone?: 'default' | 'danger' | 'success'
  durationMs?: number
  persist?: boolean
  action?: { label: string; onClick: () => void }
  onClose: () => void
}) {
  useEffect(() => {
    if (persist) return undefined
    const t = window.setTimeout(onClose, durationMs)
    return () => window.clearTimeout(t)
  }, [durationMs, message, onClose, persist])

  return (
    <div className={`toast ${tone}`} role={tone === 'danger' ? 'alert' : 'status'} aria-live={tone === 'danger' ? 'assertive' : 'polite'}>
      <span>{message}</span>
      {action ? <Button variant="ghost" onClick={action.onClick}>{action.label}</Button> : null}
      <button className="toast-close" type="button" aria-label="关闭提示" onClick={onClose}>×</button>
    </div>
  )
}

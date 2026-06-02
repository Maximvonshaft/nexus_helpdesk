import { ReactNode, useEffect, useRef } from 'react'
import { Button } from './Button'

export interface ConfirmDialogProps {
  open: boolean
  title: string
  description: string
  consequence?: string
  confirmLabel?: string
  cancelLabel?: string
  tone?: 'default' | 'danger'
  pending?: boolean
  onConfirm: () => void
  onCancel: () => void
  children?: ReactNode
}

export function ConfirmDialog({
  open,
  title,
  description,
  consequence,
  confirmLabel = '确认',
  cancelLabel = '取消',
  tone = 'default',
  pending,
  onConfirm,
  onCancel,
  children,
}: ConfirmDialogProps) {
  const cancelRef = useRef<HTMLButtonElement>(null)
  const confirmRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    const previous = document.activeElement as HTMLElement | null
    const timer = window.setTimeout(() => cancelRef.current?.focus(), 0)
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onCancel()
      }
      if (event.key === 'Tab') {
        const first = cancelRef.current
        const last = confirmRef.current
        if (!first || !last) return
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault()
          last.focus()
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault()
          first.focus()
        }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => {
      window.clearTimeout(timer)
      window.removeEventListener('keydown', onKey)
      previous?.focus?.()
    }
  }, [onCancel, open])

  if (!open) return null

  return (
    <div className="dialog-backdrop" role="presentation">
      <div className="dialog-card" role="dialog" aria-modal="true" aria-labelledby="confirm-dialog-title" aria-describedby="confirm-dialog-description">
        <h2 id="confirm-dialog-title">{title}</h2>
        <p id="confirm-dialog-description">{description}</p>
        {consequence ? <div className="dialog-consequence">{consequence}</div> : null}
        {children}
        <div className="button-row dialog-actions">
          <button ref={cancelRef} className="button" onClick={onCancel} disabled={pending}>{cancelLabel}</button>
          <Button ref={confirmRef} variant={tone === 'danger' ? 'danger' : 'primary'} onClick={onConfirm} disabled={pending}>{pending ? '处理中...' : confirmLabel}</Button>
        </div>
      </div>
    </div>
  )
}

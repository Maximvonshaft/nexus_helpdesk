import * as Dialog from '@radix-ui/react-dialog'
import type { ReactNode } from 'react'
import { Button } from './Button'

export type ConfirmDialogProps = {
  open: boolean
  title: string
  description: string
  confirmLabel: string
  cancelLabel?: string
  destructive?: boolean
  busy?: boolean
  children?: ReactNode
  onOpenChange: (open: boolean) => void
  onConfirm: () => void
}

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  cancelLabel = '取消',
  destructive = false,
  busy = false,
  children,
  onOpenChange,
  onConfirm,
}: ConfirmDialogProps) {
  return (
    <Dialog.Root open={open} onOpenChange={(nextOpen) => { if (!busy) onOpenChange(nextOpen) }}>
      <Dialog.Portal>
        <Dialog.Overlay className="nd-dialog__overlay" />
        <Dialog.Content
          className="nd-dialog__content"
          onEscapeKeyDown={(event) => { if (busy) event.preventDefault() }}
          onPointerDownOutside={(event) => { if (busy) event.preventDefault() }}
        >
          <div className="nd-dialog__header">
            <Dialog.Title className="nd-dialog__title">{title}</Dialog.Title>
            <Dialog.Description className="nd-dialog__description">{description}</Dialog.Description>
          </div>
          {children ? <div className="nd-dialog__body">{children}</div> : null}
          <div className="nd-dialog__actions">
            <Dialog.Close asChild>
              <Button variant="ghost" disabled={busy}>{cancelLabel}</Button>
            </Dialog.Close>
            <Button
              variant={destructive ? 'danger' : 'primary'}
              loading={busy}
              loadingLabel="处理中…"
              onClick={onConfirm}
            >
              {confirmLabel}
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

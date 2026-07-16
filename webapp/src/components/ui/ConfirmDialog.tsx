import {
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
} from '@mui/material'
import type { ReactNode } from 'react'

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
  const close = () => {
    if (!busy) onOpenChange(false)
  }

  return (
    <Dialog
      open={open}
      onClose={close}
      disableEscapeKeyDown={busy}
      aria-labelledby="nd-confirm-dialog-title"
      aria-describedby="nd-confirm-dialog-description"
    >
      <DialogTitle id="nd-confirm-dialog-title">{title}</DialogTitle>
      <DialogContent>
        <DialogContentText id="nd-confirm-dialog-description">{description}</DialogContentText>
        {children}
      </DialogContent>
      <DialogActions>
        <Button color="inherit" variant="text" disabled={busy} onClick={close}>
          {cancelLabel}
        </Button>
        <Button
          color={destructive ? 'error' : 'primary'}
          variant="contained"
          disabled={busy}
          startIcon={busy ? <CircularProgress color="inherit" size={16} /> : undefined}
          aria-busy={busy || undefined}
          onClick={onConfirm}
        >
          {busy ? '处理中…' : confirmLabel}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

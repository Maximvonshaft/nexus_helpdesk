import { useEffect } from 'react'

export function Toast({ message, tone = 'default', onClose }: { message: string; tone?: 'default' | 'danger' | 'success'; onClose: () => void }) {
  useEffect(() => {
    const t = window.setTimeout(onClose, 2400)
    return () => window.clearTimeout(t)
  }, [message, onClose])

  return <div className={`toast ${tone}`}>{message}</div>
}

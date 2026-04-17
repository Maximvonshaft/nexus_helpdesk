import { useEffect, useState } from 'react'

export function useAutoRefresh(defaultValue = true) {
  const [enabled, setEnabled] = useState(defaultValue)
  useEffect(() => {
    const raw = window.sessionStorage.getItem('helpdesk-auto-refresh')
    if (raw === 'false') setEnabled(false)
  }, [])
  useEffect(() => {
    window.sessionStorage.setItem('helpdesk-auto-refresh', enabled ? 'true' : 'false')
  }, [enabled])
  return { enabled, setEnabled }
}

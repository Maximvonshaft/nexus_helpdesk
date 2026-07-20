import { useNavigate } from '@tanstack/react-router'
import { useEffect } from 'react'

export function usePasswordRecoveryGuard(
  mustChangePassword: boolean | null | undefined,
  activeRoute: string,
) {
  const navigate = useNavigate()
  const recoveryRequired = Boolean(mustChangePassword) && activeRoute !== 'account'

  useEffect(() => {
    if (recoveryRequired) navigate({ to: '/account', replace: true })
  }, [navigate, recoveryRequired])

  return recoveryRequired
}

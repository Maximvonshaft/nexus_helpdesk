import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { identityApi } from '@/lib/identityApi'
import { clearSupportToken, getSupportToken, setSupportToken, supportApi } from '@/lib/supportApi'

export function useSession() {
  const token = getSupportToken()
  return useQuery({
    queryKey: ['session'],
    queryFn: async () => {
      const [user, security] = await Promise.all([
        supportApi.me(),
        identityApi.accountSecurity(),
      ])
      return {
        ...user,
        must_change_password: security.must_change_password,
        password_changed_at: security.password_changed_at,
        last_login_at: security.last_login_at,
      }
    },
    enabled: !!token,
    retry: false,
  })
}

export function useLogin() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async (input: { username: string; password: string }) => {
      const res = await supportApi.login(input.username, input.password)
      setSupportToken(res.access_token)
      return res
    },
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ['session'] })
    },
  })
}

export function useLogout() {
  const client = useQueryClient()
  return () => {
    clearSupportToken()
    client.clear()
  }
}

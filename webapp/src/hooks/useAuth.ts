import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { clearSupportToken, getSupportToken, setSupportToken, supportApi } from '@/lib/supportApi'

export function useSession() {
  const token = getSupportToken()
  return useQuery({
    queryKey: ['session'],
    queryFn: supportApi.me,
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

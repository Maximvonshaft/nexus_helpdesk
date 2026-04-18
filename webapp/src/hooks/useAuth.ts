import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, clearToken, getToken, setToken } from '@/lib/api'

export function useSession() {
  const token = getToken()
  return useQuery({
    queryKey: ['session'],
    queryFn: api.me,
    enabled: !!token,
    retry: false,
  })
}

export function useLogin() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async (input: { username: string; password: string }) => {
      const res = await api.login(input.username, input.password)
      setToken(res.access_token)
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
    clearToken()
    client.clear()
  }
}

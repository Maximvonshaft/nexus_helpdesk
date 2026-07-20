import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { LoginResult, MfaLoginChallenge } from '@/lib/types'
import { clearSupportToken, getSupportToken, setSupportToken, supportApi } from '@/lib/supportApi'

export function isMfaLoginChallenge(result: LoginResult): result is MfaLoginChallenge {
  return 'mfa_required' in result && result.mfa_required === true
}

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
      const result = await supportApi.login(input.username, input.password)
      if (!isMfaLoginChallenge(result)) setSupportToken(result.access_token)
      return result
    },
    onSuccess: async (result) => {
      if (!isMfaLoginChallenge(result)) await client.invalidateQueries({ queryKey: ['session'] })
    },
  })
}

export function useMfaLoginVerification() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async (input: { challengeToken: string; credential: string }) => {
      const result = await supportApi.verifyMfaLogin(input.challengeToken, input.credential)
      setSupportToken(result.access_token)
      return result
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

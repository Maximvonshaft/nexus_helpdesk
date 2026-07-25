import { useQuery } from '@tanstack/react-query'
import { supportApi } from '@/lib/supportApi'

export const ticketClosureReadinessQueryKey = (ticketId: number | null | undefined) => [
  'ticket-closure-readiness',
  ticketId ?? null,
] as const

export function useTicketClosureReadiness(ticketId: number | null | undefined) {
  return useQuery({
    queryKey: ticketClosureReadinessQueryKey(ticketId),
    queryFn: () => {
      if (!ticketId) throw new Error('当前任务没有工单')
      return supportApi.ticketClosureReadiness(ticketId)
    },
    enabled: Boolean(ticketId),
    staleTime: 0,
    retry: false,
  })
}

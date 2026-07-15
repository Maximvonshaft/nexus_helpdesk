export type { ChannelOnboardingTask, ChannelOnboardingTaskList } from '@/lib/types'

export interface ChannelOnboardingTaskCreate {
  provider: string
  market_id?: number | null
  target_slot?: string | null
  desired_display_name?: string | null
  desired_channel_account_binding?: string | null
  external_channel_account_id?: string | null
}

export interface ChannelOnboardingTaskComplete {
  external_channel_account_id?: string | null
  desired_channel_account_binding?: string | null
}

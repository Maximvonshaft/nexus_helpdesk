export interface ChannelOnboardingTask {
  id: number
  provider: string
  status: string
  requested_by?: number | null
  market_id?: number | null
  target_slot?: string | null
  desired_display_name?: string | null
  desired_channel_account_binding?: string | null
  external_channel_account_id?: string | null
  last_error?: string | null
  created_at: string
  updated_at: string
  started_at?: string | null
  completed_at?: string | null
}

export interface ChannelOnboardingTaskList {
  tasks: ChannelOnboardingTask[]
  total: number
}

export interface ExternalChannelUnresolvedEvent {
  id: number
  source: string
  session_key?: string | null
  event_type?: string | null
  recipient?: string | null
  source_chat_id?: string | null
  preferred_reply_contact?: string | null
  status: string
  replay_count: number
  last_error?: string | null
  created_at: string
  updated_at: string
}

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

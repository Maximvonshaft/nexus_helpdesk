import { apiClient } from '@/lib/apiClient'
import type { ControlTowerOutcomeMetricsResponse } from '@/lib/types/operations'

export const outcomeMetricsApi = {
  controlTower: () => apiClient<ControlTowerOutcomeMetricsResponse>('/api/lite/control-tower/outcomes'),
}

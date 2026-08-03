import { apiRequest } from '@/lib/apiClient'
import type { UiLocale } from '@/i18n/runtime'

export interface UiPreferenceResponse {
  ui_locale: UiLocale
}

export const uiPreferenceApi = {
  updateLocale: (uiLocale: UiLocale) => apiRequest<UiPreferenceResponse>('/api/auth/preferences', {
    method: 'PATCH',
    body: JSON.stringify({ ui_locale: uiLocale }),
  }),
}

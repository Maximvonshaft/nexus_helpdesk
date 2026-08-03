import React from 'react'
import ReactDOM from 'react-dom/client'
import { RouterProvider } from '@tanstack/react-router'
import { QueryClientProvider } from '@tanstack/react-query'
import { I18nextProvider } from 'react-i18next'
import { router } from '@/router'
import { queryClient } from '@/lib/queryClient'
import { initFrontendTelemetry } from '@/lib/frontendTelemetry'
import { initWebVitals } from '@/lib/webVitals'
import { i18n, initializeUiLocale } from '@/i18n/runtime'
import { NexusThemeProvider } from '@/theme/NexusThemeProvider'
import '@/styles.css'
import '@/a11y.css'

initializeUiLocale()
initFrontendTelemetry()
initWebVitals()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <I18nextProvider i18n={i18n}>
      <NexusThemeProvider>
        <QueryClientProvider client={queryClient}>
          <RouterProvider router={router} />
        </QueryClientProvider>
      </NexusThemeProvider>
    </I18nextProvider>
  </React.StrictMode>,
)

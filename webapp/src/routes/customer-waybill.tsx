import { createRoute, redirect } from '@tanstack/react-router'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { getToken } from '@/lib/api'
import { CustomerSearchPanel } from '@/components/customer/CustomerLookupPanels'

function CustomerWaybillRoutePage() {
  return (
    <AppShell>
      <CustomerSearchPanel />
    </AppShell>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/customer-waybill',
  beforeLoad: () => { if (!getToken()) throw redirect({ to: '/login' }) },
  component: CustomerWaybillRoutePage,
})

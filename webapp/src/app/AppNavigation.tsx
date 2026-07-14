import { APP_NAVIGATION, canSeeNavigationItem } from './navigation'
import type { AppRouteKey } from './navigation'

export function AppNavigation({
  capabilities,
  activeRoute,
}: {
  capabilities: Set<string>
  activeRoute: AppRouteKey
}) {
  const items = APP_NAVIGATION.filter((item) => canSeeNavigationItem(capabilities, item))

  return (
    <nav className="nd-app-navigation" aria-label="主导航">
      {items.map((item) => (
        <a
          key={item.key}
          href={item.currentHref}
          aria-current={item.key === activeRoute ? 'page' : undefined}
          data-canonical-route={item.canonicalRoute}
          data-route-status={item.status}
        >
          {item.label}
        </a>
      ))}
    </nav>
  )
}

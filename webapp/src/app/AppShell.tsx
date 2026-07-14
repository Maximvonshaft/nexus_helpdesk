import type { ReactNode } from 'react'
import { Button } from '@/components/ui/Button'
import type { AuthorizedWorkspaceScope } from '@/lib/operatorWorkspaceTypes'
import { AppNavigation } from './AppNavigation'
import type { AppRouteKey } from './navigation'

function channelLabel(channel: string) {
  if (channel === 'webchat') return '网页客服'
  if (channel === 'whatsapp') return 'WhatsApp'
  if (channel === 'email') return '邮件'
  if (channel === 'voice') return '语音'
  return channel
}

function scopeLabel(scope: AuthorizedWorkspaceScope, duplicatePosition?: number) {
  const base = `${scope.country_code} · ${channelLabel(scope.channel_key)}`
  return duplicatePosition ? `${base} · 范围 ${duplicatePosition}` : base
}

function sameScope(left: AuthorizedWorkspaceScope, right: AuthorizedWorkspaceScope) {
  return left.tenant_key === right.tenant_key
    && left.country_code === right.country_code
    && left.channel_key === right.channel_key
}

export function AppShell({
  activeRoute,
  capabilities,
  userLabel,
  scopes = [],
  selectedScope,
  onScopeChange,
  onLogout,
  children,
}: {
  activeRoute: AppRouteKey
  capabilities: Set<string>
  userLabel: string
  scopes?: AuthorizedWorkspaceScope[]
  selectedScope?: AuthorizedWorkspaceScope | null
  onScopeChange?: (scope: AuthorizedWorkspaceScope) => void
  onLogout: () => void
  children: ReactNode
}) {
  const selectedIndex = selectedScope ? scopes.findIndex((scope) => sameScope(scope, selectedScope)) : -1
  const labelCounts = new Map<string, number>()
  for (const scope of scopes) {
    const label = `${scope.country_code}\u0000${scope.channel_key}`
    labelCounts.set(label, (labelCounts.get(label) ?? 0) + 1)
  }

  return (
    <div className="nd-app-shell">
      <a className="nd-skip-link" href="#nd-main-content">跳到主要内容</a>
      <header className="nd-app-header">
        <div className="nd-app-brand" aria-label="Nexus OSR 客服与运营工作台">
          <span translate="no">Nexus OSR</span>
          <strong>客服与运营工作台</strong>
        </div>

        <AppNavigation capabilities={capabilities} activeRoute={activeRoute} />

        <div className="nd-app-session">
          {selectedScope && scopes.length === 1 ? (
            <span className="nd-app-scope" aria-label="当前工作范围">
              {scopeLabel(selectedScope)}
            </span>
          ) : null}

          {selectedScope && scopes.length > 1 && onScopeChange ? (
            <label className="nd-app-scope-select">
              <span>工作范围</span>
              <select
                value={selectedIndex >= 0 ? String(selectedIndex) : '0'}
                onChange={(event) => {
                  const next = scopes[Number.parseInt(event.target.value, 10)]
                  if (next) onScopeChange(next)
                }}
              >
                {scopes.map((scope, index) => {
                  const duplicateKey = `${scope.country_code}\u0000${scope.channel_key}`
                  const duplicate = (labelCounts.get(duplicateKey) ?? 0) > 1
                  return (
                    <option key={`${scope.tenant_hash}-${scope.country_code}-${scope.channel_key}`} value={String(index)}>
                      {scopeLabel(scope, duplicate ? index + 1 : undefined)}
                    </option>
                  )
                })}
              </select>
            </label>
          ) : null}

          <span className="nd-app-user">{userLabel}</span>
          <Button variant="ghost" onClick={onLogout}>退出</Button>
        </div>
      </header>

      <div id="nd-main-content" className="nd-app-content" tabIndex={-1}>
        {children}
      </div>
    </div>
  )
}

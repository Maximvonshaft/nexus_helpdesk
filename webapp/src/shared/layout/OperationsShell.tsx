import type { ReactNode } from 'react'

export interface OperationsShellProps {
  sidebar: ReactNode
  topbar: ReactNode
  children: ReactNode
  contextPanel?: ReactNode
  eventDock?: ReactNode
}

export function OperationsShell({
  sidebar,
  topbar,
  children,
  contextPanel,
  eventDock,
}: OperationsShellProps) {
  return (
    <div className="app-shell ops-shell">
      {sidebar}
      <main role="main" className="ops-main">
        {topbar}
        <div className="ops-workspace">
          <div className="content">{children}</div>
          {contextPanel ? (
            <aside className="ops-context-panel" data-testid="operations-context-panel" aria-label="运营上下文">
              {contextPanel}
            </aside>
          ) : null}
        </div>
        {eventDock ? (
          <div className="ops-event-dock" data-testid="operations-event-dock" aria-live="polite">
            {eventDock}
          </div>
        ) : null}
      </main>
    </div>
  )
}

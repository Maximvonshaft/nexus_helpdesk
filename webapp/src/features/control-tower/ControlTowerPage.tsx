import { useQuery } from '@tanstack/react-query'
import { canonicalAppHref } from '@/app/canonicalRoutes'
import { Badge } from '@/components/ui/Badge'
import { ButtonLink } from '@/components/ui/ButtonLink'
import { EmptyState } from '@/components/ui/EmptyState'
import { ErrorSummary } from '@/components/ui/ErrorSummary'
import { sanitizeDisplayText } from '@/lib/format'
import { supportApi } from '@/lib/supportApi'
import type { BadgeTone, ControlTowerAction, ControlTowerGovernanceLane } from '@/lib/types'
import '@/features/admin-routes/admin-routes.css'

function errorCopy(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback
}

function safeTone(value: string | null | undefined): BadgeTone {
  return value === 'success' || value === 'warning' || value === 'danger' || value === 'default'
    ? value
    : 'default'
}

function ActionRow({ item }: { item: ControlTowerAction }) {
  const href = canonicalAppHref(item.href)
  return (
    <article className="nd-control-action">
      <div>
        <div className="nd-control-action__title">
          <strong>{sanitizeDisplayText(item.label)}</strong>
          <Badge tone={safeTone(item.tone)}>{item.count}</Badge>
        </div>
        <p>{sanitizeDisplayText(item.next)}</p>
      </div>
      {item.enabled && href ? (
        <ButtonLink href={href}>打开处理页面</ButtonLink>
      ) : (
        <span className="nd-admin-muted">{item.enabled ? '后端未返回受支持的处理入口' : '当前账号无执行权限'}</span>
      )}
    </article>
  )
}

function GovernanceRow({ item }: { item: ControlTowerGovernanceLane }) {
  const href = canonicalAppHref(item.href)
  return (
    <tr>
      <td data-label="领域">{sanitizeDisplayText(item.area)}</td>
      <td data-label="待处理"><Badge tone={safeTone(item.risk)}>{item.value}</Badge></td>
      <td data-label="下一步">{sanitizeDisplayText(item.next)}</td>
      <td data-label="入口">
        {item.enabled && href ? <a href={href}>查看</a> : <span className="nd-admin-muted">不可用</span>}
      </td>
    </tr>
  )
}

export function ControlTowerPage() {
  const tower = useQuery({
    queryKey: ['canonicalControlTower'],
    queryFn: supportApi.controlTower,
    refetchInterval: 30_000,
    retry: false,
  })

  return (
    <main className="nd-admin-page nd-control-tower">
      <header className="nd-admin-page__header">
        <div>
          <h1>运营总览</h1>
          <p>查看未分配任务、SLA 风险、渠道异常和需要修复的工作，并进入对应的唯一处理页面。</p>
        </div>
        {tower.isFetching ? <Badge>正在刷新</Badge> : null}
      </header>

      {tower.isError ? (
        <ErrorSummary title="无法读取运营总览" errors={[errorCopy(tower.error, '请稍后重试')]} />
      ) : tower.isLoading ? (
        <EmptyState title="正在加载运营总览" description="正在汇总当前账号可见的工作和风险。" />
      ) : tower.data ? (
        <>
          <section className="nd-control-kpis" aria-label="关键运营指标">
            {tower.data.kpis.map((item) => (
              <article key={item.key}>
                <span>{sanitizeDisplayText(item.label)}</span>
                <strong>{item.value}</strong>
                <small>{sanitizeDisplayText(item.hint)}</small>
              </article>
            ))}
          </section>

          <div className="nd-admin-grid">
            <section className="nd-admin-panel" aria-labelledby="control-actions-title">
              <div className="nd-admin-panel__head">
                <h2 id="control-actions-title">需要处理</h2>
                <Badge>{tower.data.manager_actions.reduce((sum, item) => sum + item.count, 0)} 项</Badge>
              </div>
              <div className="nd-admin-panel__body nd-control-actions">
                {tower.data.manager_actions.length
                  ? tower.data.manager_actions.map((item) => <ActionRow key={item.key} item={item} />)
                  : <EmptyState title="当前没有管理待办" description="当前可见范围没有需要管理介入的工作。" />}
              </div>
            </section>

            <aside className="nd-admin-panel" aria-labelledby="team-workload-title">
              <div className="nd-admin-panel__head">
                <h2 id="team-workload-title">团队负载</h2>
              </div>
              <div className="nd-admin-panel__body nd-admin-stack">
                {tower.data.team_workload.length ? tower.data.team_workload.map((team) => (
                  <article className="nd-control-team" key={`${team.team_id || 'none'}-${team.team_name}`}>
                    <strong>{sanitizeDisplayText(team.team_name)}</strong>
                    <dl>
                      <div><dt>处理中</dt><dd>{team.active_tickets}</dd></div>
                      <div><dt>未分配</dt><dd>{team.unassigned}</dd></div>
                      <div><dt>SLA 风险</dt><dd>{team.sla_risk}</dd></div>
                      <div><dt>已超时</dt><dd>{team.overdue}</dd></div>
                    </dl>
                  </article>
                )) : <EmptyState title="暂无团队负载" description="当前账号没有可见的团队工作数据。" />}
              </div>
            </aside>
          </div>

          <section className="nd-admin-panel" aria-labelledby="governance-lanes-title">
            <div className="nd-admin-panel__head">
              <h2 id="governance-lanes-title">运行与治理风险</h2>
            </div>
            <div className="nd-admin-panel__body nd-admin-table-wrap">
              <table className="nd-admin-table">
                <caption className="sr-only">运行与治理风险列表</caption>
                <thead><tr><th scope="col">领域</th><th scope="col">待处理</th><th scope="col">下一步</th><th scope="col">入口</th></tr></thead>
                <tbody>
                  {tower.data.governance_lanes.map((item) => <GovernanceRow key={item.key} item={item} />)}
                </tbody>
              </table>
            </div>
          </section>
        </>
      ) : null}
    </main>
  )
}

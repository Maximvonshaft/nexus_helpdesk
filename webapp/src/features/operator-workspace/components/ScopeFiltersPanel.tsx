import { Button } from '@/components/ui/Button'
import { ErrorSummary } from '@/components/ui/ErrorSummary'
import { Field, Input, Select } from '@/components/ui/Field'
import type { WorkspaceFilters, WorkspaceScope } from '@/lib/operatorWorkspaceTypes'

export function ScopeFiltersPanel({
  draft,
  applied,
  filters,
  onDraftChange,
  onApply,
  onFiltersChange,
}: {
  draft: WorkspaceScope
  applied: boolean
  filters: WorkspaceFilters
  onDraftChange: (scope: WorkspaceScope) => void
  onApply: () => void
  onFiltersChange: (filters: WorkspaceFilters) => void
}) {
  const errors = [
    !draft.tenantKey.trim() ? '缺少业务组织代码，请联系主管或系统管理员。' : '',
    draft.countryCode.trim().length < 2 ? '请选择有效的服务国家代码。' : '',
    !draft.channelKey.trim() ? '请选择客户联系渠道。' : '',
  ].filter(Boolean)

  return (
    <section className="workspace-filter-card" aria-labelledby="workspace-scope-title">
      <div className="workspace-section-heading">
        <div>
          <h2 id="workspace-scope-title">我的工作范围</h2>
          <p>只显示当前国家、渠道和权限范围内的客户案例。</p>
        </div>
        <span className={applied ? 'scope-state is-applied' : 'scope-state'}>
          {applied ? '已生效' : '有修改'}
        </span>
      </div>

      <div className="workspace-scope-fields">
        <Field label="业务组织" required hint="通常由系统预先配置。">
          <Input
            name="workspace-tenant"
            value={draft.tenantKey}
            onChange={(event) => onDraftChange({ ...draft, tenantKey: event.target.value })}
            autoComplete="organization"
            placeholder="组织代码"
          />
        </Field>
        <Field label="服务国家" required>
          <Input
            name="workspace-country"
            value={draft.countryCode}
            onChange={(event) => onDraftChange({ ...draft, countryCode: event.target.value.toUpperCase() })}
            autoComplete="country"
            placeholder="CH"
          />
        </Field>
        <Field label="客户渠道" required>
          <Input
            name="workspace-channel"
            value={draft.channelKey}
            onChange={(event) => onDraftChange({ ...draft, channelKey: event.target.value.toLowerCase() })}
            autoComplete="off"
            placeholder="webchat"
          />
        </Field>
      </div>

      {errors.length ? <ErrorSummary title="工作范围尚未完成" errors={errors} /> : null}
      <Button variant="primary" disabled={Boolean(errors.length) || applied} onClick={onApply}>
        应用工作范围
      </Button>

      <div className="workspace-filter-divider" />
      <div className="workspace-section-heading compact">
        <div>
          <h2>筛选待办</h2>
          <p>优先处理超时、紧急和未分配案例。</p>
        </div>
      </div>

      <div className="workspace-filter-fields">
        <Field label="处理状态">
          <Select value={filters.state} onChange={(event) => onFiltersChange({ ...filters, state: event.target.value as WorkspaceFilters['state'] })}>
            <option value="active">需要处理</option>
            <option value="terminal">来源已结束</option>
            <option value="all">全部</option>
          </Select>
        </Field>
        <Field label="案例来源">
          <Select value={filters.sourceType} onChange={(event) => onFiltersChange({ ...filters, sourceType: event.target.value as WorkspaceFilters['sourceType'] })}>
            <option value="all">全部来源</option>
            <option value="handoff">客户请求人工</option>
            <option value="ticket">客服工单</option>
            <option value="dispatch">运营协同</option>
          </Select>
        </Field>
        <Field label="责任人">
          <Select value={filters.owner} onChange={(event) => onFiltersChange({ ...filters, owner: event.target.value as WorkspaceFilters['owner'] })}>
            <option value="any">全部责任人</option>
            <option value="mine">我负责</option>
            <option value="unassigned">未分配</option>
            <option value="team">我的团队</option>
          </Select>
        </Field>
        <Field label="优先级">
          <Select value={filters.priority} onChange={(event) => onFiltersChange({ ...filters, priority: event.target.value as WorkspaceFilters['priority'] })}>
            <option value="all">全部优先级</option>
            <option value="urgent">紧急</option>
            <option value="high">高</option>
            <option value="medium">普通</option>
            <option value="low">低</option>
          </Select>
        </Field>
        <Field label="时限风险">
          <Select value={filters.sla} onChange={(event) => onFiltersChange({ ...filters, sla: event.target.value as WorkspaceFilters['sla'] })}>
            <option value="any">全部时限</option>
            <option value="breached">已超时</option>
            <option value="at_risk">即将超时</option>
            <option value="stale">长期未更新</option>
            <option value="paused">计时暂停</option>
            <option value="healthy">正常</option>
            <option value="unavailable">无时限数据</option>
          </Select>
        </Field>
        <Field label="执行状态">
          <Select value={filters.retry} onChange={(event) => onFiltersChange({ ...filters, retry: event.target.value as WorkspaceFilters['retry'] })}>
            <option value="any">全部执行状态</option>
            <option value="pending">等待执行</option>
            <option value="processing">执行中</option>
            <option value="retry_scheduled">等待重试</option>
            <option value="exhausted">需要人工修复</option>
            <option value="settled">已稳定</option>
          </Select>
        </Field>
        <Field label="排序">
          <Select value={filters.sort} onChange={(event) => onFiltersChange({ ...filters, sort: event.target.value as WorkspaceFilters['sort'] })}>
            <option value="oldest">最早待办优先</option>
            <option value="newest">最新更新优先</option>
          </Select>
        </Field>
      </div>
    </section>
  )
}

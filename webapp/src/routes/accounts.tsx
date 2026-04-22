import { useEffect, useMemo, useState } from 'react'
import { createRoute, redirect, useNavigate } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import type { ChannelAccount, ChannelOnboardingTask } from '@/lib/types'
import { formatDateTime, healthTone, labelize, sanitizeDisplayText } from '@/lib/format'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Field, Input, Select } from '@/components/ui/Field'
import { EmptyState } from '@/components/ui/EmptyState'
import { PageHeader } from '@/components/ui/PageHeader'
import { Toast } from '@/components/ui/Toast'
import { MetricCard } from '@/components/ui/MetricCard'
import { SegmentedControl } from '@/components/ui/SegmentedControl'
import { useAutoRefresh } from '@/hooks/useAutoRefresh'
import { useSession } from '@/hooks/useAuth'
import { canManageChannels } from '@/lib/access'

const PROVIDERS = [
  { label: '全部渠道', value: 'all' },
  { label: 'WhatsApp', value: 'whatsapp' },
  { label: 'Telegram', value: 'telegram' },
  { label: 'SMS', value: 'sms' },
  { label: 'Email', value: 'email' },
  { label: 'Web Chat', value: 'web_chat' },
]

function emptyAccountForm(): Partial<ChannelAccount> {
  return {
    provider: 'whatsapp',
    account_id: '',
    display_name: '',
    market_id: undefined,
    is_active: true,
    priority: 100,
    fallback_account_id: '',
  }
}

function emptyOnboardingForm() {
  return {
    provider: 'whatsapp',
    market_id: '',
    target_slot: '',
    desired_display_name: '',
    desired_channel_account_binding: '',
  }
}

function AccountsPage() {
  const client = useQueryClient()
  const autoRefresh = useAutoRefresh(true)
  const session = useSession()
  const navigate = useNavigate()
  const permitted = canManageChannels(session.data?.role)

  const accounts = useQuery({
    queryKey: ['channel-control-accounts'],
    queryFn: api.channelControlAccounts,
    refetchInterval: autoRefresh.enabled ? 30000 : false,
    enabled: permitted,
  })
  const onboardingTasks = useQuery({
    queryKey: ['channel-onboarding-tasks'],
    queryFn: () => api.onboardingTasks(),
    refetchInterval: autoRefresh.enabled ? 30000 : false,
    enabled: permitted,
  })
  const markets = useQuery({ queryKey: ['markets-channel-control'], queryFn: api.markets, enabled: permitted })

  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [providerFilter, setProviderFilter] = useState('all')
  const [form, setForm] = useState<Partial<ChannelAccount>>(emptyAccountForm())
  const [routeExplainInput, setRouteExplainInput] = useState({ provider: 'whatsapp', market_id: '', requested_account_id: '', ticket_id: '' })
  const [onboardingForm, setOnboardingForm] = useState(emptyOnboardingForm())
  const [toast, setToast] = useState<{ message: string; tone?: 'default' | 'danger' | 'success' } | null>(null)

  useEffect(() => {
    if (session.data && !permitted) navigate({ to: '/' })
  }, [navigate, permitted, session.data])

  const filteredAccounts = useMemo(
    () => (accounts.data ?? []).filter((item) => providerFilter === 'all' || item.provider === providerFilter),
    [accounts.data, providerFilter],
  )

  const selected = useMemo(
    () => filteredAccounts.find((item) => item.id === selectedId) ?? (accounts.data ?? []).find((item) => item.id === selectedId) ?? null,
    [filteredAccounts, accounts.data, selectedId],
  )

  const marketMap = useMemo(
    () => new Map((markets.data ?? []).map((market) => [market.id, `${market.code} · ${market.name}`])),
    [markets.data],
  )

  useEffect(() => {
    if (selected) {
      setForm({
        provider: selected.provider,
        account_id: selected.account_id,
        display_name: selected.display_name ?? '',
        market_id: selected.market_id ?? undefined,
        is_active: selected.is_active,
        priority: selected.priority,
        fallback_account_id: selected.fallback_account_id ?? '',
      })
      setRouteExplainInput((s) => ({
        ...s,
        provider: selected.provider,
        market_id: selected.market_id ? String(selected.market_id) : '',
        requested_account_id: selected.account_id,
      }))
    } else {
      setForm(emptyAccountForm())
    }
  }, [selected])

  const saveMutation = useMutation({
    mutationFn: async () => {
      const payload = {
        provider: form.provider,
        account_id: form.account_id,
        display_name: form.display_name || null,
        market_id: form.market_id || null,
        is_active: Boolean(form.is_active),
        priority: Number(form.priority || 100),
        fallback_account_id: form.fallback_account_id || null,
      }
      if (selectedId) return api.updateChannelControlAccount(selectedId, payload)
      return api.createChannelControlAccount(payload)
    },
    onSuccess: async (saved) => {
      setSelectedId(saved.id)
      setToast({ message: selectedId ? '渠道控制面账号已更新' : '渠道控制面账号已创建', tone: 'success' })
      await client.invalidateQueries({ queryKey: ['channel-control-accounts'] })
    },
    onError: (err: Error) => setToast({ message: err.message || '保存账号失败', tone: 'danger' }),
  })

  const routeExplainMutation = useMutation({
    mutationFn: () => api.explainChannelRoute({
      provider: routeExplainInput.provider || undefined,
      market_id: routeExplainInput.market_id ? Number(routeExplainInput.market_id) : undefined,
      requested_account_id: routeExplainInput.requested_account_id || undefined,
      ticket_id: routeExplainInput.ticket_id ? Number(routeExplainInput.ticket_id) : undefined,
    }),
    onError: (err: Error) => setToast({ message: err.message || '路由解释失败', tone: 'danger' }),
  })

  const createOnboardingMutation = useMutation({
    mutationFn: () => api.createOnboardingTask({
      provider: onboardingForm.provider,
      market_id: onboardingForm.market_id ? Number(onboardingForm.market_id) : null,
      target_slot: onboardingForm.target_slot || null,
      desired_display_name: onboardingForm.desired_display_name || null,
      desired_channel_account_binding: onboardingForm.desired_channel_account_binding || null,
    }),
    onSuccess: async () => {
      setToast({ message: 'Onboarding 任务已创建，等待 OpenClaw 执行。', tone: 'success' })
      setOnboardingForm(emptyOnboardingForm())
      await client.invalidateQueries({ queryKey: ['channel-onboarding-tasks'] })
    },
    onError: (err: Error) => setToast({ message: err.message || '创建 onboarding 任务失败', tone: 'danger' }),
  })

  const updateOnboardingMutation = useMutation({
    mutationFn: ({ taskId, payload }: { taskId: number; payload: Record<string, unknown> }) => api.updateOnboardingTask(taskId, payload),
    onSuccess: async () => {
      setToast({ message: 'Onboarding 状态已更新。', tone: 'success' })
      await client.invalidateQueries({ queryKey: ['channel-onboarding-tasks'] })
    },
    onError: (err: Error) => setToast({ message: err.message || '更新 onboarding 状态失败', tone: 'danger' }),
  })

  return (
    <AppShell>
      <PageHeader
        eyebrow="Channel Control Plane"
        title="渠道配置中心"
        description="把 channel account 从技术配置页升级为业务控制平面：前台只允许编辑业务字段，执行侧状态只读展示；同时补上路由解释和 WhatsApp onboarding 骨架。"
        actions={<div className="button-row"><Button variant="secondary" onClick={() => autoRefresh.setEnabled(!autoRefresh.enabled)}>{autoRefresh.enabled ? '暂停刷新' : '恢复刷新'}</Button><Button onClick={() => { setSelectedId(null); setForm(emptyAccountForm()) }} disabled={!permitted}>新建账号</Button></div>}
      />
      {!permitted ? (
        <Card>
          <CardHeader title="无权限访问" subtitle="渠道控制面由主管或管理员维护。" />
          <CardBody><div className="message" data-role="agent">如遇到渠道账号失效、路由切换或 onboarding 需求，请联系主管或管理员处理。</div></CardBody>
        </Card>
      ) : (
        <>
          <div className="metrics-grid metrics-grid-wide">
            <MetricCard label="账号总数" value={accounts.data?.length ?? '—'} />
            <MetricCard label="启用中" value={(accounts.data ?? []).filter((a) => a.is_active).length} />
            <MetricCard label="已配置 fallback" value={(accounts.data ?? []).filter((a) => !!a.fallback_account_id).length} />
            <MetricCard label="健康正常" value={(accounts.data ?? []).filter((a) => a.health_status === 'healthy').length} />
            <MetricCard label="待执行 onboarding" value={(onboardingTasks.data ?? []).filter((a) => ['pending', 'running'].includes(a.status)).length} />
            <MetricCard label="最近失败 onboarding" value={(onboardingTasks.data ?? []).filter((a) => a.status === 'failed').length} />
          </div>

          <div className="workspace-toolbar">
            <SegmentedControl value={providerFilter} onChange={setProviderFilter} options={PROVIDERS} />
            <div className="workspace-toolbar-meta">账号 {filteredAccounts.length} 个 · onboarding 任务 {(onboardingTasks.data ?? []).length} 个</div>
          </div>

          <div className="page-grid split-grid-wide">
            <Card>
              <CardHeader title="账号列表" subtitle="左侧看业务控制面账号，右侧维护业务字段；健康状态和心跳时间只读展示。" />
              <CardBody>
                <div className="list">
                  {filteredAccounts.map((account) => (
                    <button key={account.id} className={`queue-card ${selectedId === account.id ? 'selected' : ''}`} onClick={() => setSelectedId(account.id)}>
                      <div className="badges">
                        <Badge>{labelize(account.provider)}</Badge>
                        <Badge tone={account.is_active ? 'success' : 'danger'}>{account.is_active ? '启用中' : '已停用'}</Badge>
                        <Badge tone={healthTone(account.health_status)}>{labelize(account.health_status)}</Badge>
                      </div>
                      <div className="queue-card-title">{sanitizeDisplayText(account.display_name || account.account_id)}</div>
                      <div className="queue-card-meta">市场：{account.market_id ? sanitizeDisplayText(marketMap.get(account.market_id) || `ID ${account.market_id}`) : '全局通用'} · 优先级：{account.priority}</div>
                      <div className="queue-card-meta">fallback：{sanitizeDisplayText(account.fallback_account_id || '未配置')} · 最近心跳：{formatDateTime(account.last_health_check_at)}</div>
                    </button>
                  ))}
                  {!filteredAccounts.length ? <EmptyState text="当前筛选条件下没有账号。" /> : null}
                </div>
              </CardBody>
            </Card>

            <Card>
              <CardHeader title={selectedId ? '编辑业务控制面字段' : '新建业务控制面账号'} subtitle="这里维护 provider / account_id / display_name / market / is_active / priority / fallback。执行面状态只读，不允许人工伪造健康结果。" />
              <CardBody>
                <div className="stack">
                  <div className="form-grid">
                    <Field label="Provider"><Select value={form.provider ?? 'whatsapp'} onChange={(e) => setForm((s) => ({ ...s, provider: e.target.value }))} disabled={!!selectedId}>{PROVIDERS.filter((item) => item.value !== 'all').map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</Select></Field>
                    <Field label="Account ID"><Input value={form.account_id ?? ''} onChange={(e) => setForm((s) => ({ ...s, account_id: e.target.value }))} disabled={!!selectedId} /></Field>
                    <Field label="Display Name"><Input value={form.display_name ?? ''} onChange={(e) => setForm((s) => ({ ...s, display_name: e.target.value }))} /></Field>
                    <Field label="Market"><Select value={String(form.market_id ?? '')} onChange={(e) => setForm((s) => ({ ...s, market_id: e.target.value ? Number(e.target.value) : undefined }))}><option value="">全局 / 不区分市场</option>{(markets.data ?? []).map((market) => <option key={market.id} value={market.id}>{market.code} · {market.name}</option>)}</Select></Field>
                    <Field label="Priority"><Input type="number" value={String(form.priority ?? 100)} onChange={(e) => setForm((s) => ({ ...s, priority: Number(e.target.value) }))} /></Field>
                    <Field label="Fallback Account ID" hint="服务端会校验：不能指向自己、必须存在、provider 一致、market 关系合理。"><Input value={form.fallback_account_id ?? ''} onChange={(e) => setForm((s) => ({ ...s, fallback_account_id: e.target.value }))} /></Field>
                  </div>

                  <label className="toggle-row"><input type="checkbox" checked={Boolean(form.is_active)} onChange={(e) => setForm((s) => ({ ...s, is_active: e.target.checked }))} /> 当前账号启用</label>

                  {selected ? (
                    <Card className="soft">
                      <CardHeader title="执行面状态（只读）" subtitle="这些字段来自 runtime / probe / worker，不允许在控制面手工伪造。" />
                      <CardBody>
                        <div className="guide-grid">
                          <div className="guide-item"><strong>health_status</strong><span>{sanitizeDisplayText(selected.health_status)}</span></div>
                          <div className="guide-item"><strong>last_health_check_at</strong><span>{formatDateTime(selected.last_health_check_at)}</span></div>
                          <div className="guide-item"><strong>updated_at</strong><span>{formatDateTime(selected.updated_at)}</span></div>
                          <div className="guide-item"><strong>route summary</strong><span>{selected.fallback_account_id ? `主账号 ${selected.account_id} → fallback ${selected.fallback_account_id}` : `主账号 ${selected.account_id}，无 fallback`}</span></div>
                        </div>
                      </CardBody>
                    </Card>
                  ) : null}

                  <div className="button-row">
                    <Button variant="primary" onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending}>{saveMutation.isPending ? '保存中…' : selectedId ? '保存修改' : '创建账号'}</Button>
                    <Button onClick={() => { setSelectedId(null); setForm(emptyAccountForm()) }}>重置</Button>
                  </div>
                </div>
              </CardBody>
            </Card>
          </div>

          <div className="page-grid split-grid">
            <Card>
              <CardHeader title="路由解释 / 命中说明" subtitle="给管理员解释某条 ticket 或某个 accountId 为什么首发命中这条账号，避免“看起来有 fallback 但不知道什么时候触发”。" />
              <CardBody>
                <div className="stack">
                  <div className="form-grid">
                    <Field label="Provider"><Select value={routeExplainInput.provider} onChange={(e) => setRouteExplainInput((s) => ({ ...s, provider: e.target.value }))}>{PROVIDERS.filter((item) => item.value !== 'all').map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</Select></Field>
                    <Field label="Market"><Select value={routeExplainInput.market_id} onChange={(e) => setRouteExplainInput((s) => ({ ...s, market_id: e.target.value }))}><option value="">不指定</option>{(markets.data ?? []).map((market) => <option key={market.id} value={market.id}>{market.code} · {market.name}</option>)}</Select></Field>
                    <Field label="Requested Account ID"><Input value={routeExplainInput.requested_account_id} onChange={(e) => setRouteExplainInput((s) => ({ ...s, requested_account_id: e.target.value }))} /></Field>
                    <Field label="Ticket ID（可选）"><Input value={routeExplainInput.ticket_id} onChange={(e) => setRouteExplainInput((s) => ({ ...s, ticket_id: e.target.value }))} /></Field>
                  </div>
                  <div className="button-row"><Button onClick={() => routeExplainMutation.mutate()} disabled={routeExplainMutation.isPending}>{routeExplainMutation.isPending ? '解释中…' : '执行解释'}</Button></div>
                  {routeExplainMutation.data ? (
                    <div className="list">
                      <div className="list-item">
                        <div className="badges">
                          <Badge tone="success">selected</Badge>
                          {routeExplainMutation.data.fallback_account ? <Badge>fallback ready</Badge> : <Badge>no fallback</Badge>}
                        </div>
                        <div><strong>{sanitizeDisplayText(routeExplainMutation.data.selected_account?.display_name || routeExplainMutation.data.selected_account?.account_id || '未命中账号')}</strong></div>
                        <div className="section-subtitle">{sanitizeDisplayText(routeExplainMutation.data.selected_account?.account_id || '')}</div>
                      </div>
                      {routeExplainMutation.data.debug_steps.map((step) => <div key={step} className="list-item"><div className="section-subtitle">{sanitizeDisplayText(step)}</div></div>)}
                    </div>
                  ) : <EmptyState text="执行解释后，这里会显示命中步骤和 fallback 说明。" />}
                </div>
              </CardBody>
            </Card>

            <Card>
              <CardHeader title="WhatsApp onboarding 骨架" subtitle="Helpdesk 负责发起与记录，OpenClaw 负责登录 / pairing / account lifecycle execution；本轮只做任务状态机，不伪造网页内扫码登录。" />
              <CardBody>
                <div className="stack">
                  <div className="form-grid">
                    <Field label="Provider"><Select value={onboardingForm.provider} onChange={(e) => setOnboardingForm((s) => ({ ...s, provider: e.target.value }))}><option value="whatsapp">WhatsApp</option><option value="telegram">Telegram</option><option value="sms">SMS</option><option value="email">Email</option><option value="web_chat">Web Chat</option></Select></Field>
                    <Field label="Target Market"><Select value={onboardingForm.market_id} onChange={(e) => setOnboardingForm((s) => ({ ...s, market_id: e.target.value }))}><option value="">不指定</option>{(markets.data ?? []).map((market) => <option key={market.id} value={market.id}>{market.code} · {market.name}</option>)}</Select></Field>
                    <Field label="Target Slot"><Input value={onboardingForm.target_slot} onChange={(e) => setOnboardingForm((s) => ({ ...s, target_slot: e.target.value }))} /></Field>
                    <Field label="Desired Display Name"><Input value={onboardingForm.desired_display_name} onChange={(e) => setOnboardingForm((s) => ({ ...s, desired_display_name: e.target.value }))} /></Field>
                    <Field label="Desired Channel Binding"><Input value={onboardingForm.desired_channel_account_binding} onChange={(e) => setOnboardingForm((s) => ({ ...s, desired_channel_account_binding: e.target.value }))} /></Field>
                  </div>
                  <div className="button-row"><Button onClick={() => createOnboardingMutation.mutate()} disabled={createOnboardingMutation.isPending}>{createOnboardingMutation.isPending ? '创建中…' : '发起 onboarding 任务'}</Button></div>

                  <div className="list">
                    {(onboardingTasks.data ?? []).map((task: ChannelOnboardingTask) => (
                      <div key={task.id} className="list-item">
                        <div className="badges">
                          <Badge>{labelize(task.provider)}</Badge>
                          <Badge tone={task.status === 'success' ? 'success' : task.status === 'failed' ? 'danger' : 'warning'}>{labelize(task.status)}</Badge>
                        </div>
                        <div><strong>{sanitizeDisplayText(task.desired_display_name || `task-${task.id}`)}</strong></div>
                        <div className="section-subtitle">market {task.market_id ?? '—'} · slot {sanitizeDisplayText(task.target_slot || '—')} · openclawAccountId {sanitizeDisplayText(task.openclaw_account_id || '—')}</div>
                        <div className="section-subtitle">最近错误：{sanitizeDisplayText(task.last_error || '无')}</div>
                        <div className="button-row" style={{ marginTop: 8 }}>
                          <Button variant="secondary" onClick={() => updateOnboardingMutation.mutate({ taskId: task.id, payload: { status: 'running', last_error: null } })} disabled={updateOnboardingMutation.isPending || task.status === 'running'}>标记 running</Button>
                          <Button variant="secondary" onClick={() => updateOnboardingMutation.mutate({ taskId: task.id, payload: { status: 'success', last_error: null, openclaw_account_id: task.openclaw_account_id || task.desired_channel_account_binding || '' } })} disabled={updateOnboardingMutation.isPending || task.status === 'success'}>标记 success</Button>
                          <Button variant="secondary" onClick={() => updateOnboardingMutation.mutate({ taskId: task.id, payload: { status: 'failed', last_error: task.last_error || 'Manual failure note required from execution plane' } })} disabled={updateOnboardingMutation.isPending || task.status === 'failed'}>标记 failed</Button>
                        </div>
                      </div>
                    ))}
                    {!onboardingTasks.data?.length ? <EmptyState text="还没有 onboarding 任务。" /> : null}
                  </div>
                </div>
              </CardBody>
            </Card>
          </div>
        </>
      )}
      {toast ? <Toast message={toast.message} tone={toast.tone} onClose={() => setToast(null)} /> : null}
    </AppShell>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/accounts',
  beforeLoad: () => { if (!getToken()) throw redirect({ to: '/login' }) },
  component: AccountsPage,
})

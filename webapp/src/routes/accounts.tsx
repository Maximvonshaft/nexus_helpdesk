import { useEffect, useMemo, useState } from 'react'
import { createRoute, redirect, useNavigate } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import type { ChannelAccount } from '@/lib/types'
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
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { ErrorSummary } from '@/components/ui/ErrorSummary'
import { useAutoRefresh } from '@/hooks/useAutoRefresh'
import { useSession } from '@/hooks/useAuth'
import { canManageChannels } from '@/lib/access'
import { accountHealthLabels } from '@/lib/uxCopy'

const PROVIDERS = [
  { label: 'WhatsApp', value: 'whatsapp' },
  { label: 'Telegram', value: 'telegram' },
  { label: 'SMS', value: 'sms' },
]

function emptyForm(): Partial<ChannelAccount> {
  return {
    provider: 'whatsapp',
    account_id: '',
    display_name: '',
    market_id: undefined,
    is_active: true,
    priority: 100,
    health_status: 'unknown',
    fallback_account_id: '',
  }
}

function AccountsPage() {
  const client = useQueryClient()
  const autoRefresh = useAutoRefresh(true)
  const session = useSession()
  const navigate = useNavigate()
  const permitted = canManageChannels(session.data)
  const accounts = useQuery({ queryKey: ['channelAccounts'], queryFn: api.channelAccounts, refetchInterval: autoRefresh.enabled ? 30000 : false, enabled: permitted })
  const markets = useQuery({ queryKey: ['markets'], queryFn: api.markets, enabled: permitted })
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [provider, setProvider] = useState('all')
  const [health, setHealth] = useState('all')
  const [form, setForm] = useState<Partial<ChannelAccount>>(emptyForm())
  const [toast, setToast] = useState<{ message: string; tone?: 'default' | 'danger' | 'success' } | null>(null)
  const [dirty, setDirty] = useState(false)
  const [confirmReset, setConfirmReset] = useState(false)

  useEffect(() => {
    if (session.data && !permitted) navigate({ to: '/' })
  }, [navigate, permitted, session.data])

  const filteredAccounts = useMemo(() => (accounts.data ?? []).filter((item) => (provider === 'all' || item.provider === provider) && (health === 'all' || item.health_status === health)), [accounts.data, provider, health])
  const selected = useMemo(() => filteredAccounts.find((item) => item.id === selectedId) ?? (accounts.data ?? []).find((item) => item.id === selectedId) ?? null, [filteredAccounts, accounts.data, selectedId])
  const marketMap = useMemo(() => new Map((markets.data ?? []).map((market) => [market.id, `${market.code} · ${market.name}`])), [markets.data])
  const fallbackOptions = useMemo(() => (accounts.data ?? []).filter((item) => {
    if (selected && item.id === selected.id) return false
    if (form.provider && item.provider !== form.provider) return false
    return item.is_active
  }), [accounts.data, form.provider, selected])

  useEffect(() => {
    if (selected) {
      setForm({
        provider: selected.provider,
        account_id: selected.account_id,
        display_name: selected.display_name ?? '',
        market_id: selected.market_id ?? undefined,
        is_active: selected.is_active,
        priority: selected.priority,
        health_status: selected.health_status,
        fallback_account_id: selected.fallback_account_id ?? '',
      })
      setDirty(false)
    } else {
      setForm(emptyForm())
      setDirty(false)
    }
  }, [selected])

  const patchForm = (patch: Partial<ChannelAccount>) => {
    setForm((current) => ({ ...current, ...patch }))
    setDirty(true)
  }

  const resetForm = () => {
    setSelectedId(null)
    setForm(emptyForm())
    setDirty(false)
    setConfirmReset(false)
  }

  const saveMutation = useMutation({
    mutationFn: async () => {
      const basePayload = {
        provider: form.provider,
        account_id: form.account_id,
        display_name: form.display_name || null,
        market_id: form.market_id || null,
        priority: Number(form.priority || 100),
        fallback_account_id: form.fallback_account_id || null,
      }
      if (selectedId) {
        return api.updateChannelAccount(selectedId, {
          ...basePayload,
          is_active: Boolean(form.is_active),
          health_status: form.health_status,
        })
      }
      return api.createChannelAccount(basePayload)
    },
    onSuccess: async (saved) => {
      setSelectedId(saved.id)
      setDirty(false)
      setToast({ message: selectedId ? '发送线路已更新' : '发送线路已创建', tone: 'success' })
      await client.invalidateQueries({ queryKey: ['channelAccounts'] })
    },
    onError: (err: Error) => setToast({ message: err.message || '保存发送线路失败', tone: 'danger' }),
  })

  return (
    <AppShell>
      <PageHeader
        eyebrow="发送线路"
        title="渠道账号与备用线路"
        description="渠道账号页面必须只暴露真实有效的后端能力。本版创建态只提交基础字段，编辑态维护启停与健康状态。"
        actions={<div className="button-row"><Button variant="secondary" onClick={() => autoRefresh.setEnabled(!autoRefresh.enabled)}>{autoRefresh.enabled ? '暂停刷新' : '恢复刷新'}</Button><Button onClick={() => dirty ? setConfirmReset(true) : resetForm()} disabled={!permitted}>新建发送线路</Button></div>}
      />
      {!permitted ? (
        <Card>
          <CardHeader title="无权限访问" subtitle="发送线路由主管或管理员维护。" />
          <CardBody><div className="message" data-role="agent">如遇到账号失效、渠道限流或备用线路切换需求，请联系主管或管理员处理。</div></CardBody>
        </Card>
      ) : (
        <>
          <div className="metrics-grid metrics-grid-wide">
            <MetricCard label="账号总数" value={accounts.data?.length ?? '—'} />
            <MetricCard label="状态正常" value={(accounts.data ?? []).filter((a) => a.health_status === 'healthy').length} />
            <MetricCard label="状态受限" value={(accounts.data ?? []).filter((a) => a.health_status === 'degraded').length} />
            <MetricCard label="已离线" value={(accounts.data ?? []).filter((a) => a.health_status === 'offline').length} />
            <MetricCard label="启用中" value={(accounts.data ?? []).filter((a) => a.is_active).length} />
            <MetricCard label="已配置备用" value={(accounts.data ?? []).filter((a) => !!a.fallback_account_id).length} />
          </div>
          <div className="workspace-toolbar">
            <SegmentedControl value={provider} onChange={setProvider} options={[{ label: '全部渠道', value: 'all' }, ...PROVIDERS]} />
            <SegmentedControl value={health} onChange={setHealth} options={[{ label: '全部状态', value: 'all' }, { label: '正常', value: 'healthy' }, { label: '受限', value: 'degraded' }, { label: '离线', value: 'offline' }]} />
            <div className="workspace-toolbar-meta">共 {filteredAccounts.length} 个账号</div>
          </div>
          <div className="page-grid split-grid-wide">
            <Card>
              <CardHeader title="发送线路列表" subtitle="查看哪个账号负责哪个市场，是否可用，是否有备用线路。" />
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
                      <div className="queue-card-meta">备用账号：{sanitizeDisplayText(account.fallback_account_id || '未配置')} · 更新时间：{formatDateTime(account.updated_at)}</div>
                    </button>
                  ))}
                  {!filteredAccounts.length ? <EmptyState title="没有符合条件的发送线路" description="当前渠道或健康状态筛选下没有账号。" reason="可以清空筛选，或创建新的发送线路。" action={<Button variant="secondary" onClick={() => { setProvider('all'); setHealth('all') }}>清空筛选</Button>} /> : null}
                </div>
              </CardBody>
            </Card>
            <Card>
              <CardHeader title={selectedId ? '编辑发送线路' : '新建发送线路'} subtitle={selectedId ? '编辑态负责启停、健康状态和备用线路维护。' : '创建态只提交 provider / account_id / market / priority / fallback 等真实生效字段。'} />
              <CardBody>
                <div className="stack">
                  {!selectedId ? <div className="message" data-role="agent">创建后再维护健康状态和启停，避免出现“前端可填但后端创建根本不认”的假字段。</div> : null}
                  {saveMutation.isError ? <ErrorSummary errors={[saveMutation.error?.message || '保存发送线路失败，请检查账号编号、市场和备用线路后重试。']} /> : null}
                  <div className="form-grid">
                    <Field label="渠道类型" required><Select value={form.provider ?? 'whatsapp'} onChange={(e) => patchForm({ provider: e.target.value, fallback_account_id: '' })}>{PROVIDERS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</Select></Field>
                    <Field label="账号编号" required example="whatsapp-ch-main"><Input value={form.account_id ?? ''} onChange={(e) => patchForm({ account_id: e.target.value })} /></Field>
                    <Field label="账号名称" example="瑞士 WhatsApp 主线路"><Input value={form.display_name ?? ''} onChange={(e) => patchForm({ display_name: e.target.value })} /></Field>
                    <Field label="绑定市场"><Select value={String(form.market_id ?? '')} onChange={(e) => patchForm({ market_id: e.target.value ? Number(e.target.value) : undefined })}><option value="">不绑定市场 / 全局通用</option>{(markets.data ?? []).map((market) => <option key={market.id} value={market.id}>{market.code} · {market.name}</option>)}</Select></Field>
                    <Field label="优先级" hint="数字越小越优先。"><Input type="number" value={String(form.priority ?? 100)} onChange={(e) => patchForm({ priority: Number(e.target.value) })} /></Field>
                    <Field label="备用发送线路" description="主账号不可用时自动切换到这里，避免让客服手填不可验证的账号编号。">
                      <Select value={form.fallback_account_id ?? ''} onChange={(e) => patchForm({ fallback_account_id: e.target.value })}>
                        <option value="">不配置备用线路</option>
                        {fallbackOptions.map((account) => <option key={account.id} value={account.account_id}>{sanitizeDisplayText(account.display_name || account.account_id)} · {accountHealthLabels[account.health_status] ?? labelize(account.health_status)}</option>)}
                      </Select>
                    </Field>
                    {selectedId ? <Field label="账号状态"><Select value={form.health_status ?? 'unknown'} onChange={(e) => patchForm({ health_status: e.target.value })}><option value="unknown">未知</option><option value="healthy">正常</option><option value="degraded">受限</option><option value="offline">离线</option></Select></Field> : null}
                  </div>
                  {selectedId ? <label className="toggle-row"><input type="checkbox" checked={Boolean(form.is_active)} onChange={(e) => patchForm({ is_active: e.target.checked })} /> 当前账号启用</label> : null}
                  <div className="button-row"><Button variant="primary" onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending}>{saveMutation.isPending ? '保存中…' : selectedId ? '保存修改' : '创建线路'}</Button><Button onClick={() => dirty ? setConfirmReset(true) : resetForm()}>重置</Button></div>
                </div>
              </CardBody>
            </Card>
          </div>
        </>
      )}
      {toast ? <Toast message={toast.message} tone={toast.tone} onClose={() => setToast(null)} /> : null}
      <ConfirmDialog
        open={confirmReset}
        title="放弃当前发送线路编辑？"
        description="当前表单还有未保存内容。继续重置会丢弃账号名称、市场、备用线路和启停状态的编辑。"
        consequence="如需保留，请先保存修改。"
        confirmLabel="放弃并重置"
        tone="danger"
        onCancel={() => setConfirmReset(false)}
        onConfirm={resetForm}
      />
    </AppShell>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/accounts',
  beforeLoad: () => { if (!getToken()) throw redirect({ to: '/login' }) },
  component: AccountsPage,
})

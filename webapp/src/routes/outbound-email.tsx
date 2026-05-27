import { useEffect, useMemo, useState } from 'react'
import { createRoute, redirect, useNavigate } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import type { BadgeTone, OutboundEmailAccount, OutboundEmailAccountCreate, OutboundEmailAccountUpdate, OutboundEmailSecurityMode } from '@/lib/types'
import { boolLabel, formatDateTime, labelize, sanitizeDisplayText } from '@/lib/format'
import { accountHealthLabels, smtpFailureLabels } from '@/lib/uxCopy'
import { canManageChannels } from '@/lib/access'
import { useSession } from '@/hooks/useAuth'
import { useAutoRefresh } from '@/hooks/useAutoRefresh'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { EmptyState } from '@/components/ui/EmptyState'
import { ErrorSummary } from '@/components/ui/ErrorSummary'
import { Field, Input, Select, Textarea } from '@/components/ui/Field'
import { MetricCard } from '@/components/ui/MetricCard'
import { PageHeader } from '@/components/ui/PageHeader'
import { SegmentedControl } from '@/components/ui/SegmentedControl'
import { TechnicalDetails } from '@/components/ui/TechnicalDetails'
import { Toast } from '@/components/ui/Toast'

type EmailForm = {
  display_name: string
  host: string
  port: number
  username: string
  password: string
  from_address: string
  reply_to: string
  security_mode: OutboundEmailSecurityMode
  market_id: number | null
  priority: number
  is_active: boolean
}

const SECURITY_OPTIONS: Array<{ label: string; value: OutboundEmailSecurityMode; port: number }> = [
  { label: 'STARTTLS', value: 'starttls', port: 587 },
  { label: 'SSL/TLS', value: 'ssl', port: 465 },
  { label: 'Plain', value: 'plain', port: 25 },
]

function suggestedPort(mode: OutboundEmailSecurityMode) {
  return SECURITY_OPTIONS.find((item) => item.value === mode)?.port ?? 587
}

function emptyForm(): EmailForm {
  return {
    display_name: '',
    host: '',
    port: 587,
    username: '',
    password: '',
    from_address: '',
    reply_to: '',
    security_mode: 'starttls',
    market_id: null,
    priority: 100,
    is_active: true,
  }
}

function formFromAccount(account: OutboundEmailAccount): EmailForm {
  const securityMode = SECURITY_OPTIONS.some((item) => item.value === account.security_mode) ? account.security_mode as OutboundEmailSecurityMode : 'starttls'
  return {
    display_name: account.display_name ?? '',
    host: account.host,
    port: account.port,
    username: account.username,
    password: '',
    from_address: account.from_address,
    reply_to: account.reply_to ?? '',
    security_mode: securityMode,
    market_id: account.market_id ?? null,
    priority: account.priority,
    is_active: account.is_active,
  }
}

function emailHealthTone(value?: string | null): BadgeTone {
  const normalized = String(value || '').trim().toLowerCase()
  if (normalized === 'ok' || normalized === 'healthy' || normalized === 'success') return 'success'
  if (normalized === 'error' || normalized === 'offline') return 'danger'
  if (normalized === 'degraded' || normalized === 'warning') return 'warning'
  return 'default'
}

function emailHealthLabel(value?: string | null) {
  const key = String(value || '').trim().toLowerCase()
  return accountHealthLabels[key] || labelize(value)
}

function smtpFailureLabel(value?: string | null) {
  if (!value) return '未知结果'
  return smtpFailureLabels[value] || labelize(value)
}

function isDefaultPort(value: number, mode: OutboundEmailSecurityMode) {
  return value === suggestedPort(mode)
}

function normalizeOptional(value: string) {
  const cleaned = value.trim()
  return cleaned || null
}

function buildCreatePayload(form: EmailForm): OutboundEmailAccountCreate {
  const password = form.password.trim()
  if (!password) throw new Error('创建 SMTP 账号必须填写密码。')
  return {
    display_name: normalizeOptional(form.display_name),
    host: form.host.trim(),
    port: Number(form.port),
    username: form.username.trim(),
    password,
    from_address: form.from_address.trim(),
    reply_to: normalizeOptional(form.reply_to),
    security_mode: form.security_mode,
    market_id: form.market_id,
    priority: Number(form.priority || 100),
    is_active: Boolean(form.is_active),
  }
}

function buildUpdatePayload(form: EmailForm): OutboundEmailAccountUpdate {
  const payload: OutboundEmailAccountUpdate = {
    display_name: normalizeOptional(form.display_name),
    host: form.host.trim(),
    port: Number(form.port),
    username: form.username.trim(),
    from_address: form.from_address.trim(),
    reply_to: normalizeOptional(form.reply_to),
    security_mode: form.security_mode,
    market_id: form.market_id,
    priority: Number(form.priority || 100),
    is_active: Boolean(form.is_active),
  }
  const rotatedPassword = form.password.trim()
  if (rotatedPassword) payload.password = rotatedPassword
  return payload
}

function marketLabel(marketMap: Map<number, string>, marketId?: number | null) {
  if (!marketId) return '全局 fallback'
  return marketMap.get(marketId) || `Market ID ${marketId}`
}

function OutboundEmailPage() {
  const client = useQueryClient()
  const session = useSession()
  const navigate = useNavigate()
  const autoRefresh = useAutoRefresh(true)
  const permitted = canManageChannels(session.data)
  const accounts = useQuery({ queryKey: ['outboundEmailAccounts'], queryFn: api.outboundEmailAccounts, refetchInterval: autoRefresh.enabled ? 30000 : false, enabled: permitted })
  const markets = useQuery({ queryKey: ['markets'], queryFn: api.markets, enabled: permitted })
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [statusFilter, setStatusFilter] = useState('all')
  const [form, setForm] = useState<EmailForm>(emptyForm())
  const [dirty, setDirty] = useState(false)
  const [confirmReset, setConfirmReset] = useState(false)
  const [disableTarget, setDisableTarget] = useState<OutboundEmailAccount | null>(null)
  const [testRecipient, setTestRecipient] = useState('')
  const [testSubject, setTestSubject] = useState('NexusDesk SMTP test')
  const [testBody, setTestBody] = useState('This is a NexusDesk outbound email test message.')
  const [toast, setToast] = useState<{ message: string; tone?: 'default' | 'danger' | 'success' } | null>(null)

  useEffect(() => {
    if (session.data && !permitted) navigate({ to: '/' })
  }, [navigate, permitted, session.data])

  const marketMap = useMemo(() => new Map((markets.data ?? []).map((market) => [market.id, `${market.code} · ${market.name}`])), [markets.data])
  const filteredAccounts = useMemo(() => (accounts.data ?? []).filter((account) => {
    if (statusFilter === 'active') return account.is_active
    if (statusFilter === 'inactive') return !account.is_active
    if (statusFilter === 'error') return account.health_status === 'error'
    if (statusFilter === 'ok') return account.health_status === 'ok'
    return true
  }), [accounts.data, statusFilter])
  const selected = useMemo(() => (accounts.data ?? []).find((account) => account.id === selectedId) ?? null, [accounts.data, selectedId])

  useEffect(() => {
    if (selected) {
      setForm(formFromAccount(selected))
      setDirty(false)
      return
    }
    setForm(emptyForm())
    setDirty(false)
  }, [selected])

  const patchForm = (patch: Partial<EmailForm>) => {
    setForm((current) => ({ ...current, ...patch }))
    setDirty(true)
  }

  const onSecurityModeChange = (nextMode: OutboundEmailSecurityMode) => {
    setForm((current) => ({
      ...current,
      security_mode: nextMode,
      port: isDefaultPort(current.port, current.security_mode) ? suggestedPort(nextMode) : current.port,
    }))
    setDirty(true)
  }

  const resetForm = () => {
    setSelectedId(null)
    setForm(emptyForm())
    setDirty(false)
    setConfirmReset(false)
  }

  const refreshAccounts = async () => {
    await client.invalidateQueries({ queryKey: ['outboundEmailAccounts'] })
  }

  const saveMutation = useMutation({
    mutationFn: async () => {
      if (selectedId) return api.updateOutboundEmailAccount(selectedId, buildUpdatePayload(form))
      return api.createOutboundEmailAccount(buildCreatePayload(form))
    },
    onSuccess: async (saved) => {
      setSelectedId(saved.id)
      setForm(formFromAccount(saved))
      setDirty(false)
      setToast({ message: selectedId ? 'Outbound Email 账号已更新' : 'Outbound Email 账号已创建', tone: 'success' })
      await refreshAccounts()
    },
    onError: (err: Error) => setToast({ message: err.message || '保存 Outbound Email 账号失败', tone: 'danger' }),
  })

  const enableMutation = useMutation({
    mutationFn: (accountId: number) => api.enableOutboundEmailAccount(accountId),
    onSuccess: async (saved) => {
      setToast({ message: 'Outbound Email 账号已启用', tone: 'success' })
      setSelectedId(saved.id)
      await refreshAccounts()
    },
    onError: (err: Error) => setToast({ message: err.message || '启用账号失败', tone: 'danger' }),
  })

  const disableMutation = useMutation({
    mutationFn: (accountId: number) => api.disableOutboundEmailAccount(accountId),
    onSuccess: async (saved) => {
      setToast({ message: 'Outbound Email 账号已停用', tone: 'success' })
      setSelectedId(saved.id)
      setDisableTarget(null)
      await refreshAccounts()
    },
    onError: (err: Error) => setToast({ message: err.message || '停用账号失败', tone: 'danger' }),
  })

  const testMutation = useMutation({
    mutationFn: async () => {
      if (!selectedId) throw new Error('请先选择一个已保存的 SMTP 账号。')
      const recipient = testRecipient.trim()
      if (!recipient) throw new Error('测试收件人邮箱不能为空。')
      return api.testOutboundEmailAccount(selectedId, {
        to_address: recipient,
        subject: normalizeOptional(testSubject),
        body: normalizeOptional(testBody),
      })
    },
    onSuccess: async (result) => {
      setToast({
        message: result.ok ? '测试邮件已发送，账号健康状态已更新' : `测试发送失败：${smtpFailureLabel(result.failure_code || result.provider_status)}`,
        tone: result.ok ? 'success' : 'danger',
      })
      await refreshAccounts()
    },
    onError: (err: Error) => setToast({ message: err.message || '测试发送失败', tone: 'danger' }),
  })

  const validationErrors = [
    !form.host.trim() ? 'SMTP host 不能为空。' : null,
    !form.username.trim() ? 'SMTP username 不能为空。' : null,
    !form.from_address.trim() ? 'From address 不能为空。' : null,
    !selectedId && !form.password.trim() ? '创建账号时必须填写 SMTP password。' : null,
  ].filter(Boolean) as string[]

  return (
    <AppShell>
      <PageHeader
        eyebrow="Outbound Email"
        title="SMTP 账号配置"
        description="维护真实 SMTP 出站账号、市场 fallback、健康检查与测试发送。密码只在创建或轮换时提交，保存后只显示已配置状态。"
        actions={<div className="button-row"><Button variant="secondary" onClick={() => autoRefresh.setEnabled(!autoRefresh.enabled)}>{autoRefresh.enabled ? '暂停刷新' : '恢复刷新'}</Button><Button onClick={() => dirty ? setConfirmReset(true) : resetForm()} disabled={!permitted}>新建 SMTP 账号</Button></div>}
      />
      {!permitted ? (
        <Card>
          <CardHeader title="无权限访问" subtitle="Outbound Email 账号由具备 channel_account.manage 的管理员维护。" />
          <CardBody><div className="message" data-role="agent">如需新增、停用、轮换密码或测试 SMTP 账号，请联系主管或管理员处理。</div></CardBody>
        </Card>
      ) : (
        <>
          <div className="metrics-grid metrics-grid-wide">
            <MetricCard label="SMTP 账号" value={accounts.data?.length ?? '—'} />
            <MetricCard label="启用中" value={(accounts.data ?? []).filter((account) => account.is_active).length} />
            <MetricCard label="测试成功" value={(accounts.data ?? []).filter((account) => account.health_status === 'ok').length} />
            <MetricCard label="测试失败" value={(accounts.data ?? []).filter((account) => account.health_status === 'error').length} />
            <MetricCard label="全局 fallback" value={(accounts.data ?? []).filter((account) => account.market_id == null).length} />
            <MetricCard label="市场绑定" value={(accounts.data ?? []).filter((account) => account.market_id != null).length} />
          </div>

          <div className="workspace-toolbar">
            <SegmentedControl value={statusFilter} onChange={setStatusFilter} options={[
              { label: '全部', value: 'all' },
              { label: '启用中', value: 'active' },
              { label: '已停用', value: 'inactive' },
              { label: '健康正常', value: 'ok' },
              { label: '测试失败', value: 'error' },
            ]} />
            <div className="workspace-toolbar-meta">共 {filteredAccounts.length} 个 SMTP 账号</div>
          </div>

          <div className="page-grid split-grid-wide">
            <Card>
              <CardHeader title="账号列表" subtitle="按优先级选择市场专用账号；没有市场账号时回退到全局 SMTP 账号。" />
              <CardBody>
                <div className="list">
                  {filteredAccounts.map((account) => (
                    <button key={account.id} className={`queue-card ${selectedId === account.id ? 'selected' : ''}`} onClick={() => setSelectedId(account.id)}>
                      <div className="badges">
                        <Badge tone={account.is_active ? 'success' : 'danger'}>{account.is_active ? '启用中' : '已停用'}</Badge>
                        <Badge tone={emailHealthTone(account.health_status)}>{emailHealthLabel(account.health_status)}</Badge>
                        <Badge>{labelize(account.security_mode)}</Badge>
                      </div>
                      <div className="queue-card-title">{sanitizeDisplayText(account.display_name || account.from_address)}</div>
                      <div className="queue-card-meta">{sanitizeDisplayText(account.host)}:{account.port} · {sanitizeDisplayText(account.from_address)}</div>
                      <div className="queue-card-meta">范围：{sanitizeDisplayText(marketLabel(marketMap, account.market_id))} · 优先级：{account.priority} · 密码：{account.password_configured ? account.password_mask || '********' : '未配置'}</div>
                      <div className="queue-card-meta">最近测试：{sanitizeDisplayText(emailHealthLabel(account.last_test_status || account.health_status))} · {formatDateTime(account.last_test_at)}</div>
                    </button>
                  ))}
                  {!filteredAccounts.length ? <EmptyState title="没有 Outbound Email 账号" description="当前筛选下没有可维护的 SMTP 账号。" reason="创建账号后，先执行测试发送，再让客服使用 Email 外部发送。" action={<Button variant="secondary" onClick={() => { setStatusFilter('all'); resetForm() }}>新建账号</Button>} /> : null}
                </div>
              </CardBody>
            </Card>

            <Card>
              <CardHeader title={selectedId ? '编辑 SMTP 账号' : '新建 SMTP 账号'} subtitle={selectedId ? '编辑时密码为空表示不轮换；填写后才会提交新密码。' : '创建账号必须提交一次密码，保存后不会回显明文。'} />
              <CardBody>
                <div className="stack">
                  {saveMutation.isError ? <ErrorSummary errors={[saveMutation.error?.message || '保存 SMTP 账号失败，请检查 host、port、username、from address 和市场绑定。']} /> : null}
                  {validationErrors.length ? <ErrorSummary title="保存前需要补全" errors={validationErrors} /> : null}
                  <div className="form-grid">
                    <Field label="显示名称" example="瑞士客服 SMTP"><Input value={form.display_name} onChange={(event) => patchForm({ display_name: event.target.value })} /></Field>
                    <Field label="SMTP host" required example="smtp.example.com"><Input value={form.host} onChange={(event) => patchForm({ host: event.target.value })} /></Field>
                    <Field label="Port" required hint={`当前安全模式建议端口：${suggestedPort(form.security_mode)}`}><Input type="number" min={1} max={65535} value={String(form.port)} onChange={(event) => patchForm({ port: Number(event.target.value) })} /></Field>
                    <Field label="Username" required><Input value={form.username} onChange={(event) => patchForm({ username: event.target.value })} /></Field>
                    <Field label={selectedId ? '轮换密码' : 'SMTP password'} required={!selectedId} description={selectedId ? '留空表示不修改已保存密码；填写后会轮换密码并重置测试状态。' : '创建时必填。保存后 UI 只显示密码已配置和 mask。'}>
                      <Input type="password" autoComplete="new-password" value={form.password} onChange={(event) => patchForm({ password: event.target.value })} />
                    </Field>
                    <Field label="From address" required example="support@example.com"><Input type="email" value={form.from_address} onChange={(event) => patchForm({ from_address: event.target.value })} /></Field>
                    <Field label="Reply-To" example="replies@example.com"><Input type="email" value={form.reply_to} onChange={(event) => patchForm({ reply_to: event.target.value })} /></Field>
                    <Field label="Security mode" required>
                      <Select value={form.security_mode} onChange={(event) => onSecurityModeChange(event.target.value as OutboundEmailSecurityMode)}>
                        {SECURITY_OPTIONS.map((item) => <option key={item.value} value={item.value}>{item.label} · {item.port}</option>)}
                      </Select>
                    </Field>
                    <Field label="绑定市场" description="留空表示全局 fallback；市场专用账号优先于全局账号。">
                      <Select value={String(form.market_id ?? '')} onChange={(event) => patchForm({ market_id: event.target.value ? Number(event.target.value) : null })}>
                        <option value="">全局 fallback</option>
                        {(markets.data ?? []).map((market) => <option key={market.id} value={market.id}>{market.code} · {market.name}</option>)}
                      </Select>
                    </Field>
                    <Field label="优先级" hint="数字越小越优先。"><Input type="number" min={1} max={1000} value={String(form.priority)} onChange={(event) => patchForm({ priority: Number(event.target.value) })} /></Field>
                  </div>

                  {form.security_mode === 'plain' ? (
                    <div className="message" data-role="agent">
                      Plain SMTP 不加密传输凭证和邮件内容，只应在受控内网或明确批准的兼容环境使用。生产默认应优先使用 STARTTLS 或 SSL/TLS。
                    </div>
                  ) : null}

                  <label className="toggle-row">
                    <input type="checkbox" checked={form.is_active} onChange={(event) => patchForm({ is_active: event.target.checked })} />
                    当前账号启用
                  </label>

                  {selected ? (
                    <div className="kv-grid">
                      <div className="kv"><label>密码状态</label><div>{selected.password_configured ? selected.password_mask || '********' : '未配置'}</div></div>
                      <div className="kv"><label>最近测试</label><div>{sanitizeDisplayText(emailHealthLabel(selected.last_test_status || selected.health_status))} · {formatDateTime(selected.last_test_at)}</div></div>
                      <div className="kv"><label>账号范围</label><div>{sanitizeDisplayText(marketLabel(marketMap, selected.market_id))}</div></div>
                      <div className="kv"><label>是否启用</label><div>{boolLabel(selected.is_active, '启用中', '已停用')}</div></div>
                    </div>
                  ) : null}

                  {selected?.last_test_error ? (
                    <TechnicalDetails title="最近测试错误" summary={selected.last_test_status || selected.health_status}>
                      <pre>{selected.last_test_error}</pre>
                    </TechnicalDetails>
                  ) : null}

                  <div className="button-row">
                    <Button variant="primary" onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending || validationErrors.length > 0}>{saveMutation.isPending ? '保存中…' : selectedId ? '保存修改' : '创建账号'}</Button>
                    {selected?.is_active === false ? <Button onClick={() => enableMutation.mutate(selected.id)} disabled={enableMutation.isPending}>启用账号</Button> : null}
                    {selected?.is_active ? <Button variant="danger" onClick={() => setDisableTarget(selected)} disabled={disableMutation.isPending}>停用账号</Button> : null}
                    <Button onClick={() => dirty ? setConfirmReset(true) : resetForm()}>重置</Button>
                  </div>

                  <div className="section-title">测试发送</div>
                  <div className="message" data-role="agent">测试发送会发出真实邮件。请使用明确的测试收件人，不要填客户邮箱。</div>
                  <div className="form-grid">
                    <Field label="测试收件人" required><Input type="email" value={testRecipient} onChange={(event) => setTestRecipient(event.target.value)} placeholder="ops@example.com" /></Field>
                    <Field label="测试主题"><Input value={testSubject} onChange={(event) => setTestSubject(event.target.value)} /></Field>
                    <Field label="测试正文"><Textarea value={testBody} onChange={(event) => setTestBody(event.target.value)} rows={4} /></Field>
                  </div>
                  <Button variant="primary" onClick={() => testMutation.mutate()} disabled={!selectedId || !testRecipient.trim() || testMutation.isPending}>{testMutation.isPending ? '测试发送中…' : '发送测试邮件'}</Button>
                  {testMutation.data ? (
                    <div className="message" data-role={testMutation.data.ok ? 'agent' : 'user'}>
                      <strong>{testMutation.data.ok ? '测试成功' : '测试失败'}：</strong>
                      {' '}{sanitizeDisplayText(smtpFailureLabel(testMutation.data.failure_code || testMutation.data.provider_status))} · 健康状态 {sanitizeDisplayText(emailHealthLabel(testMutation.data.health_status))}
                      {testMutation.data.error_message ? <TechnicalDetails title="测试发送错误详情" summary={testMutation.data.failure_code || testMutation.data.provider_status}><pre>{testMutation.data.error_message}</pre></TechnicalDetails> : null}
                    </div>
                  ) : null}
                </div>
              </CardBody>
            </Card>
          </div>
        </>
      )}
      {toast ? <Toast message={toast.message} tone={toast.tone} onClose={() => setToast(null)} /> : null}
      <ConfirmDialog
        open={confirmReset}
        title="放弃当前 SMTP 账号编辑？"
        description="当前表单还有未保存内容。继续重置会丢弃 host、port、账号、市场绑定和密码轮换输入。"
        consequence="密码输入会被清空，已保存账号不会受影响。"
        confirmLabel="放弃并重置"
        tone="danger"
        onCancel={() => setConfirmReset(false)}
        onConfirm={resetForm}
      />
      <ConfirmDialog
        open={!!disableTarget}
        title="停用 Outbound Email 账号？"
        description={`停用后，市场 ${sanitizeDisplayText(marketLabel(marketMap, disableTarget?.market_id))} 将不会再通过该 SMTP 账号发送 Email。`}
        consequence="如果没有其它启用账号或全局 fallback，客服 Email 发送会被 capability gate 阻断。"
        confirmLabel="停用账号"
        tone="danger"
        pending={disableMutation.isPending}
        onCancel={() => setDisableTarget(null)}
        onConfirm={() => disableTarget ? disableMutation.mutate(disableTarget.id) : undefined}
      />
    </AppShell>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/outbound-email',
  beforeLoad: () => { if (!getToken()) throw redirect({ to: '/login' }) },
  component: OutboundEmailPage,
})

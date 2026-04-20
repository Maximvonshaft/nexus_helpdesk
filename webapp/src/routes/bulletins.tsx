import { useEffect, useMemo, useState } from 'react'
import { createRoute, redirect } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import type { Bulletin } from '@/lib/types'
import { formatDateTime, labelize, sanitizeDisplayText, severityTone } from '@/lib/format'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Field, Input, Select, Textarea } from '@/components/ui/Field'
import { EmptyState } from '@/components/ui/EmptyState'
import { PageHeader } from '@/components/ui/PageHeader'
import { Toast } from '@/components/ui/Toast'
import { SegmentedControl } from '@/components/ui/SegmentedControl'
import { useAutoRefresh } from '@/hooks/useAutoRefresh'
import { useSession } from '@/hooks/useAuth'
import { canEditBulletins } from '@/lib/access'

function emptyForm(): Partial<Bulletin> {
  return {
    title: '',
    body: '',
    summary: '',
    category: 'notice',
    audience: 'customer',
    severity: 'info',
    auto_inject_to_ai: true,
    is_active: true,
    market_id: undefined,
    country_code: '',
    channels_csv: '',
  }
}

function BulletinsPage() {
  const client = useQueryClient()
  const autoRefresh = useAutoRefresh(true)
  const session = useSession()
  const canEdit = canEditBulletins(session.data)
  const bulletins = useQuery({ queryKey: ['bulletins'], queryFn: api.bulletins, refetchInterval: autoRefresh.enabled ? 30000 : false })
  const markets = useQuery({ queryKey: ['markets'], queryFn: api.markets })
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [query, setQuery] = useState('')
  const [mode, setMode] = useState('all')
  const [form, setForm] = useState<Partial<Bulletin>>(emptyForm())
  const [toast, setToast] = useState<{ message: string; tone?: 'default' | 'danger' | 'success' } | null>(null)

  const filteredBulletins = useMemo(() => (bulletins.data ?? []).filter((item) => (mode === 'all' || (mode === 'active' ? item.is_active : !item.is_active)) && (query ? `${item.title} ${item.summary ?? ''} ${item.body}`.toLowerCase().includes(query.toLowerCase()) : true)), [bulletins.data, mode, query])
  const selected = useMemo(() => filteredBulletins.find((item) => item.id === selectedId) ?? (bulletins.data ?? []).find((item) => item.id === selectedId) ?? null, [filteredBulletins, bulletins.data, selectedId])

  useEffect(() => {
    if (selected) {
      setForm({
        title: selected.title,
        body: selected.body,
        summary: selected.summary ?? '',
        category: selected.category ?? 'notice',
        audience: selected.audience ?? 'customer',
        severity: selected.severity ?? 'info',
        auto_inject_to_ai: selected.auto_inject_to_ai,
        is_active: selected.is_active,
        market_id: selected.market_id ?? undefined,
        country_code: selected.country_code ?? '',
        channels_csv: selected.channels_csv ?? '',
      })
    } else {
      setForm(emptyForm())
    }
  }, [selected])

  const saveMutation = useMutation({
    mutationFn: async () => {
      const payload = {
        market_id: form.market_id || null,
        country_code: form.country_code || null,
        title: form.title || '',
        body: form.body || '',
        summary: form.summary || null,
        category: form.category || 'notice',
        channels_csv: form.channels_csv || null,
        audience: form.audience || 'customer',
        severity: form.severity || 'info',
        auto_inject_to_ai: Boolean(form.auto_inject_to_ai),
        is_active: Boolean(form.is_active),
      }
      if (selectedId) return api.updateBulletin(selectedId, payload)
      return api.createBulletin(payload)
    },
    onSuccess: async (saved) => {
      setSelectedId(saved.id)
      setToast({ message: selectedId ? '公告已更新' : '公告已创建', tone: 'success' })
      await client.invalidateQueries({ queryKey: ['bulletins'] })
    },
    onError: (err: Error) => setToast({ message: err.message || '保存公告失败', tone: 'danger' }),
  })

  return (
    <AppShell>
      <PageHeader
        eyebrow="通知公告"
        title="公告与回复口径中心"
        description="把影响客服回复的话术、政策、时效通知集中管理，让客服和智能助手引用同一套口径。"
        actions={<div className="button-row"><Button variant="secondary" onClick={() => autoRefresh.setEnabled(!autoRefresh.enabled)}>{autoRefresh.enabled ? '暂停刷新' : '恢复刷新'}</Button>{canEdit ? <Button onClick={() => { setSelectedId(null); setForm(emptyForm()) }}>新建公告</Button> : null}</div>}
      />
      {!canEdit ? <Card className="soft"><CardBody><div className="message" data-role="agent">你当前可以查看公告口径，但不能新增或修改公告；需要调整口径时请联系主管。</div></CardBody></Card> : null}
      <div className="workspace-toolbar">
        <Input placeholder="搜索公告标题、摘要或正文…" value={query} onChange={(e) => setQuery(e.target.value)} />
        <SegmentedControl value={mode} onChange={setMode} options={[
          { label: '全部', value: 'all' },
          { label: '生效中', value: 'active' },
          { label: '已停用', value: 'inactive' },
        ]} />
        <div className="workspace-toolbar-meta">共 {filteredBulletins.length} 条公告</div>
      </div>
      <div className="page-grid split-grid-wide">
        <Card>
          <CardHeader title="公告列表" subtitle="客服当前能看到、能引用、能执行的公告。" />
          <CardBody>
            <div className="list">
              {filteredBulletins.map((b) => (
                <button key={b.id} className={`queue-card ${selectedId === b.id ? 'selected' : ''}`} onClick={() => setSelectedId(b.id)}>
                  <div className="badges">
                    {b.severity ? <Badge tone={severityTone(b.severity)}>{labelize(b.severity)}</Badge> : null}
                    <Badge>{labelize(b.category || 'notice')}</Badge>
                    {b.is_active ? <Badge tone="success">生效中</Badge> : <Badge>已停用</Badge>}
                    {b.auto_inject_to_ai ? <Badge tone="warning">智能助手可引用</Badge> : null}
                  </div>
                  <div className="queue-card-title">{sanitizeDisplayText(b.title)}</div>
                  <div className="queue-card-meta">{sanitizeDisplayText(b.country_code || '全局')} · {formatDateTime(b.updated_at)}</div>
                  <div className="queue-card-meta">{sanitizeDisplayText(b.summary || b.body)}</div>
                </button>
              ))}
              {!filteredBulletins.length ? <EmptyState text="当前筛选条件下没有公告。" /> : null}
            </div>
          </CardBody>
        </Card>

        <Card>
          <CardHeader title={selectedId ? '编辑公告' : '新建公告'} subtitle="写给客服看得懂、拿来就能回的公告内容。" />
          <CardBody>
            <div className="stack">
              <Field label="公告标题">
                <Input value={form.title ?? ''} onChange={(e) => setForm((s) => ({ ...s, title: e.target.value }))} disabled={!canEdit} />
              </Field>
              <Field label="简短摘要" hint="列表页与卡片页优先展示这段话。">
                <Textarea value={form.summary ?? ''} onChange={(e) => setForm((s) => ({ ...s, summary: e.target.value }))} disabled={!canEdit} />
              </Field>
              <Field label="详细内容" hint="给客服的完整口径或处理说明。">
                <Textarea value={form.body ?? ''} onChange={(e) => setForm((s) => ({ ...s, body: e.target.value }))} disabled={!canEdit} />
              </Field>
              <div className="form-grid">
                <Field label="适用市场">
                  <Select value={String(form.market_id ?? '')} onChange={(e) => setForm((s) => ({ ...s, market_id: e.target.value ? Number(e.target.value) : undefined }))} disabled={!canEdit}>
                    <option value="">全局 / 不区分市场</option>
                    {(markets.data ?? []).map((market) => <option key={market.id} value={market.id}>{market.code} · {market.name}</option>)}
                  </Select>
                </Field>
                <Field label="国家代码">
                  <Input value={form.country_code ?? ''} onChange={(e) => setForm((s) => ({ ...s, country_code: e.target.value.toUpperCase() }))} disabled={!canEdit} />
                </Field>
                <Field label="公告类型">
                  <Select value={form.category ?? 'notice'} onChange={(e) => setForm((s) => ({ ...s, category: e.target.value }))} disabled={!canEdit}>
                    <option value="notice">通知</option>
                    <option value="delay">延误</option>
                    <option value="disruption">异常</option>
                    <option value="customs">清关</option>
                  </Select>
                </Field>
                <Field label="适用对象">
                  <Select value={form.audience ?? 'customer'} onChange={(e) => setForm((s) => ({ ...s, audience: e.target.value }))} disabled={!canEdit}>
                    <option value="customer">客户</option>
                    <option value="operator">客服</option>
                    <option value="both">客户与客服</option>
                  </Select>
                </Field>
                <Field label="紧急程度">
                  <Select value={form.severity ?? 'info'} onChange={(e) => setForm((s) => ({ ...s, severity: e.target.value }))} disabled={!canEdit}>
                    <option value="info">普通</option>
                    <option value="warning">提醒</option>
                    <option value="critical">紧急</option>
                  </Select>
                </Field>
                <Field label="适用渠道" hint="多个渠道请用逗号分隔。">
                  <Input value={form.channels_csv ?? ''} onChange={(e) => setForm((s) => ({ ...s, channels_csv: e.target.value }))} placeholder="whatsapp,email" disabled={!canEdit} />
                </Field>
              </div>
              <div className="toggle-row">
                <label><input type="checkbox" checked={Boolean(form.is_active)} onChange={(e) => setForm((s) => ({ ...s, is_active: e.target.checked }))} disabled={!canEdit} /> 生效中</label>
                <label><input type="checkbox" checked={Boolean(form.auto_inject_to_ai)} onChange={(e) => setForm((s) => ({ ...s, auto_inject_to_ai: e.target.checked }))} disabled={!canEdit} /> 允许智能助手引用</label>
              </div>
              <Card className="soft">
                <CardHeader title="预览" subtitle="客服和智能助手实际看到的内容效果。" />
                <CardBody>
                  <div className="badges">
                    <Badge>{labelize(form.category || 'notice')}</Badge>
                    {form.severity ? <Badge tone={severityTone(form.severity)}>{labelize(form.severity)}</Badge> : null}
                    {form.auto_inject_to_ai ? <Badge tone="success">智能助手可引用</Badge> : null}
                  </div>
                  <div className="message" style={{ marginTop: 12 }}>
                    <strong>{sanitizeDisplayText(form.title || '未命名公告')}</strong><br />
                    {sanitizeDisplayText(form.summary || form.body || '请先填写公告内容。')}
                  </div>
                </CardBody>
              </Card>
              <div className="button-row">
                <Button variant="primary" onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending || !canEdit}>
                  {saveMutation.isPending ? '保存中…' : selectedId ? '保存修改' : '创建公告'}
                </Button>
                {canEdit ? <Button onClick={() => { setSelectedId(null); setForm(emptyForm()) }}>重置</Button> : null}
              </div>
            </div>
          </CardBody>
        </Card>
      </div>
      {toast ? <Toast message={toast.message} tone={toast.tone} onClose={() => setToast(null)} /> : null}
    </AppShell>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/bulletins',
  beforeLoad: () => { if (!getToken()) throw redirect({ to: '/login' }) },
  component: BulletinsPage,
})

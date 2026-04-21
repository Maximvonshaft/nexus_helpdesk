import { useEffect, useMemo, useState } from 'react'
import { createRoute, redirect, useNavigate } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import { formatDateTime, labelize, sanitizeDisplayText } from '@/lib/format'
import { useSession } from '@/hooks/useAuth'
import { canManageAIConfig } from '@/lib/access'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { EmptyState } from '@/components/ui/EmptyState'
import { Field, Input, Select, Textarea } from '@/components/ui/Field'
import { PageHeader } from '@/components/ui/PageHeader'
import { SegmentedControl } from '@/components/ui/SegmentedControl'
import { Toast } from '@/components/ui/Toast'
import type { AIConfigResource } from '@/lib/types'

const configTypes = ['persona', 'knowledge', 'sop', 'policy'] as const
const scopeOptions = ['global', 'market', 'team', 'channel', 'case_type'] as const

function emptyForm() {
  return {
    resource_key: '',
    config_type: 'persona',
    name: '',
    description: '',
    scope_type: 'global',
    scope_value: '',
    market_id: undefined as number | undefined,
    is_active: true,
    draft_summary: '',
    draft_content_text: '{\n  "goal": ""\n}',
  }
}

function stringifyDraft(value: unknown) {
  try {
    return JSON.stringify(value ?? {}, null, 2)
  } catch {
    return '{\n  "goal": ""\n}'
  }
}

function AIControlPage() {
  const session = useSession()
  const navigate = useNavigate()
  const client = useQueryClient()
  const permitted = canManageAIConfig(session.data)
  const [type, setType] = useState<typeof configTypes[number]>('persona')
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [toast, setToast] = useState<{ message: string; tone?: 'default' | 'danger' | 'success' } | null>(null)
  const [form, setForm] = useState(emptyForm())

  const resources = useQuery({ queryKey: ['ai-configs', type], queryFn: () => api.aiConfigs(type), enabled: permitted })
  const versions = useQuery({ queryKey: ['ai-config-versions', selectedId], queryFn: () => api.aiConfigVersions(selectedId as number), enabled: permitted && !!selectedId })
  const markets = useQuery({ queryKey: ['markets-ai-config'], queryFn: api.markets, enabled: permitted })
  const published = useQuery({ queryKey: ['published-ai-configs-preview', type], queryFn: () => api.publishedAIConfigs(type), enabled: permitted })

  const selected = useMemo(() => (resources.data ?? []).find((item) => item.id === selectedId) ?? null, [resources.data, selectedId])

  useEffect(() => {
    if (session.data && !permitted) navigate({ to: '/' })
  }, [navigate, permitted, session.data])

  useEffect(() => {
    if (selected) {
      setForm({
        resource_key: selected.resource_key,
        config_type: selected.config_type,
        name: selected.name,
        description: selected.description ?? '',
        scope_type: selected.scope_type,
        scope_value: selected.scope_value ?? '',
        market_id: selected.market_id ?? undefined,
        is_active: selected.is_active,
        draft_summary: selected.draft_summary ?? '',
        draft_content_text: stringifyDraft(selected.draft_content_json),
      })
    } else {
      setForm((prev) => ({ ...emptyForm(), config_type: type }))
    }
  }, [selected, type])

  const saveMutation = useMutation({
    mutationFn: async () => {
      let draftContent
      try {
        draftContent = JSON.parse(form.draft_content_text || '{}')
      } catch {
        throw new Error('草稿内容必须是合法 JSON')
      }
      const payload = {
        resource_key: form.resource_key,
        config_type: form.config_type,
        name: form.name,
        description: form.description || null,
        scope_type: form.scope_type,
        scope_value: form.scope_value || null,
        market_id: form.market_id || null,
        is_active: Boolean(form.is_active),
        draft_summary: form.draft_summary || null,
        draft_content_json: draftContent,
      }
      if (selectedId) return api.updateAIConfig(selectedId, payload)
      return api.createAIConfig(payload)
    },
    onSuccess: async (saved) => {
      setSelectedId(saved.id)
      setToast({ message: selectedId ? 'AI 规则草稿已更新' : 'AI 规则已创建', tone: 'success' })
      await client.invalidateQueries({ queryKey: ['ai-configs'] })
      await client.invalidateQueries({ queryKey: ['published-ai-configs-preview'] })
    },
    onError: (err: Error) => setToast({ message: err.message || '保存失败', tone: 'danger' }),
  })

  const publishMutation = useMutation({
    mutationFn: async () => {
      if (!selectedId) throw new Error('请先保存草稿')
      return api.publishAIConfig(selectedId, 'publish from AI control page')
    },
    onSuccess: async (version) => {
      setToast({ message: `已发布版本 v${version.version}`, tone: 'success' })
      await client.invalidateQueries({ queryKey: ['ai-configs'] })
      await client.invalidateQueries({ queryKey: ['ai-config-versions', selectedId] })
      await client.invalidateQueries({ queryKey: ['published-ai-configs-preview'] })
    },
    onError: (err: Error) => setToast({ message: err.message || '发布失败', tone: 'danger' }),
  })

  const rollbackMutation = useMutation({
    mutationFn: async (version: number) => {
      if (!selectedId) throw new Error('请选择规则')
      return api.rollbackAIConfig(selectedId, version, `rollback to v${version}`)
    },
    onSuccess: async (version) => {
      setToast({ message: `已回滚并重新发布为 v${version.version}`, tone: 'success' })
      await client.invalidateQueries({ queryKey: ['ai-configs'] })
      await client.invalidateQueries({ queryKey: ['ai-config-versions', selectedId] })
      await client.invalidateQueries({ queryKey: ['published-ai-configs-preview'] })
    },
    onError: (err: Error) => setToast({ message: err.message || '回滚失败', tone: 'danger' }),
  })

  return (
    <AppShell>
      <PageHeader
        eyebrow="AI规则"
        title="智能助手规则与知识配置"
        description="把人格、知识、SOP 和执行边界从零散公告里抽出来，形成真正可发布、可回滚的配置层。"
        actions={<div className="button-row"><Button variant="secondary" onClick={() => { setSelectedId(null); setForm((prev) => ({ ...emptyForm(), config_type: type })) }}>新建规则</Button><Button variant="primary" onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending}>{saveMutation.isPending ? '保存中…' : '保存草稿'}</Button><Button onClick={() => publishMutation.mutate()} disabled={publishMutation.isPending || !selectedId}>{publishMutation.isPending ? '发布中…' : '发布当前草稿'}</Button></div>}
      />
      {!permitted ? (
        <Card><CardHeader title="无权限访问" subtitle="只有具备 AI 配置治理权限的账号才可以管理 AI 规则。" /><CardBody><div className="message" data-role="agent">如需调整智能助手回复边界、SOP 或知识内容，请联系主管或管理员。</div></CardBody></Card>
      ) : (
        <>
          <Card className="soft"><CardHeader title="为什么要有这层" subtitle="公告只是知识的一部分；真正的生产系统还需要人格、SOP、执行边界与版本发布。" /><CardBody><div className="guide-grid"><div className="guide-item"><strong>Persona</strong><span>定义 AI 的语气、升级原则和品牌边界。</span></div><div className="guide-item"><strong>Knowledge</strong><span>沉淀公告、FAQ、市场规则与产品规则。</span></div><div className="guide-item"><strong>SOP</strong><span>把不同工单类型的标准动作沉淀为流程。</span></div><div className="guide-item"><strong>Policy</strong><span>约束 AI 哪些动作可自动做，哪些必须人工审批。</span></div></div></CardBody></Card>
          <div className="workspace-toolbar"><SegmentedControl value={type} onChange={(value) => { setType(value as typeof configTypes[number]); setSelectedId(null) }} options={[{ label: '人格', value: 'persona' },{ label: '知识', value: 'knowledge' },{ label: 'SOP', value: 'sop' },{ label: '执行边界', value: 'policy' }]} /><div className="workspace-toolbar-meta">已发布预览 {published.data?.length ?? 0} 项</div></div>
          <div className="page-grid split-grid-wide">
            <Card><CardHeader title="规则列表" subtitle="左侧看不同类型规则，右侧维护草稿并发布。" /><CardBody><div className="list">{(resources.data ?? []).map((item) => (<button key={item.id} className={`queue-card ${selectedId === item.id ? 'selected' : ''}`} onClick={() => setSelectedId(item.id)}><div className="badges"><Badge>{labelize(item.config_type)}</Badge><Badge>{labelize(item.scope_type)}</Badge>{item.published_version > 0 ? <Badge tone="success">已发布 v{item.published_version}</Badge> : <Badge tone="warning">未发布</Badge>}{item.is_active ? <Badge tone="success">启用中</Badge> : <Badge>已停用</Badge>}</div><div className="queue-card-title">{sanitizeDisplayText(item.name)}</div><div className="queue-card-meta">{sanitizeDisplayText(item.resource_key)} · {formatDateTime(item.updated_at)}</div><div className="queue-card-meta">{sanitizeDisplayText(item.draft_summary || item.description || '暂无摘要')}</div></button>))}{!resources.data?.length ? <EmptyState text="当前类型还没有规则，请先新建。" /> : null}</div></CardBody></Card>
            <Card><CardHeader title={selectedId ? '编辑规则草稿' : '新建规则草稿'} subtitle="建议把能独立发布、独立回滚的规则拆开管理。" /><CardBody><div className="stack"><div className="form-grid"><Field label="规则名称"><Input value={form.name} onChange={(e) => setForm((s) => ({ ...s, name: e.target.value }))} /></Field><Field label="规则键名" hint="建议用稳定英文键名，方便后续多租户和版本化。"><Input value={form.resource_key} onChange={(e) => setForm((s) => ({ ...s, resource_key: e.target.value }))} /></Field><Field label="规则类型"><Select value={form.config_type} onChange={(e) => setForm((s) => ({ ...s, config_type: e.target.value }))}>{configTypes.map((item) => <option key={item} value={item}>{labelize(item)}</option>)}</Select></Field><Field label="作用范围"><Select value={form.scope_type} onChange={(e) => setForm((s) => ({ ...s, scope_type: e.target.value }))}>{scopeOptions.map((item) => <option key={item} value={item}>{labelize(item)}</option>)}</Select></Field><Field label="范围值" hint="例如具体 case type、渠道编号或团队编号。"><Input value={form.scope_value} onChange={(e) => setForm((s) => ({ ...s, scope_value: e.target.value }))} /></Field><Field label="适用市场"><Select value={String(form.market_id ?? '')} onChange={(e) => setForm((s) => ({ ...s, market_id: e.target.value ? Number(e.target.value) : undefined }))}><option value="">全局 / 不区分市场</option>{(markets.data ?? []).map((market) => <option key={market.id} value={market.id}>{market.code} · {market.name}</option>)}</Select></Field></div><Field label="业务说明"><Textarea value={form.description} onChange={(e) => setForm((s) => ({ ...s, description: e.target.value }))} /></Field><Field label="草稿摘要" hint="给主管和客服看的业务摘要，而不是给机器看的原始 JSON。"><Textarea value={form.draft_summary} onChange={(e) => setForm((s) => ({ ...s, draft_summary: e.target.value }))} /></Field><Field label="草稿内容(JSON)" hint="当前用 JSON 承载配置内容，为下一步配置控制台继续拆细做准备。"><Textarea value={form.draft_content_text} onChange={(e) => setForm((s) => ({ ...s, draft_content_text: e.target.value }))} rows={16} /></Field><label className="toggle-row"><input type="checkbox" checked={Boolean(form.is_active)} onChange={(e) => setForm((s) => ({ ...s, is_active: e.target.checked }))} /> 当前规则启用</label></div></CardBody></Card>
          </div>
          <div className="page-grid split-grid"><Card><CardHeader title="发布历史" subtitle="回滚时不要靠手改；直接从这里回到某个已发布版本。" /><CardBody><div className="list">{(versions.data ?? []).map((item) => (<div key={item.id} className="list-item"><div className="badges"><Badge tone="success">v{item.version}</Badge></div><div><strong>{sanitizeDisplayText(item.summary || '未填写摘要')}</strong></div><div className="section-subtitle">{formatDateTime(item.published_at)} · {sanitizeDisplayText(item.notes || '')}</div><div className="button-row" style={{ marginTop: 8 }}><Button variant="secondary" onClick={() => setForm((s) => ({ ...s, draft_summary: item.summary || '', draft_content_text: stringifyDraft(item.snapshot_json) }))}>加载到草稿</Button><Button onClick={() => rollbackMutation.mutate(item.version)} disabled={rollbackMutation.isPending || !selectedId}>回滚到这个版本</Button></div></div>))}{!versions.data?.length ? <EmptyState text="当前规则还没有发布历史。" /> : null}</div></CardBody></Card><Card><CardHeader title="已发布预览" subtitle="这里看到的是 lookups 层能拿到的线上生效内容。" /><CardBody><div className="list">{(published.data ?? []).map((item: AIConfigResource) => (<div key={item.id} className="list-item"><div className="badges"><Badge>{labelize(item.config_type)}</Badge><Badge tone="success">v{item.published_version}</Badge></div><div><strong>{sanitizeDisplayText(item.name)}</strong></div><div className="section-subtitle">{sanitizeDisplayText(item.published_summary || item.description || '')}</div></div>))}{!published.data?.length ? <EmptyState text="当前没有已发布的规则。" /> : null}</div></CardBody></Card></div>
        </>
      )}
      {toast ? <Toast message={toast.message} tone={toast.tone} onClose={() => setToast(null)} /> : null}
    </AppShell>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/ai-control',
  beforeLoad: () => { if (!getToken()) throw redirect({ to: '/login' }) },
  component: AIControlPage,
})

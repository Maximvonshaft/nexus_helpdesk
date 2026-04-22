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
import { Toast } from '@/components/ui/Toast'
import type { KnowledgeItem } from '@/lib/types'

const SOURCE_OPTIONS = ['text', 'url', 'file']
const AUDIENCE_OPTIONS = ['customer', 'internal']
const CHANNEL_OPTIONS = ['', 'whatsapp', 'telegram', 'sms', 'email', 'web_chat']

function emptyForm() {
  return {
    item_key: '',
    title: '',
    summary: '',
    status: 'draft',
    source_type: 'text',
    market_id: undefined as number | undefined,
    channel: '',
    audience_scope: 'customer',
    priority: 100,
    starts_at: '',
    ends_at: '',
    source_url: '',
    file_name: '',
    file_storage_key: '',
    mime_type: '',
    file_size: undefined as number | undefined,
    draft_body: '',
  }
}

function KnowledgePage() {
  const session = useSession()
  const navigate = useNavigate()
  const client = useQueryClient()
  const permitted = canManageAIConfig(session.data?.role)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [toast, setToast] = useState<{ message: string; tone?: 'default' | 'danger' | 'success' } | null>(null)
  const [form, setForm] = useState(emptyForm())
  const [previewFilters, setPreviewFilters] = useState({ market_id: '', channel: '', audience_scope: 'customer' })

  const items = useQuery({ queryKey: ['knowledge-items'], queryFn: api.knowledgeItems, enabled: permitted })
  const versions = useQuery({ queryKey: ['knowledge-versions', selectedId], queryFn: () => api.knowledgeVersions(selectedId as number), enabled: permitted && !!selectedId })
  const markets = useQuery({ queryKey: ['markets-knowledge'], queryFn: api.markets, enabled: permitted })

  const preview = useMutation({
    mutationFn: () => api.previewKnowledgeResolution({
      market_id: previewFilters.market_id ? Number(previewFilters.market_id) : undefined,
      channel: previewFilters.channel || undefined,
      audience_scope: previewFilters.audience_scope,
    }),
    onError: (err: Error) => setToast({ message: err.message || '知识预览失败', tone: 'danger' }),
  })

  const selected = useMemo(() => (items.data ?? []).find((item) => item.id === selectedId) ?? null, [items.data, selectedId])

  useEffect(() => {
    if (session.data && !permitted) navigate({ to: '/' })
  }, [navigate, permitted, session.data])

  useEffect(() => {
    if (selected) {
      setForm({
        item_key: selected.item_key,
        title: selected.title,
        summary: selected.summary ?? '',
        status: selected.status,
        source_type: selected.source_type,
        market_id: selected.market_id ?? undefined,
        channel: selected.channel ?? '',
        audience_scope: selected.audience_scope,
        priority: selected.priority,
        starts_at: selected.starts_at ?? '',
        ends_at: selected.ends_at ?? '',
        source_url: selected.source_url ?? '',
        file_name: selected.file_name ?? '',
        file_storage_key: selected.file_storage_key ?? '',
        mime_type: selected.mime_type ?? '',
        file_size: selected.file_size ?? undefined,
        draft_body: selected.draft_body ?? '',
      })
    } else {
      setForm(emptyForm())
    }
  }, [selected])

  const saveMutation = useMutation({
    mutationFn: async () => {
      const payload = {
        item_key: form.item_key,
        title: form.title,
        summary: form.summary || null,
        status: form.status,
        source_type: form.source_type,
        market_id: form.market_id || null,
        channel: form.channel || null,
        audience_scope: form.audience_scope,
        priority: Number(form.priority || 100),
        starts_at: form.starts_at || null,
        ends_at: form.ends_at || null,
        source_url: form.source_url || null,
        file_name: form.file_name || null,
        file_storage_key: form.file_storage_key || null,
        mime_type: form.mime_type || null,
        file_size: form.file_size || null,
        draft_body: form.draft_body || null,
      }
      if (selectedId) return api.updateKnowledgeItem(selectedId, payload)
      return api.createKnowledgeItem(payload)
    },
    onSuccess: async (saved) => {
      setSelectedId(saved.id)
      setToast({ message: selectedId ? '知识条目已更新' : '知识条目已创建', tone: 'success' })
      await client.invalidateQueries({ queryKey: ['knowledge-items'] })
    },
    onError: (err: Error) => setToast({ message: err.message || '保存知识条目失败', tone: 'danger' }),
  })

  const publishMutation = useMutation({
    mutationFn: async () => {
      if (!selectedId) throw new Error('请先保存知识条目')
      return api.publishKnowledgeItem(selectedId, 'publish from knowledge page')
    },
    onSuccess: async (version) => {
      setToast({ message: `知识条目已发布为 v${version.version}`, tone: 'success' })
      await client.invalidateQueries({ queryKey: ['knowledge-items'] })
      await client.invalidateQueries({ queryKey: ['knowledge-versions', selectedId] })
    },
    onError: (err: Error) => setToast({ message: err.message || '发布知识条目失败', tone: 'danger' }),
  })

  const archiveMutation = useMutation({
    mutationFn: async () => {
      if (!selectedId) throw new Error('请选择知识条目')
      return api.archiveKnowledgeItem(selectedId)
    },
    onSuccess: async () => {
      setToast({ message: '知识条目已归档', tone: 'success' })
      await client.invalidateQueries({ queryKey: ['knowledge-items'] })
    },
    onError: (err: Error) => setToast({ message: err.message || '归档失败', tone: 'danger' }),
  })

  const rollbackMutation = useMutation({
    mutationFn: async (versionNum: number) => {
      if (!selectedId) throw new Error('请选择知识条目')
      return api.rollbackKnowledgeItem(selectedId, versionNum, `rollback to v${versionNum}`)
    },
    onSuccess: async (version) => {
      setToast({ message: `已回滚并重新发布为 v${version.version}`, tone: 'success' })
      await client.invalidateQueries({ queryKey: ['knowledge-items'] })
      await client.invalidateQueries({ queryKey: ['knowledge-versions', selectedId] })
    },
    onError: (err: Error) => setToast({ message: err.message || '回滚失败', tone: 'danger' }),
  })

  const uploadMutation = useMutation({
    mutationFn: async (file: File) => api.uploadKnowledgeFile(file),
    onSuccess: (result) => {
      setForm((s) => ({
        ...s,
        source_type: 'file',
        file_name: result.file_name,
        file_storage_key: result.storage_key,
        mime_type: result.mime_type,
        file_size: result.size_bytes,
        draft_body: s.draft_body || result.extracted_text || '',
      }))
      setToast({ message: '文件已上传，已回填到知识条目草稿。', tone: 'success' })
    },
    onError: (err: Error) => setToast({ message: err.message || '上传知识文件失败', tone: 'danger' }),
  })

  return (
    <AppShell>
      <PageHeader
        eyebrow="Knowledge / 云上知识库"
        title="受控知识条目库"
        description="先把文字、URL、文件这三类知识资产纳入可发布、可预览、可审计的控制面；本轮不强行上重型 RAG。"
        actions={<div className="button-row"><Button variant="secondary" onClick={() => { setSelectedId(null); setForm(emptyForm()) }}>新建条目</Button><Button variant="primary" onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending}>{saveMutation.isPending ? '保存中…' : '保存草稿'}</Button><Button onClick={() => publishMutation.mutate()} disabled={publishMutation.isPending || !selectedId}>{publishMutation.isPending ? '发布中…' : '发布条目'}</Button><Button variant="secondary" onClick={() => archiveMutation.mutate()} disabled={archiveMutation.isPending || !selectedId}>归档</Button></div>}
      />
      {!permitted ? (
        <Card><CardHeader title="无权限访问" subtitle="只有具备 AI 配置治理权限的账号才能管理知识库。" /><CardBody><div className="message" data-role="agent">如需调整对客知识口径、FAQ 或内部 SOP，请联系主管或管理员。</div></CardBody></Card>
      ) : (
        <>
          <div className="page-grid split-grid-wide">
            <Card>
              <CardHeader title="知识条目列表" subtitle="published 才会进入生效预览；archived 不再参与命中。" />
              <CardBody>
                <div className="list">
                  {(items.data ?? []).map((item) => (
                    <button key={item.id} className={`queue-card ${selectedId === item.id ? 'selected' : ''}`} onClick={() => setSelectedId(item.id)}>
                      <div className="badges">
                        <Badge>{labelize(item.source_type)}</Badge>
                        <Badge>{labelize(item.audience_scope)}</Badge>
                        <Badge tone={item.status === 'published' ? 'success' : item.status === 'archived' ? 'danger' : 'warning'}>{labelize(item.status)}</Badge>
                        {item.published_version > 0 ? <Badge tone="success">v{item.published_version}</Badge> : null}
                      </div>
                      <div className="queue-card-title">{sanitizeDisplayText(item.title)}</div>
                      <div className="queue-card-meta">市场 {item.market_id ?? 'global'} · 渠道 {sanitizeDisplayText(item.channel || 'all')} · 优先级 {item.priority}</div>
                      <div className="queue-card-meta">{sanitizeDisplayText(item.summary || '暂无摘要')}</div>
                    </button>
                  ))}
                  {!items.data?.length ? <EmptyState text="还没有知识条目，请先新建。" /> : null}
                </div>
              </CardBody>
            </Card>
            <Card>
              <CardHeader title={selectedId ? '编辑知识条目' : '新建知识条目'} subtitle="支持文字录入、URL 来源、文件上传三种输入。文件上传先接现有 storage，不强行假装已经有解析式 RAG。" />
              <CardBody>
                <div className="stack">
                  <div className="form-grid">
                    <Field label="条目标题"><Input value={form.title} onChange={(e) => setForm((s) => ({ ...s, title: e.target.value }))} /></Field>
                    <Field label="条目标识"><Input value={form.item_key} onChange={(e) => setForm((s) => ({ ...s, item_key: e.target.value }))} /></Field>
                    <Field label="来源类型"><Select value={form.source_type} onChange={(e) => setForm((s) => ({ ...s, source_type: e.target.value }))}>{SOURCE_OPTIONS.map((item) => <option key={item} value={item}>{labelize(item)}</option>)}</Select></Field>
                    <Field label="适用市场"><Select value={String(form.market_id ?? '')} onChange={(e) => setForm((s) => ({ ...s, market_id: e.target.value ? Number(e.target.value) : undefined }))}><option value="">全局</option>{(markets.data ?? []).map((market) => <option key={market.id} value={market.id}>{market.code} · {market.name}</option>)}</Select></Field>
                    <Field label="适用渠道"><Select value={form.channel} onChange={(e) => setForm((s) => ({ ...s, channel: e.target.value }))}>{CHANNEL_OPTIONS.map((item) => <option key={item || 'all'} value={item}>{item ? labelize(item) : '全部渠道'}</option>)}</Select></Field>
                    <Field label="Audience"><Select value={form.audience_scope} onChange={(e) => setForm((s) => ({ ...s, audience_scope: e.target.value }))}>{AUDIENCE_OPTIONS.map((item) => <option key={item} value={item}>{labelize(item)}</option>)}</Select></Field>
                    <Field label="优先级"><Input type="number" value={String(form.priority)} onChange={(e) => setForm((s) => ({ ...s, priority: Number(e.target.value) }))} /></Field>
                    <Field label="生效开始"><Input type="datetime-local" value={form.starts_at} onChange={(e) => setForm((s) => ({ ...s, starts_at: e.target.value }))} /></Field>
                    <Field label="生效结束"><Input type="datetime-local" value={form.ends_at} onChange={(e) => setForm((s) => ({ ...s, ends_at: e.target.value }))} /></Field>
                  </div>
                  <Field label="摘要"><Textarea value={form.summary} onChange={(e) => setForm((s) => ({ ...s, summary: e.target.value }))} /></Field>
                  {form.source_type === 'url' ? <Field label="来源 URL"><Input value={form.source_url} onChange={(e) => setForm((s) => ({ ...s, source_url: e.target.value }))} /></Field> : null}
                  {form.source_type === 'file' ? (
                    <div className="stack">
                      <Field label="文件上传">
                        <Input type="file" onChange={(e) => { const file = e.target.files?.[0]; if (file) uploadMutation.mutate(file) }} />
                      </Field>
                      <div className="section-subtitle">当前文件：{sanitizeDisplayText(form.file_name || '未上传')} · key: {sanitizeDisplayText(form.file_storage_key || '—')}</div>
                    </div>
                  ) : null}
                  <Field label="正文 / 归一化基础文本"><Textarea rows={16} value={form.draft_body} onChange={(e) => setForm((s) => ({ ...s, draft_body: e.target.value }))} /></Field>
                </div>
              </CardBody>
            </Card>
          </div>
          <div className="page-grid split-grid">
            <Card>
              <CardHeader title="生效预览" subtitle="给定市场 / 渠道 / audience，看当前会命中哪些知识条目。" />
              <CardBody>
                <div className="stack">
                  <div className="form-grid">
                    <Field label="市场"><Select value={previewFilters.market_id} onChange={(e) => setPreviewFilters((s) => ({ ...s, market_id: e.target.value }))}><option value="">全局</option>{(markets.data ?? []).map((market) => <option key={market.id} value={market.id}>{market.code} · {market.name}</option>)}</Select></Field>
                    <Field label="渠道"><Select value={previewFilters.channel} onChange={(e) => setPreviewFilters((s) => ({ ...s, channel: e.target.value }))}>{CHANNEL_OPTIONS.map((item) => <option key={item || 'all'} value={item}>{item ? labelize(item) : '全部渠道'}</option>)}</Select></Field>
                    <Field label="Audience"><Select value={previewFilters.audience_scope} onChange={(e) => setPreviewFilters((s) => ({ ...s, audience_scope: e.target.value }))}>{AUDIENCE_OPTIONS.map((item) => <option key={item} value={item}>{labelize(item)}</option>)}</Select></Field>
                  </div>
                  <div className="button-row"><Button onClick={() => preview.mutate()} disabled={preview.isPending}>{preview.isPending ? '预览中…' : '执行预览'}</Button></div>
                  <div className="list">
                    {(preview.data?.matched_items ?? []).map((item: KnowledgeItem) => (
                      <div key={item.id} className="list-item">
                        <div className="badges"><Badge>{labelize(item.source_type)}</Badge><Badge tone="success">priority {item.priority}</Badge></div>
                        <div><strong>{sanitizeDisplayText(item.title)}</strong></div>
                        <div className="section-subtitle">{sanitizeDisplayText(item.summary || item.published_body || '')}</div>
                      </div>
                    ))}
                    {preview.data && !preview.data.matched_items.length ? <EmptyState text="当前条件下没有命中的知识条目。" /> : null}
                  </div>
                </div>
              </CardBody>
            </Card>
            <Card>
              <CardHeader title="版本记录" subtitle="条目发布、回滚、归档都必须留痕。" />
              <CardBody>
                <div className="list">
                  {(versions.data ?? []).map((item) => (
                    <div key={item.id} className="list-item">
                      <div className="badges"><Badge tone="success">v{item.version}</Badge></div>
                      <div><strong>{sanitizeDisplayText(item.summary || '未填写摘要')}</strong></div>
                      <div className="section-subtitle">{formatDateTime(item.published_at)} · {sanitizeDisplayText(item.notes || '')}</div>
                      <div className="button-row" style={{ marginTop: 8 }}><Button onClick={() => rollbackMutation.mutate(item.version)} disabled={rollbackMutation.isPending || !selectedId}>回滚到这个版本</Button></div>
                    </div>
                  ))}
                  {!versions.data?.length ? <EmptyState text="当前条目还没有发布历史。" /> : null}
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
  path: '/knowledge',
  beforeLoad: () => { if (!getToken()) throw redirect({ to: '/login' }) },
  component: KnowledgePage,
})

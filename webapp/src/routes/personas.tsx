import { useEffect, useMemo, useState } from 'react'
import { createRoute, redirect, useNavigate } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import type { PersonaProfile } from '@/lib/types'
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

const CHANNEL_OPTIONS = ['', 'whatsapp', 'telegram', 'sms', 'email', 'web_chat']
const LANGUAGE_OPTIONS = ['', 'en', 'de', 'fr', 'it', 'zh', 'tl']

function emptyForm() {
  return {
    profile_key: '',
    name: '',
    description: '',
    market_id: undefined as number | undefined,
    channel: '',
    language: '',
    is_active: true,
    draft_summary: '',
    draft_content_text: '{\n  "tone": "professional",\n  "guardrails": [],\n  "reply_style": ""\n}',
  }
}

function stringifyContent(value: unknown) {
  try {
    return JSON.stringify(value ?? {}, null, 2)
  } catch {
    return '{}'
  }
}

function PersonasPage() {
  const session = useSession()
  const navigate = useNavigate()
  const client = useQueryClient()
  const permitted = canManageAIConfig(session.data?.role)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [toast, setToast] = useState<{ message: string; tone?: 'default' | 'danger' | 'success' } | null>(null)
  const [form, setForm] = useState(emptyForm())
  const [previewInput, setPreviewInput] = useState({ market_id: '', channel: '', language: '', user_message: 'Customer asks where the parcel is and sounds upset.' })

  const personas = useQuery({ queryKey: ['persona-profiles'], queryFn: api.personaProfiles, enabled: permitted })
  const versions = useQuery({ queryKey: ['persona-versions', selectedId], queryFn: () => api.personaVersions(selectedId as number), enabled: permitted && !!selectedId })
  const markets = useQuery({ queryKey: ['markets-personas'], queryFn: api.markets, enabled: permitted })
  const preview = useMutation({
    mutationFn: () => api.previewPersonaResolution({
      profile_id: selectedId || undefined,
      use_draft: true,
      market_id: previewInput.market_id ? Number(previewInput.market_id) : undefined,
      channel: previewInput.channel || undefined,
      language: previewInput.language || undefined,
      user_message: previewInput.user_message,
    }),
    onError: (err: Error) => setToast({ message: err.message || '人格预览失败', tone: 'danger' }),
  })

  const selected = useMemo(() => (personas.data ?? []).find((item) => item.id === selectedId) ?? null, [personas.data, selectedId])

  useEffect(() => {
    if (session.data && !permitted) navigate({ to: '/' })
  }, [navigate, permitted, session.data])

  useEffect(() => {
    if (selected) {
      setForm({
        profile_key: selected.profile_key,
        name: selected.name,
        description: selected.description ?? '',
        market_id: selected.market_id ?? undefined,
        channel: selected.channel ?? '',
        language: selected.language ?? '',
        is_active: selected.is_active,
        draft_summary: selected.draft_summary ?? '',
        draft_content_text: stringifyContent(selected.draft_content_json),
      })
    } else {
      setForm(emptyForm())
    }
  }, [selected])

  const saveMutation = useMutation({
    mutationFn: async () => {
      let draftContent
      try {
        draftContent = JSON.parse(form.draft_content_text || '{}')
      } catch {
        throw new Error('人格草稿内容必须是合法 JSON')
      }
      const payload = {
        profile_key: form.profile_key,
        name: form.name,
        description: form.description || null,
        market_id: form.market_id || null,
        channel: form.channel || null,
        language: form.language || null,
        is_active: Boolean(form.is_active),
        draft_summary: form.draft_summary || null,
        draft_content_json: draftContent,
      }
      if (selectedId) return api.updatePersonaProfile(selectedId, payload)
      return api.createPersonaProfile(payload)
    },
    onSuccess: async (saved) => {
      setSelectedId(saved.id)
      setToast({ message: selectedId ? '人格草稿已更新' : '人格草稿已创建', tone: 'success' })
      await client.invalidateQueries({ queryKey: ['persona-profiles'] })
    },
    onError: (err: Error) => setToast({ message: err.message || '保存人格草稿失败', tone: 'danger' }),
  })

  const publishMutation = useMutation({
    mutationFn: async () => {
      if (!selectedId) throw new Error('请先保存人格草稿')
      return api.publishPersonaProfile(selectedId, 'publish from personas page')
    },
    onSuccess: async (version) => {
      setToast({ message: `人格已发布为 v${version.version}`, tone: 'success' })
      await client.invalidateQueries({ queryKey: ['persona-profiles'] })
      await client.invalidateQueries({ queryKey: ['persona-versions', selectedId] })
    },
    onError: (err: Error) => setToast({ message: err.message || '发布人格失败', tone: 'danger' }),
  })

  const rollbackMutation = useMutation({
    mutationFn: async (versionNum: number) => {
      if (!selectedId) throw new Error('请选择人格')
      return api.rollbackPersonaProfile(selectedId, versionNum, `rollback to v${versionNum}`)
    },
    onSuccess: async (version) => {
      setToast({ message: `已回滚并重新发布为 v${version.version}`, tone: 'success' })
      await client.invalidateQueries({ queryKey: ['persona-profiles'] })
      await client.invalidateQueries({ queryKey: ['persona-versions', selectedId] })
    },
    onError: (err: Error) => setToast({ message: err.message || '回滚人格失败', tone: 'danger' }),
  })

  return (
    <AppShell>
      <PageHeader
        eyebrow="Persona / 客服人格"
        title="客服人格配置中心"
        description="把隐含在 AI 规则里的语气、边界、升级原则独立出来，形成真正可版本化、可发布、可回滚的人格层。"
        actions={<div className="button-row"><Button variant="secondary" onClick={() => { setSelectedId(null); setForm(emptyForm()) }}>新建人格</Button><Button variant="primary" onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending}>{saveMutation.isPending ? '保存中…' : '保存草稿'}</Button><Button onClick={() => publishMutation.mutate()} disabled={publishMutation.isPending || !selectedId}>{publishMutation.isPending ? '发布中…' : '发布人格'}</Button></div>}
      />
      {!permitted ? (
        <Card><CardHeader title="无权限访问" subtitle="只有具备 AI 配置治理权限的账号才能管理人格。" /><CardBody><div className="message" data-role="agent">如需调整 AI 的语气、升级口径或边界，请联系主管或管理员。</div></CardBody></Card>
      ) : (
        <>
          <div className="page-grid split-grid-wide">
            <Card>
              <CardHeader title="人格列表" subtitle="按市场 / 渠道 / 语言绑定。匹配规则越具体，优先级越高。" />
              <CardBody>
                <div className="list">
                  {(personas.data ?? []).map((item) => (
                    <button key={item.id} className={`queue-card ${selectedId === item.id ? 'selected' : ''}`} onClick={() => setSelectedId(item.id)}>
                      <div className="badges">
                        <Badge>{item.channel ? labelize(item.channel) : '全渠道'}</Badge>
                        <Badge>{item.language ? item.language.toUpperCase() : '全部语言'}</Badge>
                        {item.published_version > 0 ? <Badge tone="success">已发布 v{item.published_version}</Badge> : <Badge tone="warning">未发布</Badge>}
                        {item.is_active ? <Badge tone="success">启用中</Badge> : <Badge>已停用</Badge>}
                      </div>
                      <div className="queue-card-title">{sanitizeDisplayText(item.name)}</div>
                      <div className="queue-card-meta">{sanitizeDisplayText(item.profile_key)} · 市场 {item.market_id ?? 'global'}</div>
                      <div className="queue-card-meta">{sanitizeDisplayText(item.draft_summary || item.description || '暂无摘要')}</div>
                    </button>
                  ))}
                  {!personas.data?.length ? <EmptyState text="还没有人格配置，请先新建。" /> : null}
                </div>
              </CardBody>
            </Card>
            <Card>
              <CardHeader title={selectedId ? '编辑人格草稿' : '新建人格草稿'} subtitle="本轮最小绑定维度：market / channel / language。草稿与发布分离，避免直接改线上人格。" />
              <CardBody>
                <div className="stack">
                  <div className="form-grid">
                    <Field label="人格名称"><Input value={form.name} onChange={(e) => setForm((s) => ({ ...s, name: e.target.value }))} /></Field>
                    <Field label="人格键名"><Input value={form.profile_key} onChange={(e) => setForm((s) => ({ ...s, profile_key: e.target.value }))} /></Field>
                    <Field label="绑定市场"><Select value={String(form.market_id ?? '')} onChange={(e) => setForm((s) => ({ ...s, market_id: e.target.value ? Number(e.target.value) : undefined }))}><option value="">全局</option>{(markets.data ?? []).map((market) => <option key={market.id} value={market.id}>{market.code} · {market.name}</option>)}</Select></Field>
                    <Field label="绑定渠道"><Select value={form.channel} onChange={(e) => setForm((s) => ({ ...s, channel: e.target.value }))}>{CHANNEL_OPTIONS.map((item) => <option key={item || 'all'} value={item}>{item ? labelize(item) : '全部渠道'}</option>)}</Select></Field>
                    <Field label="绑定语言"><Select value={form.language} onChange={(e) => setForm((s) => ({ ...s, language: e.target.value }))}>{LANGUAGE_OPTIONS.map((item) => <option key={item || 'all'} value={item}>{item ? item.toUpperCase() : '全部语言'}</option>)}</Select></Field>
                  </div>
                  <Field label="业务说明"><Textarea value={form.description} onChange={(e) => setForm((s) => ({ ...s, description: e.target.value }))} /></Field>
                  <Field label="草稿摘要"><Textarea value={form.draft_summary} onChange={(e) => setForm((s) => ({ ...s, draft_summary: e.target.value }))} /></Field>
                  <Field label="人格草稿(JSON)"><Textarea rows={16} value={form.draft_content_text} onChange={(e) => setForm((s) => ({ ...s, draft_content_text: e.target.value }))} /></Field>
                  <label className="toggle-row"><input type="checkbox" checked={Boolean(form.is_active)} onChange={(e) => setForm((s) => ({ ...s, is_active: e.target.checked }))} /> 当前人格启用</label>
                </div>
              </CardBody>
            </Card>
          </div>
          <div className="page-grid split-grid">
            <Card>
              <CardHeader title="预览拼装结果" subtitle="输入一条用户消息，查看当前草稿或解析后的人格配置会如何生效。" />
              <CardBody>
                <div className="stack">
                  <div className="form-grid">
                    <Field label="市场"><Select value={previewInput.market_id} onChange={(e) => setPreviewInput((s) => ({ ...s, market_id: e.target.value }))}><option value="">全局</option>{(markets.data ?? []).map((market) => <option key={market.id} value={market.id}>{market.code} · {market.name}</option>)}</Select></Field>
                    <Field label="渠道"><Select value={previewInput.channel} onChange={(e) => setPreviewInput((s) => ({ ...s, channel: e.target.value }))}>{CHANNEL_OPTIONS.map((item) => <option key={item || 'all'} value={item}>{item ? labelize(item) : '全部渠道'}</option>)}</Select></Field>
                    <Field label="语言"><Select value={previewInput.language} onChange={(e) => setPreviewInput((s) => ({ ...s, language: e.target.value }))}>{LANGUAGE_OPTIONS.map((item) => <option key={item || 'all'} value={item}>{item ? item.toUpperCase() : '全部语言'}</option>)}</Select></Field>
                  </div>
                  <Field label="用户消息"><Textarea value={previewInput.user_message} onChange={(e) => setPreviewInput((s) => ({ ...s, user_message: e.target.value }))} /></Field>
                  <div className="button-row"><Button onClick={() => preview.mutate()} disabled={preview.isPending}>{preview.isPending ? '预览中…' : '执行预览'}</Button></div>
                  {preview.data ? <pre className="code-block">{JSON.stringify(preview.data.preview_json, null, 2)}</pre> : <EmptyState text="还没有预览结果。" />}
                </div>
              </CardBody>
            </Card>
            <Card>
              <CardHeader title="版本记录" subtitle="发布和回滚必须落在版本上，不靠人工复制粘贴。" />
              <CardBody>
                <div className="list">
                  {(versions.data ?? []).map((item) => (
                    <div key={item.id} className="list-item">
                      <div className="badges"><Badge tone="success">v{item.version}</Badge></div>
                      <div><strong>{sanitizeDisplayText(item.summary || '未填写摘要')}</strong></div>
                      <div className="section-subtitle">{formatDateTime(item.published_at)} · {sanitizeDisplayText(item.notes || '')}</div>
                      <div className="button-row" style={{ marginTop: 8 }}>
                        <Button onClick={() => rollbackMutation.mutate(item.version)} disabled={rollbackMutation.isPending || !selectedId}>回滚到这个版本</Button>
                      </div>
                    </div>
                  ))}
                  {!versions.data?.length ? <EmptyState text="当前人格还没有发布历史。" /> : null}
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
  path: '/personas',
  beforeLoad: () => { if (!getToken()) throw redirect({ to: '/login' }) },
  component: PersonasPage,
})

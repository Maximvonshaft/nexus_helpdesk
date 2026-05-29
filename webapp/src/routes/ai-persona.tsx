import { useEffect, useMemo, useState } from 'react'
import { createRoute, redirect } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { EmptyState } from '@/components/ui/EmptyState'
import { ErrorSummary } from '@/components/ui/ErrorSummary'
import { Field, Input, Select, Textarea } from '@/components/ui/Field'
import { GuidedWorkflow } from '@/components/ui/GuidedWorkflow'
import { MetricCard } from '@/components/ui/MetricCard'
import { PageHeader } from '@/components/ui/PageHeader'
import { TechnicalDetails } from '@/components/ui/TechnicalDetails'
import { Toast } from '@/components/ui/Toast'
import { RequireCapability } from '@/components/security/RequireCapability'
import { useSession } from '@/hooks/useAuth'
import { formatDateTime, sanitizeDisplayText } from '@/lib/format'
import { canAccess, CAPABILITIES, routeAccess } from '@/lib/rbac'
import type { PersonaProfile } from '@/lib/types'

const channelOptions = ['website', 'webchat', 'whatsapp', 'email'] as const

const personaTemplate = {
  summary: '专业、简洁、先确认事实再给承诺；涉及赔付、投诉升级或账号风险时转人工。',
  content: {
    brand_name: '',
    assistant_name: '在线客服助手',
    role_label: 'AI 客服',
    identity_statement: '我是在线客服助手，可以协助处理常见客户服务问题。',
    identity_answer_rule: '客户询问身份时，只使用已发布 Persona 的品牌、助手名称和能力范围回答，不提及内部平台或运行时名称。',
    capabilities: ['回答常见客服问题', '协助收集必要信息', '需要人工处理时转接客服'],
    disallowed_identity_claims: [],
    handoff_boundary: '涉及赔付、投诉升级、账号风险或缺少事实证据时转人工。',
    tone: 'professional_concise',
    guardrails: ['No parcel status without trusted tracking evidence', 'Escalate compensation and legal requests'],
    escalation: 'handoff_for_high_risk_or_missing_facts',
  },
}

type PersonaForm = ReturnType<typeof emptyPersonaForm>
type ConfirmAction =
  | { kind: 'publish'; title: string; description: string; consequence: string }
  | { kind: 'disable'; title: string; description: string; consequence: string }
  | { kind: 'rollback'; version: number; title: string; description: string; consequence: string }

function emptyPersonaForm() {
  return {
    profile_key: '',
    name: '',
    description: '',
    channel: 'website',
    language: '',
    is_active: true,
    draft_summary: personaTemplate.summary,
    draft_content_text: JSON.stringify(personaTemplate.content, null, 2),
  }
}

function stringifyDraft(value: unknown) {
  try {
    return JSON.stringify(value ?? {}, null, 2)
  } catch {
    return '{}'
  }
}

function parseDraftObject(value: string): Record<string, unknown> {
  try {
    const parsed = JSON.parse(value || '{}')
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed as Record<string, unknown> : {}
  } catch {
    return {}
  }
}

function parseLines(value: string) {
  return value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean)
}

function draftStringValue(draft: Record<string, unknown>, key: string) {
  const value = draft[key]
  return typeof value === 'string' ? value : ''
}

function draftListText(draft: Record<string, unknown>, key: string) {
  const value = draft[key]
  return Array.isArray(value) ? value.map((item) => String(item)).join('\n') : ''
}

function setDraftField(raw: string, key: string, value: string, asList = false) {
  const parsed = parseDraftObject(raw)
  parsed[key] = asList ? parseLines(value) : value
  return JSON.stringify(parsed, null, 2)
}

function formFromPersona(persona: PersonaProfile | null): PersonaForm {
  if (!persona) return emptyPersonaForm()
  return {
    profile_key: persona.profile_key,
    name: persona.name,
    description: persona.description ?? '',
    channel: persona.channel ?? 'website',
    language: persona.language ?? '',
    is_active: persona.is_active,
    draft_summary: persona.draft_summary ?? persona.published_summary ?? '',
    draft_content_text: stringifyDraft(persona.draft_content_json ?? persona.published_content_json ?? personaTemplate.content),
  }
}

function previewIdentityAnswer(draft: Record<string, unknown>) {
  const assistant = draftStringValue(draft, 'assistant_name') || draftStringValue(draft, 'role_label') || '在线客服助手'
  const brand = draftStringValue(draft, 'brand_name')
  const identity = draftStringValue(draft, 'identity_statement')
  const capabilities = draftListText(draft, 'capabilities').split('\n').filter(Boolean).slice(0, 3)
  return [
    brand ? `我是 ${brand} 的 ${assistant}。` : `我是 ${assistant}。`,
    identity,
    capabilities.length ? `我可以协助：${capabilities.join('、')}。` : '',
  ].filter(Boolean).join(' ')
}

function profileSubtitle(persona: PersonaProfile) {
  return `${persona.profile_key} · ${persona.channel || 'global'} · ${persona.language || 'all languages'} · ${formatDateTime(persona.updated_at)}`
}

function PersonaBuilderContent() {
  const session = useSession()
  const client = useQueryClient()
  const canManage = canAccess(session.data, { allOf: [CAPABILITIES.aiConfigManage] })
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [form, setForm] = useState(emptyPersonaForm())
  const [simulation, setSimulation] = useState({ market_id: '', channel: 'website', language: '' })
  const [toast, setToast] = useState<{ message: string; tone?: 'default' | 'danger' | 'success' } | null>(null)
  const [confirmAction, setConfirmAction] = useState<ConfirmAction | null>(null)

  const personas = useQuery({ queryKey: ['persona-builder-profiles'], queryFn: () => api.personaProfiles(), enabled: !!session.data })
  const markets = useQuery({ queryKey: ['persona-builder-markets'], queryFn: api.markets, enabled: !!session.data })
  const selectedPersona = useQuery({ queryKey: ['persona-builder-profile', selectedId], queryFn: () => api.personaProfile(selectedId as number), enabled: !!selectedId })
  const resolvePreview = useMutation({
    mutationFn: () => api.personaResolvePreview({
      market_id: simulation.market_id ? Number(simulation.market_id) : null,
      channel: simulation.channel || null,
      language: simulation.language.trim() || null,
    }),
    onError: (err: Error) => setToast({ message: err.message || 'Persona 模拟失败', tone: 'danger' }),
  })

  const rows = personas.data?.profiles ?? []
  const selected = selectedPersona.data ?? null
  const draft = useMemo(() => parseDraftObject(form.draft_content_text), [form.draft_content_text])
  const jsonError = useMemo(() => {
    try {
      JSON.parse(form.draft_content_text || '{}')
      return ''
    } catch (err) {
      return err instanceof Error ? err.message : 'JSON 格式无效'
    }
  }, [form.draft_content_text])

  useEffect(() => {
    setForm(formFromPersona(selected))
  }, [selected])

  async function invalidatePersona(id?: number | null) {
    await client.invalidateQueries({ queryKey: ['persona-builder-profiles'] })
    await client.invalidateQueries({ queryKey: ['persona-profiles'] })
    if (id) {
      await client.invalidateQueries({ queryKey: ['persona-builder-profile', id] })
      await client.invalidateQueries({ queryKey: ['persona-profile', id] })
    }
  }

  const savePersona = useMutation({
    mutationFn: async () => {
      const payload = {
        profile_key: form.profile_key.trim(),
        name: form.name.trim(),
        description: form.description.trim() || null,
        channel: form.channel || null,
        language: form.language.trim() || null,
        is_active: form.is_active,
        draft_summary: form.draft_summary.trim() || null,
        draft_content_json: JSON.parse(form.draft_content_text || '{}'),
      }
      if (selectedId) {
        const { profile_key: _profileKey, ...updatePayload } = payload
        return api.updatePersonaProfile(selectedId, updatePayload)
      }
      return api.createPersonaProfile(payload)
    },
    onSuccess: async (saved) => {
      setSelectedId(saved.id)
      setToast({ message: 'Persona 草稿已保存', tone: 'success' })
      await invalidatePersona(saved.id)
    },
    onError: (err: Error) => setToast({ message: err.message || '保存 Persona 失败', tone: 'danger' }),
  })

  const publishPersona = useMutation({
    mutationFn: async () => {
      if (!selectedId) throw new Error('请先保存 Persona 草稿')
      return api.publishPersonaProfile(selectedId, 'publish from AI Persona Builder')
    },
    onSuccess: async (version) => {
      setToast({ message: `Persona 已发布 v${version.version}`, tone: 'success' })
      await invalidatePersona(selectedId)
    },
    onError: (err: Error) => setToast({ message: err.message || '发布 Persona 失败', tone: 'danger' }),
  })

  const disablePersona = useMutation({
    mutationFn: async () => {
      if (!selectedId) throw new Error('请选择 Persona')
      return api.updatePersonaProfile(selectedId, { is_active: false })
    },
    onSuccess: async () => {
      setToast({ message: 'Persona 已停用', tone: 'success' })
      await invalidatePersona(selectedId)
    },
    onError: (err: Error) => setToast({ message: err.message || '停用 Persona 失败', tone: 'danger' }),
  })

  const rollbackPersona = useMutation({
    mutationFn: async (version: number) => {
      if (!selectedId) throw new Error('请选择 Persona')
      return api.rollbackPersonaProfile(selectedId, version, `rollback from AI Persona Builder to v${version}`)
    },
    onSuccess: async (version) => {
      setToast({ message: `Persona 已回滚并发布为 v${version.version}`, tone: 'success' })
      await invalidatePersona(selectedId)
    },
    onError: (err: Error) => setToast({ message: err.message || '回滚 Persona 失败', tone: 'danger' }),
  })

  function updateDraftField(key: string, value: string, asList = false) {
    setForm((current) => ({ ...current, draft_content_text: setDraftField(current.draft_content_text, key, value, asList) }))
  }

  function runConfirmedAction() {
    const action = confirmAction
    setConfirmAction(null)
    if (!action) return
    if (action.kind === 'publish') publishPersona.mutate()
    if (action.kind === 'disable') disablePersona.mutate()
    if (action.kind === 'rollback') rollbackPersona.mutate(action.version)
  }

  const selectedPublished = selected?.published_version ? selected.published_version > 0 : false
  const matchedProfile = resolvePreview.data?.profile ?? null

  return (
    <>
      <PageHeader
        eyebrow="AI Persona Builder"
        title="AI Persona Builder / 人格配置与发布"
        description="独立维护 Persona 的草稿、身份声明、匹配模拟、发布与回滚证据。"
        actions={<div className="button-row"><Badge tone={canManage ? 'success' : 'warning'}>{canManage ? '可发布' : '只读模拟'}</Badge><Button variant="secondary" onClick={() => { setSelectedId(null); setForm(emptyPersonaForm()) }} disabled={!canManage}>新建 Persona</Button><Button variant="primary" onClick={() => savePersona.mutate()} disabled={!canManage || savePersona.isPending || !!jsonError}>{savePersona.isPending ? '保存中...' : '保存草稿'}</Button></div>}
      />

      <div className="metrics-grid">
        <MetricCard label="Persona 总数" value={rows.length} />
        <MetricCard label="已发布" value={rows.filter((item) => item.published_version > 0).length} />
        <MetricCard label="启用中" value={rows.filter((item) => item.is_active).length} />
        <MetricCard label="当前版本" value={selected?.published_version ? `v${selected.published_version}` : '—'} hint={selectedPublished ? formatDateTime(selected?.published_at) : '未发布'} />
      </div>

      <Card className="soft">
        <CardBody>
          <GuidedWorkflow steps={[
            { title: '编辑人格', description: '品牌名、助手名、身份声明和能力边界。', status: form.name ? 'active' : 'todo' },
            { title: '保存草稿', description: '写入 PersonaProfile draft_content_json。', status: selectedId ? 'done' : 'todo' },
            { title: '模拟匹配', description: '调用 resolve-preview 验证市场、渠道和语言命中。', status: resolvePreview.data ? 'done' : 'todo' },
            { title: '发布版本', description: '生成 PersonaProfileVersion 和运行时可读快照。', status: selectedPublished ? 'done' : 'todo' },
            { title: '回滚证据', description: '历史版本可复制为新版本发布。', status: selected?.versions?.length ? 'done' : 'todo' },
          ]} />
        </CardBody>
      </Card>

      <div className="page-grid split-grid-wide">
        <Card>
          <CardHeader title="Persona Profiles" subtitle="列表来自真实 `/api/persona-profiles`。" />
          <CardBody>
            <div className="list">
              {rows.map((item) => (
                <button key={item.id} className={`queue-card ${selectedId === item.id ? 'selected' : ''}`} onClick={() => setSelectedId(item.id)}>
                  <div className="badges">
                    <Badge>{sanitizeDisplayText(item.channel || 'global')}</Badge>
                    <Badge>{sanitizeDisplayText(item.language || 'all languages')}</Badge>
                    {item.is_active ? <Badge tone="success">启用</Badge> : <Badge>停用</Badge>}
                    {item.published_version > 0 ? <Badge tone="success">v{item.published_version}</Badge> : <Badge tone="warning">未发布</Badge>}
                  </div>
                  <div className="queue-card-title">{sanitizeDisplayText(item.name)}</div>
                  <div className="queue-card-meta">{profileSubtitle(item)}</div>
                  <div className="queue-card-meta">{sanitizeDisplayText(item.draft_summary || item.published_summary || item.description || '暂无摘要')}</div>
                </button>
              ))}
              {!rows.length ? <EmptyState title="还没有 Persona" description="先创建默认 WebChat Persona，再发布给运行时使用。" reason="未发布的 Persona 不会影响客户回复。" /> : null}
            </div>
          </CardBody>
        </Card>

        <Card>
          <CardHeader title={selectedId ? '编辑 Persona 草稿' : '新建 Persona'} subtitle="写动作由后端 `ai_config.manage` 再次校验。" />
          <CardBody>
            <div className="stack">
              {!canManage ? <div className="message" data-role="agent">当前账号仅可查看和模拟 Persona；保存、发布、停用、回滚需要 `ai_config.manage`。</div> : null}
              {jsonError ? <ErrorSummary title="高级 JSON 暂时不能保存" errors={[`JSON 格式无效：${jsonError}`]} /> : null}
              <div className="form-grid">
                <Field label="Profile Key" required example="default.website"><Input value={form.profile_key} disabled={!!selectedId} onChange={(event) => setForm((current) => ({ ...current, profile_key: event.target.value }))} /></Field>
                <Field label="名称" required><Input value={form.name} onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))} /></Field>
                <Field label="渠道"><Select value={form.channel} onChange={(event) => setForm((current) => ({ ...current, channel: event.target.value }))}>{channelOptions.map((item) => <option key={item} value={item}>{item}</option>)}</Select></Field>
                <Field label="语言" hint="留空表示所有语言。"><Input value={form.language} placeholder="所有语言" onChange={(event) => setForm((current) => ({ ...current, language: event.target.value }))} /></Field>
              </div>
              <Field label="业务说明"><Textarea rows={3} value={form.description} onChange={(event) => setForm((current) => ({ ...current, description: event.target.value }))} /></Field>
              <Field label="发布摘要" required><Textarea rows={3} value={form.draft_summary} onChange={(event) => setForm((current) => ({ ...current, draft_summary: event.target.value }))} /></Field>
              <div className="form-grid">
                <Field label="Customer-facing brand name"><Input value={draftStringValue(draft, 'brand_name')} onChange={(event) => updateDraftField('brand_name', event.target.value)} /></Field>
                <Field label="Assistant display name"><Input value={draftStringValue(draft, 'assistant_name')} onChange={(event) => updateDraftField('assistant_name', event.target.value)} /></Field>
                <Field label="Role label"><Input value={draftStringValue(draft, 'role_label')} onChange={(event) => updateDraftField('role_label', event.target.value)} /></Field>
              </div>
              <Field label="Identity statement"><Textarea rows={3} value={draftStringValue(draft, 'identity_statement')} onChange={(event) => updateDraftField('identity_statement', event.target.value)} /></Field>
              <Field label="Identity answer rule"><Textarea rows={3} value={draftStringValue(draft, 'identity_answer_rule')} onChange={(event) => updateDraftField('identity_answer_rule', event.target.value)} /></Field>
              <div className="form-grid">
                <Field label="Capabilities" hint="每行一项"><Textarea rows={4} value={draftListText(draft, 'capabilities')} onChange={(event) => updateDraftField('capabilities', event.target.value, true)} /></Field>
                <Field label="Disallowed identity claims" hint="每行一项"><Textarea rows={4} value={draftListText(draft, 'disallowed_identity_claims')} onChange={(event) => updateDraftField('disallowed_identity_claims', event.target.value, true)} /></Field>
              </div>
              <Field label="Handoff boundary"><Textarea rows={3} value={draftStringValue(draft, 'handoff_boundary')} onChange={(event) => updateDraftField('handoff_boundary', event.target.value)} /></Field>
              <TechnicalDetails title="高级 JSON 配置" summary="仅管理员排查或批量迁移时编辑">
                <Field label="草稿内容 JSON" error={jsonError || undefined}><Textarea rows={12} value={form.draft_content_text} onChange={(event) => setForm((current) => ({ ...current, draft_content_text: event.target.value }))} /></Field>
              </TechnicalDetails>
              <label className="toggle-row"><input type="checkbox" checked={form.is_active} onChange={(event) => setForm((current) => ({ ...current, is_active: event.target.checked }))} /> 当前 Persona 启用</label>
              <div className="button-row">
                <Button variant="primary" onClick={() => savePersona.mutate()} disabled={!canManage || savePersona.isPending || !!jsonError}>保存草稿</Button>
                <Button onClick={() => setConfirmAction({ kind: 'publish', title: '发布当前 Persona？', description: '发布后，匹配渠道的运行时可以读取这个 Persona。', consequence: '请确认语气、升级规则和事实边界已经检查。' })} disabled={!canManage || !selectedId || publishPersona.isPending || !!jsonError}>发布</Button>
                <Button variant="danger" onClick={() => setConfirmAction({ kind: 'disable', title: '停用当前 Persona？', description: '停用后运行时不会再选择这个 Persona。', consequence: '如果没有其他匹配 Persona，助手将只使用基础运行时规则。' })} disabled={!canManage || !selectedId || !selected?.is_active}>停用</Button>
              </div>
            </div>
          </CardBody>
        </Card>
      </div>

      <div className="page-grid split-grid">
        <Card>
          <CardHeader title="Simulation / Resolve Preview" subtitle="调用真实 `/api/persona-profiles/resolve-preview`。" />
          <CardBody>
            <div className="stack">
              <div className="form-grid">
                <Field label="市场"><Select value={simulation.market_id} onChange={(event) => setSimulation((current) => ({ ...current, market_id: event.target.value }))}><option value="">全部市场</option>{(markets.data ?? []).map((market) => <option key={market.id} value={market.id}>{market.code} · {market.name}</option>)}</Select></Field>
                <Field label="渠道"><Select value={simulation.channel} onChange={(event) => setSimulation((current) => ({ ...current, channel: event.target.value }))}>{channelOptions.map((item) => <option key={item} value={item}>{item}</option>)}</Select></Field>
                <Field label="语言"><Input value={simulation.language} placeholder="例如 en / zh" onChange={(event) => setSimulation((current) => ({ ...current, language: event.target.value }))} /></Field>
              </div>
              <div className="button-row"><Button onClick={() => resolvePreview.mutate()} disabled={resolvePreview.isPending}>{resolvePreview.isPending ? '模拟中...' : '模拟命中'}</Button></div>
              <div className="list-item">
                <div className="badges">
                  <Badge tone={matchedProfile ? 'success' : 'warning'}>{matchedProfile ? 'matched' : 'no match'}</Badge>
                  {resolvePreview.data ? <Badge>rank {resolvePreview.data.match_rank ?? '—'}</Badge> : null}
                </div>
                <strong>{matchedProfile ? sanitizeDisplayText(matchedProfile.name) : '尚未找到已发布且启用的 Persona'}</strong>
                <div className="section-subtitle">{matchedProfile ? profileSubtitle(matchedProfile) : '请发布匹配市场、渠道和语言的 Persona 后重试。'}</div>
              </div>
              <div className="message" data-role="assistant">{sanitizeDisplayText(previewIdentityAnswer(draft) || '填写身份声明后，这里会预览客户问“你是谁”时的回答边界。')}</div>
            </div>
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Release / Rollback Evidence" subtitle="版本历史来自真实 PersonaProfileVersion。" />
          <CardBody>
            <div className="list">
              {(selected?.versions ?? []).map((version) => (
                <div key={`${version.version}-${version.published_at}`} className="list-item">
                  <div className="badges"><Badge tone="success">v{version.version}</Badge><Badge>{formatDateTime(version.published_at)}</Badge></div>
                  <strong>{sanitizeDisplayText(version.summary || '无摘要')}</strong>
                  {version.notes ? <div className="section-subtitle">{sanitizeDisplayText(version.notes)}</div> : null}
                  <div className="button-row"><Button variant="secondary" onClick={() => setConfirmAction({ kind: 'rollback', version: version.version, title: `回滚到 v${version.version}？`, description: '回滚会把历史快照复制为新的发布版本。', consequence: '这不是删除当前版本；后端会保留版本历史，便于审计。' })} disabled={!canManage}>回滚到 v{version.version}</Button></div>
                </div>
              ))}
              {!(selected?.versions ?? []).length ? <EmptyState title="暂无发布历史" description="发布后这里会显示版本和回滚入口。" reason="系统用版本历史保证变更可审计。" /> : null}
            </div>
          </CardBody>
        </Card>
      </div>

      <ConfirmDialog
        open={!!confirmAction}
        title={confirmAction?.title || ''}
        description={confirmAction?.description || ''}
        confirmLabel="确认执行"
        consequence={confirmAction?.consequence}
        pending={publishPersona.isPending || disablePersona.isPending || rollbackPersona.isPending}
        onCancel={() => setConfirmAction(null)}
        onConfirm={runConfirmedAction}
      />
      {toast ? <Toast message={toast.message} tone={toast.tone} onClose={() => setToast(null)} /> : null}
    </>
  )
}

function PersonaBuilderPage() {
  return (
    <AppShell>
      <RequireCapability requirement={routeAccess['/ai-persona']}>
        <PersonaBuilderContent />
      </RequireCapability>
    </AppShell>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/ai-persona',
  beforeLoad: () => { if (!getToken()) throw redirect({ to: '/login' }) },
  component: PersonaBuilderPage,
})

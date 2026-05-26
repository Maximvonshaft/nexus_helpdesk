import { useEffect, useMemo, useState } from 'react'
import { createRoute, redirect, useNavigate } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import { canManageAIConfig } from '@/lib/access'
import { formatDateTime, labelize, sanitizeDisplayText } from '@/lib/format'
import { useSession } from '@/hooks/useAuth'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { EmptyState } from '@/components/ui/EmptyState'
import { ErrorSummary } from '@/components/ui/ErrorSummary'
import { Field, Input, Select, Textarea } from '@/components/ui/Field'
import { GuidedWorkflow } from '@/components/ui/GuidedWorkflow'
import { PageHeader } from '@/components/ui/PageHeader'
import { SegmentedControl } from '@/components/ui/SegmentedControl'
import { TechnicalDetails } from '@/components/ui/TechnicalDetails'
import { Toast } from '@/components/ui/Toast'
import type { AIConfigResource, BadgeTone, KnowledgeChunkHit, KnowledgeItem, PersonaProfile } from '@/lib/types'
import { aiConfigTypeLabels } from '@/lib/uxCopy'

const configTypes = ['persona', 'knowledge'] as const
type ControlTab = 'persona' | 'knowledge' | 'rules'
const ruleTypes = ['sop', 'policy'] as const
const knowledgeStatuses = ['draft', 'active', 'archived'] as const
const channelOptions = ['website', 'webchat', 'whatsapp', 'email'] as const

const templateDrafts: Record<string, { summary: string; content: Record<string, unknown>; body?: string }> = {
  persona: {
    summary: '专业、简洁、先确认事实再给承诺；涉及赔付、投诉升级或账号风险时转人工。',
    content: {
      tone: 'professional_concise',
      guardrails: ['No parcel status without trusted tracking evidence', 'Escalate compensation and legal requests'],
      escalation: 'handoff_for_high_risk_or_missing_facts',
    },
  },
  knowledge: {
    summary: '客户可在包裹发出前申请改地址；发出后必须由客服核实承运商能力。',
    content: { category: 'faq', source: 'admin_entered' },
    body: 'Customers may request address changes before dispatch. After dispatch, support must verify carrier options before promising any change.',
  },
  rules: {
    summary: '业务规则、SOP 和执行边界，用于约束助手何时回答、何时转人工、何时只读事实证据。',
    content: {
      scope: 'business_rules_sop_policy',
      rules: ['Never promise parcel status without tracking fact evidence', 'Escalate compensation and address-change execution'],
      execution_boundary: 'AI may explain SOP/policy, but controlled actions require verified workflow tools or human handoff.',
    },
  },
}

function emptyPersonaForm() {
  return {
    profile_key: '',
    name: '',
    description: '',
    channel: 'website',
    language: '',
    is_active: true,
    draft_summary: templateDrafts.persona.summary,
    draft_content_text: JSON.stringify(templateDrafts.persona.content, null, 2),
  }
}

function emptyKnowledgeForm() {
  return {
    item_key: '',
    title: '',
    summary: '',
    status: 'draft',
    source_type: 'text',
    channel: 'website',
    audience_scope: 'customer',
    priority: 100,
    draft_body: templateDrafts.knowledge.body ?? '',
  }
}

function emptyRuleForm() {
  return {
    resource_key: '',
    config_type: 'sop',
    name: '',
    description: '',
    scope_type: 'global',
    scope_value: '',
    is_active: true,
    draft_summary: templateDrafts.rules.summary,
    draft_content_text: JSON.stringify(templateDrafts.rules.content, null, 2),
  }
}

function stringifyDraft(value: unknown) {
  try {
    return JSON.stringify(value ?? {}, null, 2)
  } catch {
    return '{\n  "goal": ""\n}'
  }
}

function statusTone(status: string, publishedVersion = 0): BadgeTone {
  if (status === 'archived') return 'danger'
  if (publishedVersion > 0 && status === 'active') return 'success'
  if (status === 'draft') return 'warning'
  return 'default'
}

function AIControlPage() {
  const session = useSession()
  const navigate = useNavigate()
  const client = useQueryClient()
  const permitted = canManageAIConfig(session.data)
  const [tab, setTab] = useState<ControlTab>('persona')
  const [selectedPersonaId, setSelectedPersonaId] = useState<number | null>(null)
  const [selectedKnowledgeId, setSelectedKnowledgeId] = useState<number | null>(null)
  const [selectedRuleId, setSelectedRuleId] = useState<number | null>(null)
  const [personaForm, setPersonaForm] = useState(emptyPersonaForm())
  const [knowledgeForm, setKnowledgeForm] = useState(emptyKnowledgeForm())
  const [ruleForm, setRuleForm] = useState(emptyRuleForm())
  const [uploadFile, setUploadFile] = useState<File | null>(null)
  const [retrievalQuery, setRetrievalQuery] = useState('')
  const [toast, setToast] = useState<{ message: string; tone?: 'default' | 'danger' | 'success' } | null>(null)
  const [confirmAction, setConfirmAction] = useState<null | { kind: 'publish-persona' | 'disable-persona' | 'publish-knowledge' | 'archive-knowledge' | 'publish-rule' | 'disable-rule'; title: string; description: string; consequence: string }>(null)
  const [confirmRollback, setConfirmRollback] = useState<null | { target: 'persona' | 'knowledge' | 'rule'; version: number }>(null)

  const personas = useQuery({ queryKey: ['persona-profiles'], queryFn: () => api.personaProfiles(), enabled: permitted })
  const knowledge = useQuery({ queryKey: ['knowledge-items'], queryFn: () => api.knowledgeItems(), enabled: permitted })
  const rules = useQuery({
    queryKey: ['ai-config-business-rules'],
    queryFn: async () => {
      const [sop, policy] = await Promise.all([api.aiConfigs('sop'), api.aiConfigs('policy')])
      return [...sop, ...policy].sort((a, b) => a.config_type.localeCompare(b.config_type) || a.name.localeCompare(b.name))
    },
    enabled: permitted,
  })
  const markets = useQuery({ queryKey: ['markets-ai-control'], queryFn: api.markets, enabled: permitted })
  const personaDetail = useQuery({ queryKey: ['persona-profile', selectedPersonaId], queryFn: () => api.personaProfile(selectedPersonaId as number), enabled: permitted && !!selectedPersonaId })
  const knowledgeDetail = useQuery({ queryKey: ['knowledge-item', selectedKnowledgeId], queryFn: () => api.knowledgeItem(selectedKnowledgeId as number), enabled: permitted && !!selectedKnowledgeId })
  const ruleVersions = useQuery({ queryKey: ['ai-config-rule-versions', selectedRuleId], queryFn: () => api.aiConfigVersions(selectedRuleId as number), enabled: permitted && !!selectedRuleId })

  const selectedPersona = personaDetail.data ?? null
  const selectedKnowledge = knowledgeDetail.data ?? null
  const selectedRule = useMemo(() => (rules.data ?? []).find((item) => item.id === selectedRuleId) ?? null, [rules.data, selectedRuleId])
  const personaRows = personas.data?.profiles ?? []
  const knowledgeRows = knowledge.data?.items ?? []
  const ruleRows = rules.data ?? []

  const jsonError = useMemo(() => {
    try {
      JSON.parse(personaForm.draft_content_text || '{}')
      return ''
    } catch (err) {
      return err instanceof Error ? err.message : 'JSON 格式无效'
    }
  }, [personaForm.draft_content_text])

  const ruleJsonError = useMemo(() => {
    try {
      JSON.parse(ruleForm.draft_content_text || '{}')
      return ''
    } catch (err) {
      return err instanceof Error ? err.message : 'JSON 格式无效'
    }
  }, [ruleForm.draft_content_text])

  useEffect(() => {
    if (session.data && !permitted) navigate({ to: '/' })
  }, [navigate, permitted, session.data])

  useEffect(() => {
    if (!selectedPersona) return
    setPersonaForm({
      profile_key: selectedPersona.profile_key,
      name: selectedPersona.name,
      description: selectedPersona.description ?? '',
      channel: selectedPersona.channel ?? 'website',
      language: selectedPersona.language ?? '',
      is_active: selectedPersona.is_active,
      draft_summary: selectedPersona.draft_summary ?? '',
      draft_content_text: stringifyDraft(selectedPersona.draft_content_json),
    })
  }, [selectedPersona])

  useEffect(() => {
    if (!selectedKnowledge) return
    setKnowledgeForm({
      item_key: selectedKnowledge.item_key,
      title: selectedKnowledge.title,
      summary: selectedKnowledge.summary ?? '',
      status: selectedKnowledge.status,
      source_type: selectedKnowledge.source_type,
      channel: selectedKnowledge.channel ?? 'website',
      audience_scope: selectedKnowledge.audience_scope,
      priority: selectedKnowledge.priority,
      draft_body: selectedKnowledge.draft_body ?? '',
    })
  }, [selectedKnowledge])

  useEffect(() => {
    if (!selectedRule) return
    setRuleForm({
      resource_key: selectedRule.resource_key,
      config_type: ruleTypes.includes(selectedRule.config_type as typeof ruleTypes[number]) ? selectedRule.config_type : 'sop',
      name: selectedRule.name,
      description: selectedRule.description ?? '',
      scope_type: selectedRule.scope_type || 'global',
      scope_value: selectedRule.scope_value ?? '',
      is_active: selectedRule.is_active,
      draft_summary: selectedRule.draft_summary ?? selectedRule.published_summary ?? '',
      draft_content_text: stringifyDraft(selectedRule.draft_content_json ?? selectedRule.published_content_json),
    })
  }, [selectedRule])

  const invalidatePersona = async (id?: number | null) => {
    await client.invalidateQueries({ queryKey: ['persona-profiles'] })
    if (id) await client.invalidateQueries({ queryKey: ['persona-profile', id] })
  }

  const invalidateKnowledge = async (id?: number | null) => {
    await client.invalidateQueries({ queryKey: ['knowledge-items'] })
    if (id) await client.invalidateQueries({ queryKey: ['knowledge-item', id] })
  }

  const invalidateRules = async (id?: number | null) => {
    await client.invalidateQueries({ queryKey: ['ai-config-business-rules'] })
    if (id) await client.invalidateQueries({ queryKey: ['ai-config-rule-versions', id] })
  }

  const savePersona = useMutation({
    mutationFn: async () => {
      const payload = {
        profile_key: personaForm.profile_key,
        name: personaForm.name,
        description: personaForm.description || null,
        channel: personaForm.channel || null,
        language: personaForm.language || null,
        is_active: personaForm.is_active,
        draft_summary: personaForm.draft_summary || null,
        draft_content_json: JSON.parse(personaForm.draft_content_text || '{}'),
      }
      if (selectedPersonaId) {
        const { profile_key: _profileKey, ...updatePayload } = payload
        return api.updatePersonaProfile(selectedPersonaId, updatePayload)
      }
      return api.createPersonaProfile(payload)
    },
    onSuccess: async (saved) => {
      setSelectedPersonaId(saved.id)
      setToast({ message: 'Persona 草稿已保存', tone: 'success' })
      await invalidatePersona(saved.id)
    },
    onError: (err: Error) => setToast({ message: err.message || '保存 Persona 失败', tone: 'danger' }),
  })

  const publishPersona = useMutation({
    mutationFn: async () => {
      if (!selectedPersonaId) throw new Error('请先保存 Persona 草稿')
      return api.publishPersonaProfile(selectedPersonaId, 'publish from AI Control Center')
    },
    onSuccess: async (version) => {
      setToast({ message: `Persona 已发布 v${version.version}`, tone: 'success' })
      await invalidatePersona(selectedPersonaId)
    },
    onError: (err: Error) => setToast({ message: err.message || '发布 Persona 失败', tone: 'danger' }),
  })

  const updatePersona = useMutation({
    mutationFn: (payload: Partial<PersonaProfile>) => {
      if (!selectedPersonaId) throw new Error('请选择 Persona')
      return api.updatePersonaProfile(selectedPersonaId, payload)
    },
    onSuccess: async () => {
      setToast({ message: 'Persona 已停用', tone: 'success' })
      await invalidatePersona(selectedPersonaId)
    },
    onError: (err: Error) => setToast({ message: err.message || '更新 Persona 失败', tone: 'danger' }),
  })

  const rollbackPersona = useMutation({
    mutationFn: (version: number) => {
      if (!selectedPersonaId) throw new Error('请选择 Persona')
      return api.rollbackPersonaProfile(selectedPersonaId, version, `rollback to v${version}`)
    },
    onSuccess: async (version) => {
      setToast({ message: `Persona 已回滚并发布为 v${version.version}`, tone: 'success' })
      await invalidatePersona(selectedPersonaId)
    },
    onError: (err: Error) => setToast({ message: err.message || '回滚 Persona 失败', tone: 'danger' }),
  })

  const saveKnowledge = useMutation({
    mutationFn: async () => {
      const payload = {
        item_key: knowledgeForm.item_key,
        title: knowledgeForm.title,
        summary: knowledgeForm.summary || null,
        status: knowledgeForm.status,
        source_type: knowledgeForm.source_type,
        channel: knowledgeForm.channel || null,
        audience_scope: knowledgeForm.audience_scope,
        priority: Number(knowledgeForm.priority) || 100,
        draft_body: knowledgeForm.draft_body || null,
        draft_normalized_text: knowledgeForm.draft_body || null,
      }
      if (selectedKnowledgeId) {
        const { item_key: _itemKey, ...updatePayload } = payload
        return api.updateKnowledgeItem(selectedKnowledgeId, updatePayload)
      }
      return api.createKnowledgeItem(payload)
    },
    onSuccess: async (saved) => {
      setSelectedKnowledgeId(saved.id)
      setToast({ message: '知识草稿已保存', tone: 'success' })
      await invalidateKnowledge(saved.id)
    },
    onError: (err: Error) => setToast({ message: err.message || '保存知识失败', tone: 'danger' }),
  })

  const uploadKnowledge = useMutation({
    mutationFn: async () => {
      if (!uploadFile) throw new Error('请选择要上传的文档')
      if (!selectedKnowledgeId) {
        return api.createKnowledgeItemFromUpload(uploadFile, {
          item_key: knowledgeForm.item_key || undefined,
          title: knowledgeForm.title || undefined,
          channel: knowledgeForm.channel || undefined,
          audience_scope: knowledgeForm.audience_scope || undefined,
        })
      }
      return api.uploadKnowledgeDocument(selectedKnowledgeId, uploadFile)
    },
    onSuccess: async (saved) => {
      setSelectedKnowledgeId(saved.id)
      setUploadFile(null)
      setToast({ message: '文档已解析到草稿，可预览后发布', tone: 'success' })
      await invalidateKnowledge(saved.id)
    },
    onError: (err: Error) => setToast({ message: err.message || '上传解析失败', tone: 'danger' }),
  })

  const publishKnowledge = useMutation({
    mutationFn: async () => {
      if (!selectedKnowledgeId) throw new Error('请先保存知识草稿')
      return api.publishKnowledgeItem(selectedKnowledgeId, 'publish from AI Control Center')
    },
    onSuccess: async (version) => {
      setToast({ message: `知识已发布 v${version.version} 并完成分段索引`, tone: 'success' })
      await invalidateKnowledge(selectedKnowledgeId)
    },
    onError: (err: Error) => setToast({ message: err.message || '发布知识失败', tone: 'danger' }),
  })

  const updateKnowledge = useMutation({
    mutationFn: (payload: Partial<KnowledgeItem>) => {
      if (!selectedKnowledgeId) throw new Error('请选择知识条目')
      return api.updateKnowledgeItem(selectedKnowledgeId, payload)
    },
    onSuccess: async () => {
      setToast({ message: '知识条目已归档', tone: 'success' })
      await invalidateKnowledge(selectedKnowledgeId)
    },
    onError: (err: Error) => setToast({ message: err.message || '更新知识失败', tone: 'danger' }),
  })

  const rollbackKnowledge = useMutation({
    mutationFn: (version: number) => {
      if (!selectedKnowledgeId) throw new Error('请选择知识条目')
      return api.rollbackKnowledgeItem(selectedKnowledgeId, version, `rollback to v${version}`)
    },
    onSuccess: async (version) => {
      setToast({ message: `知识已回滚并发布为 v${version.version}`, tone: 'success' })
      await invalidateKnowledge(selectedKnowledgeId)
    },
    onError: (err: Error) => setToast({ message: err.message || '回滚知识失败', tone: 'danger' }),
  })

  const retrieval = useMutation({
    mutationFn: () => api.testKnowledgeRetrieval({
      q: retrievalQuery,
      channel: knowledgeForm.channel || null,
      audience_scope: knowledgeForm.audience_scope || 'customer',
      limit: 5,
    }),
    onError: (err: Error) => setToast({ message: err.message || '检索测试失败', tone: 'danger' }),
  })

  const saveRule = useMutation({
    mutationFn: async () => {
      const payload: Partial<AIConfigResource> = {
        resource_key: ruleForm.resource_key.trim(),
        config_type: ruleForm.config_type,
        name: ruleForm.name.trim(),
        description: ruleForm.description.trim() || null,
        scope_type: ruleForm.scope_type.trim() || 'global',
        scope_value: ruleForm.scope_value.trim() || null,
        is_active: ruleForm.is_active,
        draft_summary: ruleForm.draft_summary.trim() || null,
        draft_content_json: JSON.parse(ruleForm.draft_content_text || '{}') as Record<string, unknown>,
      }
      if (selectedRuleId) {
        const { resource_key: _resourceKey, ...updatePayload } = payload
        return api.updateAIConfig(selectedRuleId, updatePayload)
      }
      return api.createAIConfig(payload)
    },
    onSuccess: async (saved) => {
      setSelectedRuleId(saved.id)
      setToast({ message: '业务规则草稿已保存', tone: 'success' })
      await invalidateRules(saved.id)
    },
    onError: (err: Error) => setToast({ message: err.message || '保存业务规则失败', tone: 'danger' }),
  })

  const publishRule = useMutation({
    mutationFn: async () => {
      if (!selectedRuleId) throw new Error('请先保存业务规则草稿')
      return api.publishAIConfig(selectedRuleId, 'publish from AI Control Center')
    },
    onSuccess: async (version) => {
      setToast({ message: `业务规则已发布 v${version.version}`, tone: 'success' })
      await invalidateRules(selectedRuleId)
    },
    onError: (err: Error) => setToast({ message: err.message || '发布业务规则失败', tone: 'danger' }),
  })

  const updateRule = useMutation({
    mutationFn: (payload: Partial<AIConfigResource>) => {
      if (!selectedRuleId) throw new Error('请选择业务规则')
      return api.updateAIConfig(selectedRuleId, payload)
    },
    onSuccess: async () => {
      setToast({ message: '业务规则已停用', tone: 'success' })
      await invalidateRules(selectedRuleId)
    },
    onError: (err: Error) => setToast({ message: err.message || '更新业务规则失败', tone: 'danger' }),
  })

  const rollbackRule = useMutation({
    mutationFn: (version: number) => {
      if (!selectedRuleId) throw new Error('请选择业务规则')
      return api.rollbackAIConfig(selectedRuleId, version, `rollback to v${version}`)
    },
    onSuccess: async (version) => {
      setToast({ message: `业务规则已回滚并发布为 v${version.version}`, tone: 'success' })
      await invalidateRules(selectedRuleId)
    },
    onError: (err: Error) => setToast({ message: err.message || '回滚业务规则失败', tone: 'danger' }),
  })

  const runConfirmedAction = () => {
    const action = confirmAction?.kind
    setConfirmAction(null)
    if (action === 'publish-persona') publishPersona.mutate()
    if (action === 'disable-persona') updatePersona.mutate({ is_active: false })
    if (action === 'publish-knowledge') publishKnowledge.mutate()
    if (action === 'archive-knowledge') updateKnowledge.mutate({ status: 'archived' })
    if (action === 'publish-rule') publishRule.mutate()
    if (action === 'disable-rule') updateRule.mutate({ is_active: false })
  }

  return (
    <AppShell>
      <PageHeader
        eyebrow="AI Control Center"
        title="AI 控制中心"
        description="智能助手规则与知识配置：配置助手人格、业务知识、发布版本和运行时检索。只有已发布、启用、渠道匹配且未过期的内容会进入 WebChat/Codex 运行时。"
        actions={<div className="button-row"><Button variant="secondary" onClick={() => { if (tab === 'persona') { setSelectedPersonaId(null); setPersonaForm(emptyPersonaForm()) } else if (tab === 'knowledge') { setSelectedKnowledgeId(null); setKnowledgeForm(emptyKnowledgeForm()) } else { setSelectedRuleId(null); setRuleForm(emptyRuleForm()) } }}>新建{tab === 'persona' ? ' Persona' : tab === 'knowledge' ? '知识' : '业务规则'}</Button><Button variant="primary" onClick={() => tab === 'persona' ? savePersona.mutate() : tab === 'knowledge' ? saveKnowledge.mutate() : saveRule.mutate()} disabled={tab === 'persona' ? savePersona.isPending || !!jsonError : tab === 'knowledge' ? saveKnowledge.isPending : saveRule.isPending || !!ruleJsonError}>{tab === 'persona' ? '保存 Persona 草稿' : tab === 'knowledge' ? '保存知识草稿' : '保存规则草稿'}</Button></div>}
      />

      {!permitted ? (
        <Card><CardHeader title="无权限访问" subtitle="只有具备 AI 配置治理权限的账号才可以管理 AI 控制中心。" /><CardBody><div className="message" data-role="agent">请联系管理员调整权限。</div></CardBody></Card>
      ) : (
        <>
          <div className="metrics-grid metrics-grid-wide">
            <div className="metric-card"><div className="metric-label">Persona</div><div className="metric-value">{personaRows.length}</div><div className="metric-hint">已发布 {personaRows.filter((item) => item.published_version > 0).length}</div></div>
            <div className="metric-card"><div className="metric-label">Knowledge</div><div className="metric-value">{knowledgeRows.length}</div><div className="metric-hint">生效 {knowledgeRows.filter((item) => item.status === 'active' && item.published_version > 0).length}</div></div>
            <div className="metric-card"><div className="metric-label">Business Rules</div><div className="metric-value">{ruleRows.length}</div><div className="metric-hint">已发布 {ruleRows.filter((item) => item.published_version > 0).length}</div></div>
            <div className="metric-card"><div className="metric-label">分段索引</div><div className="metric-value">{knowledgeRows.reduce((sum, item) => sum + (item.chunk_count || 0), 0)}</div><div className="metric-hint">发布时生成</div></div>
            <div className="metric-card"><div className="metric-label">事实边界</div><div className="metric-value">强制</div><div className="metric-hint">物流状态只信 tracking fact</div></div>
          </div>

          <Card className="soft">
            <CardHeader title="生产发布步骤" subtitle="草稿不会进入运行时；发布后才会被上下文构建器按渠道、市场、受众和有效期筛选。" />
            <CardBody>
              <GuidedWorkflow steps={[
                { title: '维护 Persona', description: '定义语气、边界和升级原则。', status: personaRows.some((item) => item.published_version > 0) ? 'done' : 'active' },
                { title: '发布业务规则', description: '保留 SOP、Policy 和执行边界。', status: ruleRows.some((item) => item.published_version > 0) ? 'done' : 'todo' },
                { title: '上传知识', description: '解析文档并预览草稿。', status: knowledgeRows.some((item) => item.source_type === 'file') ? 'done' : 'todo' },
                { title: '发布索引', description: '发布后生成 KnowledgeChunk。', status: knowledgeRows.some((item) => item.chunk_count > 0) ? 'done' : 'todo' },
                { title: '检索测试', description: '确认命中内容和过滤条件。', status: retrieval.data ? 'done' : 'todo' },
                { title: '运行时注入', description: 'ProviderRequest.metadata 注入安全上下文。', status: 'done' },
              ]} />
            </CardBody>
          </Card>

          <div className="workspace-toolbar">
            <SegmentedControl value={tab} onChange={(value) => setTab(value as ControlTab)} options={[{ label: 'Persona', value: 'persona' }, { label: 'Knowledge Base', value: 'knowledge' }, { label: 'Business Rules', value: 'rules' }]} />
            <div className="workspace-toolbar-meta">当前市场数据 {markets.data?.length ?? 0} 个</div>
          </div>

          {tab === 'persona' ? (
            <div className="page-grid split-grid-wide">
              <Card>
                <CardHeader title="Persona Profiles" subtitle="运行时只读取已发布且启用的 PersonaProfile。" />
                <CardBody>
                  <div className="list">
                    {personaRows.map((item) => (
                      <button key={item.id} className={`queue-card ${selectedPersonaId === item.id ? 'selected' : ''}`} onClick={() => setSelectedPersonaId(item.id)}>
                        <div className="badges"><Badge>{sanitizeDisplayText(item.channel || 'global')}</Badge><Badge>{sanitizeDisplayText(item.language || 'all languages')}</Badge>{item.is_active ? <Badge tone="success">启用</Badge> : <Badge>停用</Badge>}{item.published_version > 0 ? <Badge tone="success">v{item.published_version}</Badge> : <Badge tone="warning">未发布</Badge>}</div>
                        <div className="queue-card-title">{sanitizeDisplayText(item.name)}</div>
                        <div className="queue-card-meta">{sanitizeDisplayText(item.profile_key)} · {formatDateTime(item.updated_at)}</div>
                        <div className="queue-card-meta">{sanitizeDisplayText(item.draft_summary || item.published_summary || item.description || '暂无摘要')}</div>
                      </button>
                    ))}
                    {!personaRows.length ? <EmptyState title="还没有 Persona" description="先创建默认 WebChat Persona，再发布给运行时使用。" reason="未发布的 Persona 不会影响客户回复。" /> : null}
                  </div>
                </CardBody>
              </Card>

              <Card>
                <CardHeader title={selectedPersonaId ? '编辑 Persona 草稿' : '新建 Persona'} subtitle="用业务语言维护助手口径；高级 JSON 仅用于迁移或排查。" />
                <CardBody>
                  <div className="stack">
                    {jsonError ? <ErrorSummary title="高级 JSON 暂时不能保存" errors={[`JSON 格式无效：${jsonError}`]} /> : null}
                    <div className="button-row">{configTypes.map((item) => <Button key={item} variant="secondary" onClick={() => { const template = templateDrafts[item]; if (item === 'persona') setPersonaForm((s) => ({ ...s, draft_summary: template.summary, draft_content_text: stringifyDraft(template.content) })) }}>套用{aiConfigTypeLabels[item]}模板</Button>)}</div>
                    <div className="form-grid">
                      <Field label="Profile Key" required example="default.website"><Input value={personaForm.profile_key} onChange={(e) => setPersonaForm((s) => ({ ...s, profile_key: e.target.value }))} /></Field>
                      <Field label="名称" required><Input value={personaForm.name} onChange={(e) => setPersonaForm((s) => ({ ...s, name: e.target.value }))} /></Field>
                      <Field label="渠道"><Select value={personaForm.channel} onChange={(e) => setPersonaForm((s) => ({ ...s, channel: e.target.value }))}>{channelOptions.map((item) => <option key={item} value={item}>{item}</option>)}</Select></Field>
                      <Field label="语言" hint="留空表示所有语言；也可输入 global / all / any / *。"><Input value={personaForm.language} placeholder="所有语言" onChange={(e) => setPersonaForm((s) => ({ ...s, language: e.target.value }))} /></Field>
                    </div>
                    <Field label="业务说明"><Textarea value={personaForm.description} onChange={(e) => setPersonaForm((s) => ({ ...s, description: e.target.value }))} /></Field>
                    <Field label="发布摘要" required><Textarea value={personaForm.draft_summary} onChange={(e) => setPersonaForm((s) => ({ ...s, draft_summary: e.target.value }))} /></Field>
                    <TechnicalDetails title="高级 JSON 配置" summary="仅管理员排查或批量迁移时编辑">
                      <Field label="草稿内容 JSON" error={jsonError || undefined}><Textarea rows={12} value={personaForm.draft_content_text} onChange={(e) => setPersonaForm((s) => ({ ...s, draft_content_text: e.target.value }))} /></Field>
                    </TechnicalDetails>
                    <label className="toggle-row"><input type="checkbox" checked={personaForm.is_active} onChange={(e) => setPersonaForm((s) => ({ ...s, is_active: e.target.checked }))} /> 当前 Persona 启用</label>
                    <div className="button-row"><Button variant="primary" onClick={() => savePersona.mutate()} disabled={savePersona.isPending || !!jsonError}>保存草稿</Button><Button onClick={() => setConfirmAction({ kind: 'publish-persona', title: '发布当前 Persona？', description: '发布后，匹配渠道的 WebChat/Codex 运行时可以读取这个 Persona。', consequence: '请确认语气、升级规则和事实边界已经检查。' })} disabled={!selectedPersonaId || publishPersona.isPending || !!jsonError}>发布</Button><Button variant="danger" onClick={() => setConfirmAction({ kind: 'disable-persona', title: '停用当前 Persona？', description: '停用后运行时不会再选择这个 Persona。', consequence: '如果没有其他匹配 Persona，助手将只使用基础运行时规则。' })} disabled={!selectedPersonaId || !selectedPersona?.is_active}>停用</Button></div>
                  </div>
                </CardBody>
              </Card>

              <Card>
                <CardHeader title="Persona 发布历史" subtitle="回滚会复制历史快照并发布为新版本。" />
                <CardBody>
                  <VersionList versions={selectedPersona?.versions ?? []} onRollback={(version) => setConfirmRollback({ target: 'persona', version })} />
                </CardBody>
              </Card>
            </div>
          ) : tab === 'knowledge' ? (
            <div className="page-grid split-grid-wide">
              <Card>
                <CardHeader title="Knowledge Items" subtitle="知识只回答政策、SOP、FAQ；不会作为包裹实时状态证据。" />
                <CardBody>
                  <div className="list">
                    {knowledgeRows.map((item) => (
                      <button key={item.id} className={`queue-card ${selectedKnowledgeId === item.id ? 'selected' : ''}`} onClick={() => setSelectedKnowledgeId(item.id)}>
                        <div className="badges"><Badge tone={statusTone(item.status, item.published_version)}>{labelize(item.status)}</Badge><Badge>{labelize(item.source_type)}</Badge><Badge>{sanitizeDisplayText(item.channel || 'global')}</Badge>{item.published_version > 0 ? <Badge tone="success">v{item.published_version}</Badge> : <Badge tone="warning">未发布</Badge>}</div>
                        <div className="queue-card-title">{sanitizeDisplayText(item.title)}</div>
                        <div className="queue-card-meta">{sanitizeDisplayText(item.item_key)} · chunk {item.chunk_count || 0} · {formatDateTime(item.updated_at)}</div>
                        <div className="queue-card-meta">{sanitizeDisplayText(item.summary || item.draft_body || '暂无内容')}</div>
                      </button>
                    ))}
                    {!knowledgeRows.length ? <EmptyState title="还没有知识条目" description="创建文本知识或上传文档，预览解析结果后发布。" reason="草稿、归档、渠道不匹配或过期知识不会注入运行时。" /> : null}
                  </div>
                </CardBody>
              </Card>

              <Card>
                <CardHeader title={selectedKnowledgeId ? '编辑知识草稿' : '新建知识'} subtitle="上传文档会先解析到草稿；发布后生成可检索分段。" />
                <CardBody>
                  <div className="stack">
                    <div className="button-row">{configTypes.map((item) => <Button key={item} variant="secondary" onClick={() => { const template = templateDrafts[item]; if (item === 'knowledge') setKnowledgeForm((s) => ({ ...s, summary: template.summary, draft_body: template.body || s.draft_body })) }}>套用{aiConfigTypeLabels[item]}模板</Button>)}</div>
                    <div className="form-grid">
                      <Field label="Item Key" required example="faq.address-change"><Input value={knowledgeForm.item_key} onChange={(e) => setKnowledgeForm((s) => ({ ...s, item_key: e.target.value }))} /></Field>
                      <Field label="标题" required><Input value={knowledgeForm.title} onChange={(e) => setKnowledgeForm((s) => ({ ...s, title: e.target.value }))} /></Field>
                      <Field label="状态"><Select value={knowledgeForm.status} onChange={(e) => setKnowledgeForm((s) => ({ ...s, status: e.target.value }))}>{knowledgeStatuses.map((item) => <option key={item} value={item}>{labelize(item)}</option>)}</Select></Field>
                      <Field label="渠道"><Select value={knowledgeForm.channel} onChange={(e) => setKnowledgeForm((s) => ({ ...s, channel: e.target.value }))}>{channelOptions.map((item) => <option key={item} value={item}>{item}</option>)}</Select></Field>
                      <Field label="受众"><Input value={knowledgeForm.audience_scope} onChange={(e) => setKnowledgeForm((s) => ({ ...s, audience_scope: e.target.value }))} /></Field>
                      <Field label="优先级"><Input type="number" value={knowledgeForm.priority} onChange={(e) => setKnowledgeForm((s) => ({ ...s, priority: Number(e.target.value) }))} /></Field>
                    </div>
                    <Field label="摘要"><Textarea value={knowledgeForm.summary} onChange={(e) => setKnowledgeForm((s) => ({ ...s, summary: e.target.value }))} /></Field>
                    <Field label="草稿正文 / 解析预览" hint="这里是发布前预览。发布后才会进入 KnowledgeChunk 检索。"><Textarea rows={12} value={knowledgeForm.draft_body} onChange={(e) => setKnowledgeForm((s) => ({ ...s, draft_body: e.target.value, source_type: 'text' }))} /></Field>
                    <div className="kv-grid">
                      <div className="kv"><label>解析状态</label><strong>{sanitizeDisplayText(selectedKnowledge?.parsing_status || 'unparsed')}</strong></div>
                      <div className="kv"><label>索引版本</label><strong>v{selectedKnowledge?.indexed_version || 0} · {selectedKnowledge?.chunk_count || 0} chunks</strong></div>
                    </div>
                    {selectedKnowledge?.parsing_error ? <ErrorSummary title="文档解析错误" errors={[selectedKnowledge.parsing_error]} /> : null}
                    <div className="form-grid">
                      <Field label="上传知识文档" hint="支持 UTF-8 文本和 PDF。上传后会覆盖当前草稿正文。"><Input type="file" accept=".txt,.pdf,text/plain,application/pdf" onChange={(e) => setUploadFile(e.target.files?.[0] ?? null)} /></Field>
                      <Field label="已选文件"><Input value={uploadFile?.name || selectedKnowledge?.file_name || ''} readOnly /></Field>
                    </div>
                    <div className="button-row"><Button variant="primary" onClick={() => saveKnowledge.mutate()} disabled={saveKnowledge.isPending}>保存草稿</Button><Button onClick={() => uploadKnowledge.mutate()} disabled={!uploadFile || uploadKnowledge.isPending}>{selectedKnowledgeId ? '上传并解析' : '上传并创建'}</Button><Button onClick={() => setConfirmAction({ kind: 'publish-knowledge', title: '发布当前知识？', description: '发布后会生成 KnowledgeChunk，并允许匹配渠道的运行时检索。', consequence: '请确认正文不包含未核实的包裹实时状态。' })} disabled={!selectedKnowledgeId || publishKnowledge.isPending}>发布并索引</Button><Button variant="danger" onClick={() => setConfirmAction({ kind: 'archive-knowledge', title: '归档当前知识？', description: '归档后运行时不会再检索这条知识。', consequence: '已发布版本仍保留在历史中，可回滚后重新发布。' })} disabled={!selectedKnowledgeId || selectedKnowledge?.status === 'archived'}>归档</Button></div>
                  </div>
                </CardBody>
              </Card>

              <Card>
                <CardHeader title="检索测试" subtitle="按当前渠道和受众过滤，只展示会进入运行时的发布分段。" />
                <CardBody>
                  <div className="stack">
                    <Field label="客户问题"><Input value={retrievalQuery} onChange={(e) => setRetrievalQuery(e.target.value)} placeholder="Can I change my delivery address?" /></Field>
                    <div className="button-row"><Button onClick={() => retrieval.mutate()} disabled={!retrievalQuery.trim() || retrieval.isPending}>测试检索</Button></div>
                    <div className="list">
                      {(retrieval.data?.hits ?? []).map((hit: KnowledgeChunkHit) => (
                        <div key={`${hit.item_key}-${hit.chunk_index}`} className="list-item">
                          <div className="badges"><Badge tone="success">score {hit.score}</Badge><Badge>v{hit.published_version}</Badge></div>
                          <strong>{sanitizeDisplayText(hit.title)}</strong>
                          <div className="section-subtitle">{sanitizeDisplayText(hit.item_key)} · chunk {hit.chunk_index}</div>
                          <div className="message" data-role="assistant">{sanitizeDisplayText(hit.text)}</div>
                        </div>
                      ))}
                      {retrieval.data && !retrieval.data.hits.length ? <EmptyState title="没有命中可注入知识" description="请检查关键词、渠道、受众、发布状态和有效期。" reason="系统不会放宽过滤条件去读取错误渠道或草稿知识。" /> : null}
                    </div>
                  </div>
                </CardBody>
              </Card>
            </div>
          ) : (
            <div className="page-grid split-grid-wide">
              <Card>
                <CardHeader title="Business Rules / SOP / Policy" subtitle="这些规则用于治理执行边界；不会替代 PersonaProfile 或 KnowledgeItem。" />
                <CardBody>
                  <div className="list">
                    {ruleRows.map((item) => (
                      <button key={item.id} className={`queue-card ${selectedRuleId === item.id ? 'selected' : ''}`} onClick={() => setSelectedRuleId(item.id)}>
                        <div className="badges"><Badge>{labelize(item.config_type)}</Badge><Badge>{labelize(item.scope_type)}</Badge>{item.is_active ? <Badge tone="success">启用</Badge> : <Badge>停用</Badge>}{item.published_version > 0 ? <Badge tone="success">v{item.published_version}</Badge> : <Badge tone="warning">未发布</Badge>}</div>
                        <div className="queue-card-title">{sanitizeDisplayText(item.name)}</div>
                        <div className="queue-card-meta">{sanitizeDisplayText(item.resource_key)} · {formatDateTime(item.updated_at)}</div>
                        <div className="queue-card-meta">{sanitizeDisplayText(item.draft_summary || item.published_summary || item.description || '暂无摘要')}</div>
                      </button>
                    ))}
                    {!ruleRows.length ? <EmptyState title="还没有业务规则" description="添加 SOP 或 Policy，明确 AI 可以解释什么、必须转人工什么。" reason="保留业务规则能力，避免 Persona/Knowledge 变成唯一控制面。" /> : null}
                  </div>
                </CardBody>
              </Card>

              <Card>
                <CardHeader title={selectedRuleId ? '编辑业务规则草稿' : '新建业务规则'} subtitle="用于 SOP、Policy 和执行边界管理。" />
                <CardBody>
                  <div className="stack">
                    {ruleJsonError ? <ErrorSummary title="规则 JSON 暂时不能保存" errors={[`JSON 格式无效：${ruleJsonError}`]} /> : null}
                    <div className="form-grid">
                      <Field label="Resource Key" required example="sop.tracking.truth-boundary"><Input value={ruleForm.resource_key} onChange={(e) => setRuleForm((s) => ({ ...s, resource_key: e.target.value }))} /></Field>
                      <Field label="类型"><Select value={ruleForm.config_type} onChange={(e) => setRuleForm((s) => ({ ...s, config_type: e.target.value }))}>{ruleTypes.map((item) => <option key={item} value={item}>{labelize(item)}</option>)}</Select></Field>
                      <Field label="名称" required><Input value={ruleForm.name} onChange={(e) => setRuleForm((s) => ({ ...s, name: e.target.value }))} /></Field>
                      <Field label="Scope"><Input value={ruleForm.scope_type} onChange={(e) => setRuleForm((s) => ({ ...s, scope_type: e.target.value }))} /></Field>
                    </div>
                    <Field label="Scope Value"><Input value={ruleForm.scope_value} onChange={(e) => setRuleForm((s) => ({ ...s, scope_value: e.target.value }))} /></Field>
                    <Field label="说明"><Textarea value={ruleForm.description} onChange={(e) => setRuleForm((s) => ({ ...s, description: e.target.value }))} /></Field>
                    <Field label="规则摘要"><Textarea value={ruleForm.draft_summary} onChange={(e) => setRuleForm((s) => ({ ...s, draft_summary: e.target.value }))} /></Field>
                    <TechnicalDetails title="SOP / Policy JSON" summary="高级规则结构，仅管理员编辑">
                      <Field label="草稿内容 JSON" error={ruleJsonError || undefined}><Textarea rows={12} value={ruleForm.draft_content_text} onChange={(e) => setRuleForm((s) => ({ ...s, draft_content_text: e.target.value }))} /></Field>
                    </TechnicalDetails>
                    <label className="toggle-row"><input type="checkbox" checked={ruleForm.is_active} onChange={(e) => setRuleForm((s) => ({ ...s, is_active: e.target.checked }))} /> 当前业务规则启用</label>
                    <div className="button-row"><Button variant="primary" onClick={() => saveRule.mutate()} disabled={saveRule.isPending || !!ruleJsonError}>保存规则草稿</Button><Button onClick={() => setConfirmAction({ kind: 'publish-rule', title: '发布当前业务规则？', description: '发布后会进入业务规则治理历史。', consequence: '请确认该规则不会和事实边界冲突。' })} disabled={!selectedRuleId || publishRule.isPending || !!ruleJsonError}>发布规则</Button><Button variant="danger" onClick={() => setConfirmAction({ kind: 'disable-rule', title: '停用当前业务规则？', description: '停用后该规则不会被视为有效治理项。', consequence: '历史版本仍可审计和回滚。' })} disabled={!selectedRuleId || !selectedRule?.is_active}>停用</Button></div>
                  </div>
                </CardBody>
              </Card>

              <Card>
                <CardHeader title="业务规则发布历史" subtitle="回滚并重新发布规则。" />
                <CardBody>
                  <VersionList versions={ruleVersions.data ?? []} onRollback={(version) => setConfirmRollback({ target: 'rule', version })} />
                </CardBody>
              </Card>
            </div>
          )}
        </>
      )}

      <ConfirmDialog
        open={!!confirmAction}
        title={confirmAction?.title || ''}
        description={confirmAction?.description || ''}
        confirmLabel="确认执行"
        cancelLabel="取消"
        consequence={confirmAction?.consequence}
        onCancel={() => setConfirmAction(null)}
        onConfirm={runConfirmedAction}
      />
      <ConfirmDialog
        open={!!confirmRollback}
        title="回滚并重新发布？"
        description={`将历史 v${confirmRollback?.version ?? ''} 复制为新的发布版本。`}
        confirmLabel="确认回滚"
        cancelLabel="取消"
        consequence="回滚不是删除当前版本，而是创建一个新版本，便于审计。"
        onCancel={() => setConfirmRollback(null)}
        onConfirm={() => {
          const target = confirmRollback?.target
          const version = confirmRollback?.version
          setConfirmRollback(null)
          if (!version) return
          if (target === 'persona') rollbackPersona.mutate(version)
          if (target === 'knowledge') rollbackKnowledge.mutate(version)
          if (target === 'rule') rollbackRule.mutate(version)
        }}
      />
      {toast ? <Toast message={toast.message} tone={toast.tone} onClose={() => setToast(null)} /> : null}
    </AppShell>
  )
}

function VersionList({ versions, onRollback }: { versions: { version: number; published_at?: string; summary?: string | null; notes?: string | null }[]; onRollback: (version: number) => void }) {
  if (!versions.length) return <EmptyState title="暂无发布历史" description="发布后这里会显示版本和回滚入口。" reason="系统用版本历史保证变更可审计。" />
  return (
    <div className="list">
      {versions.map((item) => (
        <div key={`${item.version}-${item.published_at}`} className="list-item">
          <div className="badges"><Badge tone="success">v{item.version}</Badge><Badge>{formatDateTime(item.published_at)}</Badge></div>
          <strong>{sanitizeDisplayText(item.summary || '无摘要')}</strong>
          {item.notes ? <div className="section-subtitle">{sanitizeDisplayText(item.notes)}</div> : null}
          <div className="button-row"><Button variant="secondary" onClick={() => onRollback(item.version)}>回滚到 v{item.version}</Button></div>
        </div>
      ))}
    </div>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/ai-control',
  beforeLoad: () => {
    if (!getToken()) throw redirect({ to: '/login' })
  },
  component: AIControlPage,
})

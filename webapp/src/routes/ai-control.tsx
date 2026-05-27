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
const knowledgeKinds = ['document', 'faq', 'business_fact', 'policy', 'sop'] as const
const factStatuses = ['draft', 'approved', 'archived'] as const
const answerModes = ['direct_answer', 'guided_answer', 'handoff_only'] as const
const channelOptions = ['website', 'webchat', 'whatsapp', 'email'] as const
const visibilityOptions = [
  { value: 'customer', label: '客户可见' },
  { value: 'internal', label: '仅内部' },
] as const
const knowledgeUploadAccept = '.txt,.pdf,.docx,.xlsx,.csv,.md,.markdown,.html,.htm,text/plain,application/pdf,text/markdown,text/csv,text/html,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

const templateDrafts: Record<string, { summary: string; content: Record<string, unknown>; body?: string }> = {
  persona: {
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
    knowledge_kind: 'document',
    market_id: '',
    channel: 'website',
    audience_scope: 'customer',
    language: '',
    priority: 100,
    fact_question: '',
    fact_answer: '',
    fact_aliases_text: '',
    fact_status: 'draft',
    answer_mode: 'guided_answer',
    citation_metadata_text: '{\n  "source": "admin_entered"\n}',
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

function parseOptionalJsonObject(value: string): Record<string, unknown> {
  try {
    const parsed = JSON.parse(value || '{}')
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed as Record<string, unknown> : {}
  } catch {
    return {}
  }
}

function draftStringValue(draft: Record<string, unknown>, key: string) {
  const value = draft[key]
  return typeof value === 'string' ? value : ''
}

function draftListText(draft: Record<string, unknown>, key: string) {
  const value = draft[key]
  if (Array.isArray(value)) return value.filter((item): item is string => typeof item === 'string').join('\n')
  return typeof value === 'string' ? value : ''
}

function setDraftField(draftText: string, key: string, value: string, asList = false) {
  const draft = parseDraftObject(draftText)
  if (asList) {
    const items = value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean)
    if (items.length) draft[key] = items
    else delete draft[key]
    return stringifyDraft(draft)
  }
  const cleaned = value.trim()
  if (cleaned) draft[key] = value
  else delete draft[key]
  return stringifyDraft(draft)
}

function statusTone(status: string, publishedVersion = 0): BadgeTone {
  if (status === 'archived') return 'danger'
  if (publishedVersion > 0 && status === 'active') return 'success'
  if (status === 'draft') return 'warning'
  return 'default'
}

function formatFileSize(value?: number | null) {
  if (!value) return '未上传'
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${Math.round(value / 102.4) / 10} KB`
  return `${Math.round(value / 1024 / 102.4) / 10} MB`
}

function makeKnowledgeItemKey(value: string) {
  const normalized = value.trim().toLowerCase().normalize('NFKD').replace(/[\u0300-\u036f]/g, '')
  const slug = normalized.replace(/\.[a-z0-9]+$/i, '').replace(/[^a-z0-9_.-]+/g, '-').replace(/^[-_.]+|[-_.]+$/g, '').slice(0, 90)
  return `kb.${slug || `knowledge-${Date.now().toString(36)}`}`.slice(0, 120)
}

function buildDraftSections(value: string) {
  const cleaned = value.trim()
  if (!cleaned) return []
  const rawSections = cleaned
    .split(/\n{2,}|(?=^#{1,4}\s+)/m)
    .map((part) => normalizePreviewText(part))
    .filter(Boolean)
  const sections = rawSections.length > 1 ? rawSections : splitLongPreview(cleaned)
  return sections.slice(0, 8)
}

function splitLongPreview(value: string) {
  const normalized = normalizePreviewText(value)
  if (!normalized) return []
  if (normalized.length <= 900) return [normalized]
  const sections: string[] = []
  for (let index = 0; index < normalized.length; index += 760) {
    sections.push(normalized.slice(index, index + 900).trim())
  }
  return sections
}

function normalizePreviewText(value: string) {
  return value.replace(/\s+/g, ' ').trim()
}

function tokenizePreview(value: string) {
  const normalized = value.toLowerCase().normalize('NFKC')
  const terms = new Set<string>()
  for (const match of normalized.matchAll(/[a-z][a-z0-9_-]{2,}|\d[\da-z.-]{1,}/g)) {
    const term = match[0]
    if (!['the', 'and', 'for', 'you', 'your', 'with', 'can', 'how', 'what', 'when', 'where'].includes(term)) terms.add(term)
  }
  for (const phrase of normalized.matchAll(/[\u4e00-\u9fff]{2,}/g)) {
    const value = phrase[0]
    for (let size = 2; size <= Math.min(4, value.length); size += 1) {
      for (let index = 0; index <= value.length - size; index += 1) {
        terms.add(value.slice(index, index + size))
      }
    }
  }
  return [...terms].slice(0, 32)
}

function isLiveTrackingQuestion(value: string) {
  const normalized = value.toLowerCase()
  const trackingTerm = /tracking|track my|waybill|parcel|package|delivery status|运单|单号|物流|包裹|快递|查件/.test(normalized)
  const liveStatusTerm = /where is|current|status|arriv|delivered|signed|now|my parcel|my package|到哪|在哪|状态|现在|签收|派送|送到|物流信息/.test(normalized)
  return trackingTerm && liveStatusTerm
}

function previewDraftQuestion(question: string, sections: string[], title: string) {
  const trimmed = question.trim()
  if (!trimmed) {
    return { status: 'idle', tone: 'default' as BadgeTone, label: '等待问题', action: '输入客户问题后显示判断', matchedTerms: [] as string[], title }
  }
  if (isLiveTrackingQuestion(trimmed)) {
    return { status: 'tracking', tone: 'warning' as BadgeTone, label: '需要物流证据', action: '实时包裹状态不能从静态知识推断，需 tracking 结果或转人工。', matchedTerms: tokenizePreview(trimmed).slice(0, 8), title }
  }
  const terms = tokenizePreview(trimmed)
  let best = { score: 0, section: '', index: -1, matchedTerms: [] as string[] }
  sections.forEach((section, index) => {
    const haystack = section.toLowerCase().normalize('NFKC')
    const matchedTerms = terms.filter((term) => haystack.includes(term))
    const score = matchedTerms.length
    if (score > best.score) best = { score, section, index, matchedTerms }
  })
  if (!best.score) {
    return { status: 'insufficient', tone: 'danger' as BadgeTone, label: '知识不足', action: 'AI 不应编造答案，应要求更多信息或转人工。', matchedTerms: terms.slice(0, 8), title }
  }
  return {
    status: 'grounded',
    tone: 'success' as BadgeTone,
    label: '可按知识回答',
    action: '回答应限制在命中的知识片段内。',
    matchedTerms: best.matchedTerms.slice(0, 10),
    section: best.section,
    sectionIndex: best.index,
    title,
  }
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
  const [confirmAction, setConfirmAction] = useState<null | { kind: 'publish-persona' | 'disable-persona' | 'replace-knowledge-file' | 'publish-knowledge' | 'archive-knowledge' | 'publish-rule' | 'disable-rule'; title: string; description: string; consequence: string }>(null)
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
  const marketLabelById = useMemo(() => new Map((markets.data ?? []).map((item) => [item.id, `${item.code} · ${item.name}`])), [markets.data])
  const draftSections = useMemo(() => buildDraftSections(knowledgeForm.draft_body), [knowledgeForm.draft_body])
  const draftQuestionPreview = useMemo(
    () => previewDraftQuestion(retrievalQuery, draftSections, knowledgeForm.title || selectedKnowledge?.title || uploadFile?.name || '当前草稿'),
    [draftSections, knowledgeForm.title, retrievalQuery, selectedKnowledge?.title, uploadFile?.name],
  )

  const jsonError = useMemo(() => {
    try {
      JSON.parse(personaForm.draft_content_text || '{}')
      return ''
    } catch (err) {
      return err instanceof Error ? err.message : 'JSON 格式无效'
    }
  }, [personaForm.draft_content_text])
  const personaDraft = useMemo(() => parseDraftObject(personaForm.draft_content_text), [personaForm.draft_content_text])
  const updatePersonaDraftField = (key: string, value: string, asList = false) => {
    setPersonaForm((s) => ({ ...s, draft_content_text: setDraftField(s.draft_content_text, key, value, asList) }))
  }

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
      knowledge_kind: selectedKnowledge.knowledge_kind || 'document',
      market_id: selectedKnowledge.market_id ? String(selectedKnowledge.market_id) : '',
      channel: selectedKnowledge.channel ?? 'website',
      audience_scope: selectedKnowledge.audience_scope,
      language: selectedKnowledge.language ?? '',
      priority: selectedKnowledge.priority,
      fact_question: selectedKnowledge.fact_question ?? '',
      fact_answer: selectedKnowledge.fact_answer ?? '',
      fact_aliases_text: (selectedKnowledge.fact_aliases_json ?? []).join('\n'),
      fact_status: selectedKnowledge.fact_status || 'draft',
      answer_mode: selectedKnowledge.answer_mode || 'guided_answer',
      citation_metadata_text: stringifyDraft(selectedKnowledge.citation_metadata_json ?? { source: 'admin_entered' }),
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
        language: personaForm.language.trim() || null,
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
      const itemKey = knowledgeForm.item_key.trim() || makeKnowledgeItemKey(knowledgeForm.title || uploadFile?.name || 'knowledge')
      const payload = {
        item_key: itemKey,
        title: knowledgeForm.title,
        summary: knowledgeForm.summary || null,
        status: knowledgeForm.status,
        source_type: knowledgeForm.source_type,
        knowledge_kind: knowledgeForm.knowledge_kind,
        market_id: knowledgeForm.market_id ? Number(knowledgeForm.market_id) : null,
        channel: knowledgeForm.channel || null,
        audience_scope: knowledgeForm.audience_scope,
        language: knowledgeForm.language.trim() || null,
        priority: Number(knowledgeForm.priority) || 100,
        fact_question: knowledgeForm.fact_question || null,
        fact_answer: knowledgeForm.fact_answer || null,
        fact_aliases_json: parseLines(knowledgeForm.fact_aliases_text),
        fact_status: knowledgeForm.fact_status,
        answer_mode: knowledgeForm.answer_mode,
        citation_metadata_json: parseOptionalJsonObject(knowledgeForm.citation_metadata_text),
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
      return api.createKnowledgeItemFromUpload(uploadFile, {
        item_key: knowledgeForm.item_key || undefined,
        title: knowledgeForm.title || undefined,
        market_id: knowledgeForm.market_id ? Number(knowledgeForm.market_id) : undefined,
        channel: knowledgeForm.channel || undefined,
        audience_scope: knowledgeForm.audience_scope || undefined,
        language: knowledgeForm.language.trim() || undefined,
      })
    },
    onSuccess: async (saved) => {
      setSelectedKnowledgeId(saved.id)
      setUploadFile(null)
      setToast({ message: '文档已作为新知识解析到草稿，可预览后发布', tone: 'success' })
      await invalidateKnowledge(saved.id)
    },
    onError: (err: Error) => setToast({ message: err.message || '上传解析失败', tone: 'danger' }),
  })

  const replaceKnowledgeFile = useMutation({
    mutationFn: async () => {
      if (!uploadFile) throw new Error('请选择要替换的文档')
      if (!selectedKnowledgeId) throw new Error('请选择要替换的知识')
      return api.uploadKnowledgeDocument(selectedKnowledgeId, uploadFile)
    },
    onSuccess: async (saved) => {
      setSelectedKnowledgeId(saved.id)
      setUploadFile(null)
      setToast({ message: '当前知识文件已替换，可预览后再发布', tone: 'success' })
      await invalidateKnowledge(saved.id)
    },
    onError: (err: Error) => setToast({ message: err.message || '替换文件失败', tone: 'danger' }),
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
      market_id: knowledgeForm.market_id ? Number(knowledgeForm.market_id) : null,
      channel: knowledgeForm.channel || null,
      audience_scope: knowledgeForm.audience_scope || 'customer',
      language: knowledgeForm.language.trim() || null,
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
    if (action === 'replace-knowledge-file') replaceKnowledgeFile.mutate()
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
        actions={<div className="button-row"><Button variant="secondary" onClick={() => { if (tab === 'persona') { setSelectedPersonaId(null); setPersonaForm(emptyPersonaForm()) } else if (tab === 'knowledge') { setSelectedKnowledgeId(null); setKnowledgeForm(emptyKnowledgeForm()); setUploadFile(null) } else { setSelectedRuleId(null); setRuleForm(emptyRuleForm()) } }}>新建{tab === 'persona' ? ' Persona' : tab === 'knowledge' ? '知识' : '业务规则'}</Button><Button variant="primary" onClick={() => tab === 'persona' ? savePersona.mutate() : tab === 'knowledge' ? saveKnowledge.mutate() : saveRule.mutate()} disabled={tab === 'persona' ? savePersona.isPending || !!jsonError : tab === 'knowledge' ? saveKnowledge.isPending : saveRule.isPending || !!ruleJsonError}>{tab === 'persona' ? '保存 Persona 草稿' : tab === 'knowledge' ? '保存知识草稿' : '保存规则草稿'}</Button></div>}
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
                    <div className="form-grid">
                      <Field label="Customer-facing brand name"><Input value={draftStringValue(personaDraft, 'brand_name')} onChange={(e) => updatePersonaDraftField('brand_name', e.target.value)} /></Field>
                      <Field label="Assistant display name"><Input value={draftStringValue(personaDraft, 'assistant_name')} onChange={(e) => updatePersonaDraftField('assistant_name', e.target.value)} /></Field>
                      <Field label="Role label"><Input value={draftStringValue(personaDraft, 'role_label')} onChange={(e) => updatePersonaDraftField('role_label', e.target.value)} /></Field>
                    </div>
                    <Field label="Identity statement"><Textarea rows={3} value={draftStringValue(personaDraft, 'identity_statement')} onChange={(e) => updatePersonaDraftField('identity_statement', e.target.value)} /></Field>
                    <Field label="Identity answer rule"><Textarea rows={3} value={draftStringValue(personaDraft, 'identity_answer_rule')} onChange={(e) => updatePersonaDraftField('identity_answer_rule', e.target.value)} /></Field>
                    <div className="form-grid">
                      <Field label="Capabilities" hint="每行一项"><Textarea rows={4} value={draftListText(personaDraft, 'capabilities')} onChange={(e) => updatePersonaDraftField('capabilities', e.target.value, true)} /></Field>
                      <Field label="Disallowed identity claims" hint="每行一项"><Textarea rows={4} value={draftListText(personaDraft, 'disallowed_identity_claims')} onChange={(e) => updatePersonaDraftField('disallowed_identity_claims', e.target.value, true)} /></Field>
                    </div>
                    <Field label="Handoff boundary"><Textarea rows={3} value={draftStringValue(personaDraft, 'handoff_boundary')} onChange={(e) => updatePersonaDraftField('handoff_boundary', e.target.value)} /></Field>
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
                <CardHeader title="知识库" subtitle="已发布知识会按市场、语言、渠道和可见范围进入客户 AI。" />
                <CardBody>
                  <div className="list">
                    {knowledgeRows.map((item) => (
                      <button key={item.id} className={`queue-card ${selectedKnowledgeId === item.id ? 'selected' : ''}`} onClick={() => setSelectedKnowledgeId(item.id)}>
                        <div className="badges"><Badge tone={statusTone(item.status, item.published_version)}>{labelize(item.status)}</Badge><Badge>{sanitizeDisplayText(marketLabelById.get(item.market_id ?? 0) || '全部市场')}</Badge><Badge>{sanitizeDisplayText(item.language || '全部语言')}</Badge><Badge>{sanitizeDisplayText(item.channel || 'global')}</Badge>{item.published_version > 0 ? <Badge tone="success">v{item.published_version}</Badge> : <Badge tone="warning">未发布</Badge>}</div>
                        <div className="queue-card-title">{sanitizeDisplayText(item.title)}</div>
                        <div className="queue-card-meta">{visibilityOptions.find((option) => option.value === item.audience_scope)?.label || sanitizeDisplayText(item.audience_scope)} · {item.chunk_count || 0} prepared sections · {formatDateTime(item.updated_at)}</div>
                        <div className="queue-card-meta">{sanitizeDisplayText(item.summary || item.draft_body || '暂无内容')}</div>
                      </button>
                    ))}
                    {!knowledgeRows.length ? <EmptyState title="还没有知识条目" description="创建文本知识或上传文档，预览解析结果后发布。" reason="草稿、归档、渠道不匹配或过期知识不会注入运行时。" /> : null}
                  </div>
                </CardBody>
              </Card>

              <Card>
                <CardHeader title={selectedKnowledgeId ? '知识草稿' : '上传知识'} subtitle="默认上传会创建新知识；替换现有文件需要单独确认。" />
                <CardBody>
                  <div className="stack">
                    <div className="form-grid">
                      <Field label="知识名称" required><Input value={knowledgeForm.title} onChange={(e) => setKnowledgeForm((s) => ({ ...s, title: e.target.value }))} placeholder="例如：瑞士客户支持 FAQ" /></Field>
                      <Field label="国家 / 市场"><Select value={knowledgeForm.market_id} onChange={(e) => setKnowledgeForm((s) => ({ ...s, market_id: e.target.value }))}><option value="">全部市场</option>{(markets.data ?? []).map((item) => <option key={item.id} value={String(item.id)}>{item.code} · {item.name}</option>)}</Select></Field>
                      <Field label="语言"><Input value={knowledgeForm.language} onChange={(e) => setKnowledgeForm((s) => ({ ...s, language: e.target.value }))} placeholder="zh / en / 留空全局" /></Field>
                      <Field label="渠道"><Select value={knowledgeForm.channel} onChange={(e) => setKnowledgeForm((s) => ({ ...s, channel: e.target.value }))}>{channelOptions.map((item) => <option key={item} value={item}>{item}</option>)}</Select></Field>
                      <Field label="可见范围"><Select value={knowledgeForm.audience_scope} onChange={(e) => setKnowledgeForm((s) => ({ ...s, audience_scope: e.target.value }))}>{visibilityOptions.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</Select></Field>
                      <Field label="上传知识文件" hint="支持 TXT、PDF、DOCX、XLSX、CSV、Markdown、HTML。"><Input type="file" accept={knowledgeUploadAccept} onChange={(e) => setUploadFile(e.target.files?.[0] ?? null)} /></Field>
                    </div>
                    <div className="kv-grid kv-grid-three">
                      <div className="kv"><label>文件</label><strong>{sanitizeDisplayText(uploadFile?.name || selectedKnowledge?.file_name || '未选择')}</strong></div>
                      <div className="kv"><label>大小</label><strong>{formatFileSize(uploadFile?.size || selectedKnowledge?.file_size)}</strong></div>
                      <div className="kv"><label>解析状态</label><strong>{sanitizeDisplayText(selectedKnowledge?.parsing_status || 'unparsed')}</strong></div>
                      <div className="kv"><label>草稿分段预览</label><strong>{draftSections.length}</strong></div>
                      <div className="kv"><label>发布版本</label><strong>v{selectedKnowledge?.published_version || 0}</strong></div>
                      <div className="kv"><label>已索引分段</label><strong>{selectedKnowledge?.chunk_count || 0}</strong></div>
                    </div>
                    {selectedKnowledge?.parsing_error ? <ErrorSummary title="文档解析错误" errors={[selectedKnowledge.parsing_error]} /> : null}
                    <Field label="摘要"><Textarea value={knowledgeForm.summary} onChange={(e) => setKnowledgeForm((s) => ({ ...s, summary: e.target.value }))} /></Field>
                    <Field label="解析内容预览"><Textarea rows={12} value={knowledgeForm.draft_body} onChange={(e) => setKnowledgeForm((s) => ({ ...s, draft_body: e.target.value, source_type: 'text' }))} /></Field>
                    <div className="list compact">
                      {draftSections.slice(0, 4).map((section, index) => (
                        <div key={`${index}-${section.slice(0, 24)}`} className="list-item">
                          <div className="badges"><Badge>section {index + 1}</Badge></div>
                          <div className="queue-card-meta">{sanitizeDisplayText(section)}</div>
                        </div>
                      ))}
                      {!draftSections.length ? <EmptyState title="暂无解析内容" description="上传或粘贴知识内容后，这里会显示系统理解到的文本。" reason="空草稿不能发布，也不会进入客户 AI。" /> : null}
                    </div>
                    <TechnicalDetails title="高级知识字段" summary="内部 ID、结构化事实、检索和来源元数据">
                      <div className="stack">
                        <div className="button-row">{configTypes.map((item) => <Button key={item} variant="secondary" onClick={() => { const template = templateDrafts[item]; if (item === 'knowledge') setKnowledgeForm((s) => ({ ...s, summary: template.summary, draft_body: template.body || s.draft_body })) }}>套用{aiConfigTypeLabels[item]}模板</Button>)}</div>
                        <div className="form-grid">
                          <Field label="Internal ID" example="faq.address-change"><Input value={knowledgeForm.item_key} onChange={(e) => setKnowledgeForm((s) => ({ ...s, item_key: e.target.value }))} /></Field>
                          <Field label="状态"><Select value={knowledgeForm.status} onChange={(e) => setKnowledgeForm((s) => ({ ...s, status: e.target.value }))}>{knowledgeStatuses.map((item) => <option key={item} value={item}>{labelize(item)}</option>)}</Select></Field>
                          <Field label="知识类型"><Select value={knowledgeForm.knowledge_kind} onChange={(e) => setKnowledgeForm((s) => ({ ...s, knowledge_kind: e.target.value }))}>{knowledgeKinds.map((item) => <option key={item} value={item}>{labelize(item)}</option>)}</Select></Field>
                          <Field label="确认状态"><Select value={knowledgeForm.fact_status} onChange={(e) => setKnowledgeForm((s) => ({ ...s, fact_status: e.target.value }))}>{factStatuses.map((item) => <option key={item} value={item}>{labelize(item)}</option>)}</Select></Field>
                          <Field label="AI 使用方式"><Select value={knowledgeForm.answer_mode} onChange={(e) => setKnowledgeForm((s) => ({ ...s, answer_mode: e.target.value }))}>{answerModes.map((item) => <option key={item} value={item}>{labelize(item)}</option>)}</Select></Field>
                          <Field label="优先级"><Input type="number" value={knowledgeForm.priority} onChange={(e) => setKnowledgeForm((s) => ({ ...s, priority: Number(e.target.value) }))} /></Field>
                        </div>
                        <div className="form-grid">
                          <Field label="结构化问题"><Textarea rows={3} value={knowledgeForm.fact_question} onChange={(e) => setKnowledgeForm((s) => ({ ...s, fact_question: e.target.value }))} /></Field>
                          <Field label="结构化答案"><Textarea rows={3} value={knowledgeForm.fact_answer} onChange={(e) => setKnowledgeForm((s) => ({ ...s, fact_answer: e.target.value }))} /></Field>
                        </div>
                        <div className="form-grid">
                          <Field label="客户问法 / 业务词" hint="每行一个客户问法、业务词或缩写"><Textarea rows={4} value={knowledgeForm.fact_aliases_text} onChange={(e) => setKnowledgeForm((s) => ({ ...s, fact_aliases_text: e.target.value }))} /></Field>
                          <Field label="来源与审批 JSON"><Textarea rows={4} value={knowledgeForm.citation_metadata_text} onChange={(e) => setKnowledgeForm((s) => ({ ...s, citation_metadata_text: e.target.value }))} /></Field>
                        </div>
                      </div>
                    </TechnicalDetails>
                    <div className="button-row"><Button variant="primary" onClick={() => saveKnowledge.mutate()} disabled={saveKnowledge.isPending}>保存草稿</Button><Button onClick={() => uploadKnowledge.mutate()} disabled={!uploadFile || uploadKnowledge.isPending}>上传为新知识</Button><Button variant="danger" onClick={() => setConfirmAction({ kind: 'replace-knowledge-file', title: '替换当前知识文件？', description: '这会把所选文件解析到当前知识草稿，不会创建新知识。', consequence: '请确认当前选中的知识和文件属于同一业务范围，避免把文件传入错误知识。' })} disabled={!selectedKnowledgeId || !uploadFile || replaceKnowledgeFile.isPending}>替换当前文件</Button><Button onClick={() => setConfirmAction({ kind: 'publish-knowledge', title: '发布当前知识？', description: '发布后会生成 KnowledgeChunk，并允许匹配渠道的运行时检索。', consequence: '请确认正文不包含未核实的包裹实时状态。' })} disabled={!selectedKnowledgeId || publishKnowledge.isPending}>发布并索引</Button><Button variant="danger" onClick={() => setConfirmAction({ kind: 'archive-knowledge', title: '归档当前知识？', description: '归档后运行时不会再检索这条知识。', consequence: '已发布版本仍保留在历史中，可回滚后重新发布。' })} disabled={!selectedKnowledgeId || selectedKnowledge?.status === 'archived'}>归档</Button></div>
                  </div>
                </CardBody>
              </Card>

              <Card>
                <CardHeader title="客户问题测试" subtitle="先看当前草稿是否足够；发布后再验证运行时会命中的知识。" />
                <CardBody>
                  <div className="stack">
                    <Field label="客户问题"><Input value={retrievalQuery} onChange={(e) => setRetrievalQuery(e.target.value)} placeholder="POD 是什么意思？" /></Field>
                    <div className="list-item">
                      <div className="badges"><Badge tone={draftQuestionPreview.tone}>{draftQuestionPreview.label}</Badge><Badge>{sanitizeDisplayText(draftQuestionPreview.title)}</Badge></div>
                      <strong>{sanitizeDisplayText(draftQuestionPreview.action)}</strong>
                      {'section' in draftQuestionPreview && draftQuestionPreview.section ? <div className="message" data-role="assistant">{sanitizeDisplayText(draftQuestionPreview.section)}</div> : null}
                      {draftQuestionPreview.matchedTerms.length ? <div className="badges">{draftQuestionPreview.matchedTerms.map((term) => <Badge key={term}>{sanitizeDisplayText(term)}</Badge>)}</div> : null}
                    </div>
                    <div className="button-row"><Button onClick={() => retrieval.mutate()} disabled={!retrievalQuery.trim() || retrieval.isPending}>测试已发布知识</Button></div>
                    {retrieval.data?.query_analysis ? (
                      <div className="kv-grid">
                        <div className="kv"><label>语言</label><strong>{sanitizeDisplayText(retrieval.data.query_analysis.language)}</strong></div>
                        <div className="kv"><label>候选知识</label><strong>{retrieval.data.candidate_count ?? 0} / {retrieval.data.total}</strong></div>
                        <div className="kv"><label>可直接回答</label><strong>{retrieval.data.grounding_would_apply ? '是' : '否'}</strong></div>
                      </div>
                    ) : null}
                    {retrieval.data?.query_analysis ? (
                      <div className="list-item">
                        <strong>客户问题关键词</strong>
                        <div className="badges">{retrieval.data.query_analysis.high_value_terms.map((term) => <Badge key={term}>{sanitizeDisplayText(term)}</Badge>)}</div>
                        <div className="section-subtitle">{sanitizeDisplayText(retrieval.data.query_analysis.normalized_query)}</div>
                      </div>
                    ) : null}
                    <div className="list">
                      {(retrieval.data?.hits ?? []).map((hit: KnowledgeChunkHit) => (
                        <div key={`${hit.item_key}-${hit.chunk_index}`} className="list-item">
                          <div className="badges"><Badge tone="success">score {hit.score}</Badge><Badge>v{hit.published_version}</Badge><Badge>{sanitizeDisplayText(hit.retrieval_method || 'hybrid')}</Badge>{hit.direct_answer ? <Badge tone="success">direct answer</Badge> : null}</div>
                          <strong>{sanitizeDisplayText(hit.title)}</strong>
                          <div className="section-subtitle">{sanitizeDisplayText(hit.source_metadata?.file_name ? String(hit.source_metadata.file_name) : hit.item_key)} · section {hit.chunk_index + 1}</div>
                          {hit.matched_terms?.length ? <div className="badges">{hit.matched_terms.map((term) => <Badge key={term}>{sanitizeDisplayText(term)}</Badge>)}</div> : null}
                          {hit.direct_answer ? <div className="message" data-role="agent">{sanitizeDisplayText(hit.direct_answer)}</div> : null}
                          <div className="message" data-role="assistant">{sanitizeDisplayText(hit.text)}</div>
                          <TechnicalDetails title="Score breakdown" summary="检索方法、分数和来源元数据">
                            <pre className="code-block">{JSON.stringify({ score_breakdown: hit.score_breakdown, source_metadata: hit.source_metadata, metadata: hit.metadata }, null, 2)}</pre>
                          </TechnicalDetails>
                        </div>
                      ))}
                      {retrieval.data && !retrieval.data.hits.length ? <EmptyState title="没有命中可注入知识" description="请检查关键词、渠道、受众、发布状态和有效期。" reason="系统不会放宽过滤条件去读取错误渠道或草稿知识。" /> : null}
                    </div>
                  </div>
                </CardBody>
              </Card>

              <Card>
                <CardHeader title="知识发布历史" subtitle="保留版本证据，可回滚并重新发布。" />
                <CardBody>
                  <VersionList versions={selectedKnowledge?.versions ?? []} onRollback={(version) => setConfirmRollback({ target: 'knowledge', version })} />
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

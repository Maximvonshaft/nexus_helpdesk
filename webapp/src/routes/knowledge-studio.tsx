import { useEffect, useMemo, useState } from 'react'
import { createRoute, redirect } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { RequireCapability } from '@/components/security/RequireCapability'
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
import { useSession } from '@/hooks/useAuth'
import { canManageAIConfig, canReadAIConfig } from '@/lib/access'
import { api, getToken } from '@/lib/api'
import { formatDateTime, labelize, sanitizeDisplayText } from '@/lib/format'
import { routeAccess } from '@/lib/rbac'
import type { BadgeTone, KnowledgeChunkHit, KnowledgeItem, KnowledgeItemVersion } from '@/lib/types'

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

type KnowledgeForm = {
  item_key: string
  title: string
  summary: string
  status: string
  source_type: string
  knowledge_kind: string
  market_id: string
  channel: string
  audience_scope: string
  language: string
  priority: number
  fact_question: string
  fact_answer: string
  fact_aliases_text: string
  fact_status: string
  answer_mode: string
  citation_metadata_text: string
  draft_body: string
}

type PendingAction = {
  kind: 'replace-file' | 'publish' | 'archive'
  title: string
  description: string
  consequence: string
  tone?: 'default' | 'danger'
}

const starterDraft = [
  'Customers may request address changes before dispatch.',
  'After dispatch, support must verify carrier options before promising any change.',
  'If a customer asks for live parcel status, use tracking evidence or hand off to an operator.',
].join('\n')

function emptyKnowledgeForm(): KnowledgeForm {
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
    citation_metadata_text: '{\n  "source": "knowledge_studio"\n}',
    draft_body: starterDraft,
  }
}

function stringifyDraft(value: unknown) {
  try {
    return JSON.stringify(value ?? {}, null, 2)
  } catch {
    return '{\n  "source": "knowledge_studio"\n}'
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

function normalizePreviewText(value: string) {
  return value.replace(/\s+/g, ' ').trim()
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
    return { tone: 'default' as BadgeTone, label: '等待问题', action: '输入客户问题后显示草稿判断', matchedTerms: [] as string[], title }
  }
  if (isLiveTrackingQuestion(trimmed)) {
    return { tone: 'warning' as BadgeTone, label: '需要物流证据', action: '实时包裹状态不能从静态知识推断，需 tracking 结果或转人工。', matchedTerms: tokenizePreview(trimmed).slice(0, 8), title }
  }
  const terms = tokenizePreview(trimmed)
  let best = { score: 0, section: '', index: -1, matchedTerms: [] as string[] }
  sections.forEach((section, index) => {
    const haystack = section.toLowerCase().normalize('NFKC')
    const matchedTerms = terms.filter((term) => haystack.includes(term))
    if (matchedTerms.length > best.score) best = { score: matchedTerms.length, section, index, matchedTerms }
  })
  if (!best.score) {
    return { tone: 'danger' as BadgeTone, label: '知识不足', action: 'AI 不应编造答案，应要求更多信息或转人工。', matchedTerms: terms.slice(0, 8), title }
  }
  return {
    tone: 'success' as BadgeTone,
    label: '可按知识回答',
    action: '回答应限制在命中的知识片段内。',
    matchedTerms: best.matchedTerms.slice(0, 10),
    section: best.section,
    sectionIndex: best.index,
    title,
  }
}

function makeKnowledgeItemKey(value: string) {
  const normalized = value.trim().toLowerCase().normalize('NFKD').replace(/[\u0300-\u036f]/g, '')
  const slug = normalized.replace(/\.[a-z0-9]+$/i, '').replace(/[^a-z0-9_.-]+/g, '-').replace(/^[-_.]+|[-_.]+$/g, '').slice(0, 90)
  return `kb.${slug || `knowledge-${Date.now().toString(36)}`}`.slice(0, 120)
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

function formFromKnowledge(item: KnowledgeItem): KnowledgeForm {
  return {
    item_key: item.item_key,
    title: item.title,
    summary: item.summary ?? '',
    status: item.status,
    source_type: item.source_type,
    knowledge_kind: item.knowledge_kind || 'document',
    market_id: item.market_id ? String(item.market_id) : '',
    channel: item.channel ?? 'website',
    audience_scope: item.audience_scope,
    language: item.language ?? '',
    priority: item.priority,
    fact_question: item.fact_question ?? '',
    fact_answer: item.fact_answer ?? '',
    fact_aliases_text: (item.fact_aliases_json ?? []).join('\n'),
    fact_status: item.fact_status || 'draft',
    answer_mode: item.answer_mode || 'guided_answer',
    citation_metadata_text: stringifyDraft(item.citation_metadata_json ?? { source: 'knowledge_studio' }),
    draft_body: item.draft_body ?? '',
  }
}

function knowledgePayload(form: KnowledgeForm) {
  return {
    item_key: form.item_key.trim() || makeKnowledgeItemKey(form.title || 'knowledge'),
    title: form.title.trim(),
    summary: form.summary.trim() || null,
    status: form.status,
    source_type: form.source_type,
    knowledge_kind: form.knowledge_kind,
    market_id: form.market_id ? Number(form.market_id) : null,
    channel: form.channel || null,
    audience_scope: form.audience_scope,
    language: form.language.trim() || null,
    priority: Number(form.priority) || 100,
    fact_question: form.fact_question.trim() || null,
    fact_answer: form.fact_answer.trim() || null,
    fact_aliases_json: parseLines(form.fact_aliases_text),
    fact_status: form.fact_status,
    answer_mode: form.answer_mode,
    citation_metadata_json: parseOptionalJsonObject(form.citation_metadata_text),
    draft_body: form.draft_body.trim() || null,
    draft_normalized_text: form.draft_body.trim() || null,
  }
}

function objectRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function VersionList({ versions, canManage, onRollback }: { versions: KnowledgeItemVersion[]; canManage: boolean; onRollback: (version: number) => void }) {
  if (!versions.length) return <EmptyState title="暂无发布历史" description="发布知识后会留下可审计版本，可回滚并重新发布。" />
  return (
    <div className="list compact" data-testid="knowledge-release-evidence">
      {versions.map((version) => (
        <div key={version.id} className="list-item">
          <div className="badges"><Badge tone="success">v{version.version}</Badge><Badge>{formatDateTime(version.published_at)}</Badge></div>
          <strong>{sanitizeDisplayText(version.summary || version.notes || '发布快照')}</strong>
          <div className="button-row">
            <Button variant="secondary" onClick={() => onRollback(version.version)} disabled={!canManage}>回滚并重新发布</Button>
          </div>
        </div>
      ))}
    </div>
  )
}

function KnowledgeStudioPage() {
  const session = useSession()
  const client = useQueryClient()
  const canRead = canReadAIConfig(session.data)
  const canManage = canManageAIConfig(session.data)
  const [selectedKnowledgeId, setSelectedKnowledgeId] = useState<number | null>(null)
  const [knowledgeForm, setKnowledgeForm] = useState<KnowledgeForm>(() => emptyKnowledgeForm())
  const [uploadFile, setUploadFile] = useState<File | null>(null)
  const [retrievalQuery, setRetrievalQuery] = useState('')
  const [filterQuery, setFilterQuery] = useState('')
  const [filterStatus, setFilterStatus] = useState('')
  const [filterChannel, setFilterChannel] = useState('')
  const [filterAudience, setFilterAudience] = useState('')
  const [filterMarket, setFilterMarket] = useState('')
  const [toast, setToast] = useState<{ message: string; tone?: 'default' | 'danger' | 'success' } | null>(null)
  const [confirmAction, setConfirmAction] = useState<PendingAction | null>(null)
  const [confirmRollback, setConfirmRollback] = useState<number | null>(null)

  const markets = useQuery({ queryKey: ['markets-knowledge-studio'], queryFn: api.markets, enabled: canRead })
  const knowledge = useQuery({
    queryKey: ['knowledge-studio-items', filterQuery, filterStatus, filterChannel, filterAudience, filterMarket],
    queryFn: () => api.knowledgeItems({
      q: filterQuery.trim() || undefined,
      status: filterStatus || undefined,
      channel: filterChannel || undefined,
      audience_scope: filterAudience || undefined,
      market_id: filterMarket ? Number(filterMarket) : undefined,
    }),
    enabled: canRead,
  })
  const knowledgeDetail = useQuery({
    queryKey: ['knowledge-studio-item', selectedKnowledgeId],
    queryFn: () => api.knowledgeItem(selectedKnowledgeId as number),
    enabled: canRead && !!selectedKnowledgeId,
  })

  const selectedKnowledge = knowledgeDetail.data ?? null
  const knowledgeRows = knowledge.data?.items ?? []
  const activeCount = knowledgeRows.filter((item) => item.status === 'active' && item.published_version > 0).length
  const indexedCount = knowledgeRows.reduce((sum, item) => sum + (item.chunk_count || 0), 0)
  const marketLabelById = useMemo(() => new Map((markets.data ?? []).map((item) => [item.id, `${item.code} · ${item.name}`])), [markets.data])
  const draftSections = useMemo(() => buildDraftSections(knowledgeForm.draft_body), [knowledgeForm.draft_body])
  const draftQuestionPreview = useMemo(
    () => previewDraftQuestion(retrievalQuery, draftSections, knowledgeForm.title || selectedKnowledge?.title || uploadFile?.name || '当前草稿'),
    [draftSections, knowledgeForm.title, retrievalQuery, selectedKnowledge?.title, uploadFile?.name],
  )

  useEffect(() => {
    if (selectedKnowledge) setKnowledgeForm(formFromKnowledge(selectedKnowledge))
  }, [selectedKnowledge])

  async function invalidateKnowledge(id?: number | null) {
    await client.invalidateQueries({ queryKey: ['knowledge-studio-items'] })
    await client.invalidateQueries({ queryKey: ['knowledge-items'] })
    if (id) {
      await client.invalidateQueries({ queryKey: ['knowledge-studio-item', id] })
      await client.invalidateQueries({ queryKey: ['knowledge-item', id] })
    }
  }

  function assertCanManage() {
    if (!canManage) throw new Error('当前账号只有查看权限，不能修改、上传、发布或回滚知识。')
  }

  const saveKnowledge = useMutation({
    mutationFn: async () => {
      assertCanManage()
      const payload = knowledgePayload(knowledgeForm)
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
      assertCanManage()
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
      setToast({ message: '文档已作为新知识解析到草稿', tone: 'success' })
      await invalidateKnowledge(saved.id)
    },
    onError: (err: Error) => setToast({ message: err.message || '上传解析失败', tone: 'danger' }),
  })

  const replaceKnowledgeFile = useMutation({
    mutationFn: async () => {
      assertCanManage()
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
      assertCanManage()
      if (!selectedKnowledgeId) throw new Error('请先保存知识草稿')
      return api.publishKnowledgeItem(selectedKnowledgeId, 'publish from Knowledge Studio')
    },
    onSuccess: async (version) => {
      setToast({ message: `知识已发布 v${version.version} 并完成分段索引`, tone: 'success' })
      await invalidateKnowledge(selectedKnowledgeId)
    },
    onError: (err: Error) => setToast({ message: err.message || '发布知识失败', tone: 'danger' }),
  })

  const archiveKnowledge = useMutation({
    mutationFn: async () => {
      assertCanManage()
      if (!selectedKnowledgeId) throw new Error('请选择知识条目')
      return api.updateKnowledgeItem(selectedKnowledgeId, { status: 'archived' })
    },
    onSuccess: async () => {
      setToast({ message: '知识条目已归档', tone: 'success' })
      await invalidateKnowledge(selectedKnowledgeId)
    },
    onError: (err: Error) => setToast({ message: err.message || '归档知识失败', tone: 'danger' }),
  })

  const rollbackKnowledge = useMutation({
    mutationFn: async (version: number) => {
      assertCanManage()
      if (!selectedKnowledgeId) throw new Error('请选择知识条目')
      return api.rollbackKnowledgeItem(selectedKnowledgeId, version, `rollback to v${version} from Knowledge Studio`)
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

  const runtimeContext = useMutation({
    mutationFn: () => api.testKnowledgeRuntimeContext({
      q: retrievalQuery,
      tenant_key: 'default',
      market_id: knowledgeForm.market_id ? Number(knowledgeForm.market_id) : null,
      channel: knowledgeForm.channel || 'website',
      audience_scope: knowledgeForm.audience_scope || 'customer',
      language: knowledgeForm.language.trim() || null,
      limit: 5,
    }),
    onError: (err: Error) => setToast({ message: err.message || '运行时上下文测试失败', tone: 'danger' }),
  })

  const runtimeKnowledge = objectRecord(runtimeContext.data?.context?.knowledge_context)
  const runtimeHits = Array.isArray(runtimeKnowledge.hits) ? runtimeKnowledge.hits : []
  const writeDisabledReason = canManage ? undefined : '当前账号缺少 ai_config.manage'

  function newKnowledge() {
    setSelectedKnowledgeId(null)
    setKnowledgeForm(emptyKnowledgeForm())
    setUploadFile(null)
  }

  function confirmCurrentAction() {
    const action = confirmAction?.kind
    setConfirmAction(null)
    if (action === 'replace-file') replaceKnowledgeFile.mutate()
    if (action === 'publish') publishKnowledge.mutate()
    if (action === 'archive') archiveKnowledge.mutate()
  }

  return (
    <AppShell>
      <RequireCapability requirement={routeAccess['/knowledge-studio']}>
        <PageHeader
          eyebrow="AI Operations"
          title="Knowledge Studio"
          description="把模板中的知识编辑、分段预览、Golden Question 测试、发布证据和回滚能力接到真实 KnowledgeItem API。"
          actions={
            <div className="button-row">
              <Button variant="secondary" onClick={() => client.invalidateQueries({ queryKey: ['knowledge-studio-items'] })} disabled={knowledge.isFetching}>刷新</Button>
              <Button variant="primary" onClick={newKnowledge} disabled={!canManage}>新建知识</Button>
            </div>
          }
        />

        {!canManage ? (
          <Card className="soft">
            <CardHeader title="只读模式" subtitle="当前账号可查看知识、发布证据和检索结果，但不能保存、上传、发布或回滚。" />
          </Card>
        ) : null}

        <div className="metrics-grid">
          <MetricCard label="Knowledge Items" value={knowledgeRows.length} hint={`当前筛选共 ${knowledge.data?.total ?? 0} 条`} />
          <MetricCard label="已发布" value={activeCount} hint="status=active 且存在发布版本" />
          <MetricCard label="分段索引" value={indexedCount} hint="发布时写入 KnowledgeChunk" />
          <MetricCard label="草稿预览" value={draftSections.length} hint="本地分段，不写入后端" />
        </div>

        <Card>
          <CardHeader title="处理步骤" subtitle="先确认内容来源，再验证检索结果，最后发布或回滚。" />
          <CardBody>
            <GuidedWorkflow steps={[
              { title: '编辑或上传知识', description: '维护标题、市场、渠道、受众、结构化事实和正文。', status: knowledgeForm.title || uploadFile ? 'done' : 'active' },
              { title: '分段预览', description: '在发布前检查草稿会如何被切成可检索片段。', status: draftSections.length ? 'done' : 'todo' },
              { title: 'Golden Question 测试', description: '用真实 retrieve-test/runtime-context-test 验证已发布知识是否命中。', status: retrieval.data || runtimeContext.data ? 'done' : 'todo' },
              { title: '发布证据', description: '发布、回滚和版本历史都保留在 KnowledgeItemVersion。', status: selectedKnowledge?.published_version ? 'done' : 'todo' },
            ]} />
          </CardBody>
        </Card>

        <div className="page-grid split-grid-wide">
          <Card>
            <CardHeader title="知识库" subtitle="按市场、渠道和受众筛选真实后端知识条目。" />
            <CardBody>
              <div className="stack">
                <div className="form-grid">
                  <Field label="搜索"><Input value={filterQuery} onChange={(e) => setFilterQuery(e.target.value)} placeholder="标题、摘要或内部 ID" /></Field>
                  <Field label="状态"><Select value={filterStatus} onChange={(e) => setFilterStatus(e.target.value)}><option value="">全部状态</option>{knowledgeStatuses.map((item) => <option key={item} value={item}>{labelize(item)}</option>)}</Select></Field>
                  <Field label="市场"><Select value={filterMarket} onChange={(e) => setFilterMarket(e.target.value)}><option value="">全部市场</option>{(markets.data ?? []).map((item) => <option key={item.id} value={String(item.id)}>{item.code} · {item.name}</option>)}</Select></Field>
                  <Field label="渠道"><Select value={filterChannel} onChange={(e) => setFilterChannel(e.target.value)}><option value="">全部渠道</option>{channelOptions.map((item) => <option key={item} value={item}>{item}</option>)}</Select></Field>
                  <Field label="受众"><Select value={filterAudience} onChange={(e) => setFilterAudience(e.target.value)}><option value="">全部受众</option>{visibilityOptions.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</Select></Field>
                </div>
                {knowledge.error ? <ErrorSummary title="知识列表加载失败" errors={[knowledge.error.message]} /> : null}
                <div className="list" data-testid="knowledge-studio-item-list">
                  {knowledgeRows.map((item) => (
                    <button key={item.id} className={`queue-card ${selectedKnowledgeId === item.id ? 'selected' : ''}`} onClick={() => setSelectedKnowledgeId(item.id)}>
                      <div className="badges">
                        <Badge tone={statusTone(item.status, item.published_version)}>{labelize(item.status)}</Badge>
                        <Badge>{sanitizeDisplayText(marketLabelById.get(item.market_id ?? 0) || '全部市场')}</Badge>
                        <Badge>{sanitizeDisplayText(item.channel || 'global')}</Badge>
                        <Badge>{item.published_version > 0 ? `v${item.published_version}` : '未发布'}</Badge>
                      </div>
                      <div className="queue-card-title">{sanitizeDisplayText(item.title)}</div>
                      <div className="queue-card-meta">{sanitizeDisplayText(item.item_key)} · {item.chunk_count || 0} sections · {formatDateTime(item.updated_at)}</div>
                      <div className="queue-card-meta">{sanitizeDisplayText(item.summary || item.draft_body || '暂无内容')}</div>
                    </button>
                  ))}
                  {!knowledgeRows.length ? <EmptyState title="还没有知识条目" description="创建文本知识或上传文档，预览解析结果后发布。" reason="草稿、归档、渠道不匹配或过期知识不会注入运行时。" /> : null}
                </div>
              </div>
            </CardBody>
          </Card>

          <Card>
            <CardHeader title={selectedKnowledgeId ? '知识草稿' : '新建知识'} subtitle="保存草稿不会进入运行时；发布后才会生成可检索索引。" />
            <CardBody>
              <div className="stack" data-testid="knowledge-studio-editor">
                <div className="form-grid">
                  <Field label="知识名称" required disabledReason={writeDisabledReason}><Input value={knowledgeForm.title} onChange={(e) => setKnowledgeForm((s) => ({ ...s, title: e.target.value }))} disabled={!canManage} placeholder="例如：瑞士客户支持 FAQ" /></Field>
                  <Field label="市场"><Select value={knowledgeForm.market_id} onChange={(e) => setKnowledgeForm((s) => ({ ...s, market_id: e.target.value }))} disabled={!canManage}><option value="">全部市场</option>{(markets.data ?? []).map((item) => <option key={item.id} value={String(item.id)}>{item.code} · {item.name}</option>)}</Select></Field>
                  <Field label="语言"><Input value={knowledgeForm.language} onChange={(e) => setKnowledgeForm((s) => ({ ...s, language: e.target.value }))} disabled={!canManage} placeholder="zh / en / 留空全局" /></Field>
                  <Field label="渠道"><Select value={knowledgeForm.channel} onChange={(e) => setKnowledgeForm((s) => ({ ...s, channel: e.target.value }))} disabled={!canManage}>{channelOptions.map((item) => <option key={item} value={item}>{item}</option>)}</Select></Field>
                  <Field label="可见范围"><Select value={knowledgeForm.audience_scope} onChange={(e) => setKnowledgeForm((s) => ({ ...s, audience_scope: e.target.value }))} disabled={!canManage}>{visibilityOptions.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</Select></Field>
                  <Field label="上传知识文件" hint="支持 TXT、PDF、DOCX、XLSX、CSV、Markdown、HTML。" disabledReason={writeDisabledReason}><Input type="file" accept={knowledgeUploadAccept} onChange={(e) => setUploadFile(e.target.files?.[0] ?? null)} disabled={!canManage} /></Field>
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
                <Field label="摘要"><Textarea value={knowledgeForm.summary} onChange={(e) => setKnowledgeForm((s) => ({ ...s, summary: e.target.value }))} disabled={!canManage} /></Field>
                <Field label="解析内容预览"><Textarea rows={12} value={knowledgeForm.draft_body} onChange={(e) => setKnowledgeForm((s) => ({ ...s, draft_body: e.target.value, source_type: 'text' }))} disabled={!canManage} /></Field>
                <div className="list compact" data-testid="knowledge-draft-chunk-preview">
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
                    <div className="form-grid">
                      <Field label="Internal ID" example="faq.address-change"><Input value={knowledgeForm.item_key} onChange={(e) => setKnowledgeForm((s) => ({ ...s, item_key: e.target.value }))} disabled={!canManage} /></Field>
                      <Field label="状态"><Select value={knowledgeForm.status} onChange={(e) => setKnowledgeForm((s) => ({ ...s, status: e.target.value }))} disabled={!canManage}>{knowledgeStatuses.map((item) => <option key={item} value={item}>{labelize(item)}</option>)}</Select></Field>
                      <Field label="知识类型"><Select value={knowledgeForm.knowledge_kind} onChange={(e) => setKnowledgeForm((s) => ({ ...s, knowledge_kind: e.target.value }))} disabled={!canManage}>{knowledgeKinds.map((item) => <option key={item} value={item}>{labelize(item)}</option>)}</Select></Field>
                      <Field label="确认状态"><Select value={knowledgeForm.fact_status} onChange={(e) => setKnowledgeForm((s) => ({ ...s, fact_status: e.target.value }))} disabled={!canManage}>{factStatuses.map((item) => <option key={item} value={item}>{labelize(item)}</option>)}</Select></Field>
                      <Field label="AI 使用方式"><Select value={knowledgeForm.answer_mode} onChange={(e) => setKnowledgeForm((s) => ({ ...s, answer_mode: e.target.value }))} disabled={!canManage}>{answerModes.map((item) => <option key={item} value={item}>{labelize(item)}</option>)}</Select></Field>
                      <Field label="优先级"><Input type="number" value={knowledgeForm.priority} onChange={(e) => setKnowledgeForm((s) => ({ ...s, priority: Number(e.target.value) }))} disabled={!canManage} /></Field>
                    </div>
                    <div className="form-grid">
                      <Field label="结构化问题"><Textarea rows={3} value={knowledgeForm.fact_question} onChange={(e) => setKnowledgeForm((s) => ({ ...s, fact_question: e.target.value }))} disabled={!canManage} /></Field>
                      <Field label="结构化答案"><Textarea rows={3} value={knowledgeForm.fact_answer} onChange={(e) => setKnowledgeForm((s) => ({ ...s, fact_answer: e.target.value }))} disabled={!canManage} /></Field>
                    </div>
                    <div className="form-grid">
                      <Field label="客户问法 / 业务词" hint="每行一个客户问法、业务词或缩写"><Textarea rows={4} value={knowledgeForm.fact_aliases_text} onChange={(e) => setKnowledgeForm((s) => ({ ...s, fact_aliases_text: e.target.value }))} disabled={!canManage} /></Field>
                      <Field label="来源与审批 JSON"><Textarea rows={4} value={knowledgeForm.citation_metadata_text} onChange={(e) => setKnowledgeForm((s) => ({ ...s, citation_metadata_text: e.target.value }))} disabled={!canManage} /></Field>
                    </div>
                  </div>
                </TechnicalDetails>
                <div className="button-row">
                  <Button variant="primary" onClick={() => saveKnowledge.mutate()} disabled={!canManage || saveKnowledge.isPending}>保存草稿</Button>
                  <Button onClick={() => uploadKnowledge.mutate()} disabled={!canManage || !uploadFile || uploadKnowledge.isPending}>上传为新知识</Button>
                  <Button variant="danger" onClick={() => setConfirmAction({ kind: 'replace-file', title: '替换当前知识文件？', description: '这会把所选文件解析到当前知识草稿，不会创建新知识。', consequence: '请确认当前选中的知识和文件属于同一业务范围。', tone: 'danger' })} disabled={!canManage || !selectedKnowledgeId || !uploadFile || replaceKnowledgeFile.isPending}>替换当前文件</Button>
                  <Button onClick={() => setConfirmAction({ kind: 'publish', title: '发布当前知识？', description: '发布后会生成 KnowledgeChunk，并允许匹配渠道的运行时检索。', consequence: '请确认正文不包含未核实的包裹实时状态。' })} disabled={!canManage || !selectedKnowledgeId || publishKnowledge.isPending}>发布并索引</Button>
                  <Button variant="danger" onClick={() => setConfirmAction({ kind: 'archive', title: '归档当前知识？', description: '归档后运行时不会再检索这条知识。', consequence: '已发布版本仍保留在历史中，可回滚后重新发布。', tone: 'danger' })} disabled={!canManage || !selectedKnowledgeId || selectedKnowledge?.status === 'archived'}>归档</Button>
                </div>
              </div>
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Golden Question / Runtime 测试" subtitle="先看当前草稿是否足够，再用真实后端验证已发布知识会怎样进入运行时。" />
            <CardBody>
              <div className="stack" data-testid="knowledge-golden-question">
                <Field label="客户问题"><Input value={retrievalQuery} onChange={(e) => setRetrievalQuery(e.target.value)} placeholder="POD 是什么意思？" /></Field>
                <div className="list-item">
                  <div className="badges"><Badge tone={draftQuestionPreview.tone}>{draftQuestionPreview.label}</Badge><Badge>{sanitizeDisplayText(draftQuestionPreview.title)}</Badge></div>
                  <strong>{sanitizeDisplayText(draftQuestionPreview.action)}</strong>
                  {'section' in draftQuestionPreview && draftQuestionPreview.section ? <div className="message" data-role="assistant">{sanitizeDisplayText(draftQuestionPreview.section)}</div> : null}
                  {draftQuestionPreview.matchedTerms.length ? <div className="badges">{draftQuestionPreview.matchedTerms.map((term) => <Badge key={term}>{sanitizeDisplayText(term)}</Badge>)}</div> : null}
                </div>
                <div className="button-row">
                  <Button onClick={() => retrieval.mutate()} disabled={!retrievalQuery.trim() || retrieval.isPending}>测试已发布知识</Button>
                  <Button variant="secondary" onClick={() => runtimeContext.mutate()} disabled={!retrievalQuery.trim() || runtimeContext.isPending}>测试运行时上下文</Button>
                </div>
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
                {runtimeContext.data ? (
                  <TechnicalDetails title="运行时上下文证据" summary={`${runtimeHits.length} 条知识命中会进入 WebChat runtime context`}>
                    <pre className="code-block">{JSON.stringify(runtimeContext.data.context, null, 2)}</pre>
                  </TechnicalDetails>
                ) : null}
              </div>
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="知识发布历史" subtitle="发布和回滚都走真实 KnowledgeItemVersion 契约。" />
            <CardBody>
              <VersionList versions={selectedKnowledge?.versions ?? []} canManage={canManage} onRollback={setConfirmRollback} />
            </CardBody>
          </Card>
        </div>

        <ConfirmDialog
          open={!!confirmAction}
          title={confirmAction?.title ?? ''}
          description={confirmAction?.description ?? ''}
          consequence={confirmAction?.consequence}
          tone={confirmAction?.tone}
          confirmLabel="确认"
          pending={replaceKnowledgeFile.isPending || publishKnowledge.isPending || archiveKnowledge.isPending}
          onConfirm={confirmCurrentAction}
          onCancel={() => setConfirmAction(null)}
        />
        <ConfirmDialog
          open={confirmRollback !== null}
          title="回滚并重新发布知识？"
          description={`将历史版本 v${confirmRollback ?? ''} 作为新的发布版本。`}
          consequence="请确认当前线上知识需要恢复到该版本；该操作会产生新的发布证据。"
          confirmLabel="回滚并发布"
          pending={rollbackKnowledge.isPending}
          onConfirm={() => {
            const version = confirmRollback
            setConfirmRollback(null)
            if (version !== null) rollbackKnowledge.mutate(version)
          }}
          onCancel={() => setConfirmRollback(null)}
        />
        {toast ? <Toast message={toast.message} tone={toast.tone} onClose={() => setToast(null)} /> : null}
      </RequireCapability>
    </AppShell>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/knowledge-studio',
  beforeLoad: () => { if (!getToken()) throw redirect({ to: '/login' }) },
  component: KnowledgeStudioPage,
})

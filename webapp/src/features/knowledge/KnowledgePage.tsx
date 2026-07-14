import { useDeferredValue, useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { EmptyState } from '@/components/ui/EmptyState'
import { ErrorSummary } from '@/components/ui/ErrorSummary'
import { Field, Input, Select, Textarea } from '@/components/ui/Field'
import { sanitizeDisplayText } from '@/lib/format'
import { supportApi } from '@/lib/supportApi'
import { knowledgeStatusPresentation } from '@/lib/supportStatus'
import type { KnowledgeItem } from '@/lib/types'
import './knowledge.css'

 type KnowledgeStatusFilter = 'active' | 'draft' | 'archived' | 'all'
 type KnowledgeKindFilter = 'all' | 'business_fact' | 'faq' | 'policy' | 'sop' | 'document'

 type KnowledgeDraft = {
  item_key: string
  title: string
  fact_question: string
  fact_answer: string
  fact_aliases: string
  summary: string
  status: string
  channel: string
  audience_scope: string
  language: string
  priority: string
  knowledge_kind: string
  answer_mode: string
}

const knowledgeKindOptions: Array<{ value: KnowledgeKindFilter; label: string; description: string }> = [
  { value: 'all', label: '全部分类', description: '查看所有知识' },
  { value: 'business_fact', label: '客服问答', description: '客户常问问题和标准事实' },
  { value: 'faq', label: '常见问题', description: '高频问题和服务说明' },
  { value: 'policy', label: '规则政策', description: '必须遵守的服务规则' },
  { value: 'sop', label: '处理流程', description: '客服和运营可参考的处理步骤' },
  { value: 'document', label: '资料文档', description: '导入资料、长文档和待整理内容' },
]

function serializeKnowledgeDraft(draft: KnowledgeDraft) {
  return JSON.stringify(draft)
}

function normalizeKnowledgeKey(value: string) {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_.-]+/g, '-')
    .replace(/^[^a-z0-9]+/, '')
    .replace(/[^a-z0-9]+$/, '')
    .slice(0, 120)
}

function createKnowledgeKey() {
  return `support.customer.${Date.now().toString(36)}`
}

function knowledgeDraftFromItem(item?: KnowledgeItem | null): KnowledgeDraft {
  if (!item) {
    return {
      item_key: createKnowledgeKey(),
      title: '',
      fact_question: '',
      fact_answer: '',
      fact_aliases: '',
      summary: '',
      status: 'draft',
      channel: 'all',
      audience_scope: 'customer',
      language: '',
      priority: '100',
      knowledge_kind: 'business_fact',
      answer_mode: 'guided_answer',
    }
  }
  return {
    item_key: item.item_key,
    title: item.title || '',
    fact_question: item.fact_question || '',
    fact_answer: item.fact_answer || item.draft_body || item.published_body || '',
    fact_aliases: (item.fact_aliases_json || []).join('\n'),
    summary: item.summary || '',
    status: item.status || 'draft',
    channel: item.channel || 'all',
    audience_scope: item.audience_scope || 'customer',
    language: item.language || '',
    priority: String(item.priority ?? 100),
    knowledge_kind: item.knowledge_kind || 'business_fact',
    answer_mode: item.answer_mode || 'guided_answer',
  }
}

function knowledgePayloadFromDraft(draft: KnowledgeDraft) {
  const question = draft.fact_question.trim()
  const answer = draft.fact_answer.trim()
  const aliases = draft.fact_aliases.split(/\r?\n/).map((item) => item.trim()).filter(Boolean).slice(0, 50)
  const draftBody = [
    question ? `Customer question: ${question}` : '',
    answer ? `Answer guidance: ${answer}` : '',
    aliases.length ? `Alternative customer wording:\n${aliases.map((item) => `- ${item}`).join('\n')}` : '',
  ].filter(Boolean).join('\n\n')
  return {
    title: draft.title.trim(),
    summary: draft.summary.trim() || null,
    status: draft.status,
    source_type: 'text',
    knowledge_kind: draft.knowledge_kind,
    channel: draft.channel === 'all' ? null : draft.channel,
    audience_scope: draft.audience_scope,
    language: draft.language.trim() || null,
    priority: Math.max(0, Math.min(10000, Number.parseInt(draft.priority, 10) || 100)),
    fact_question: question || null,
    fact_answer: answer || null,
    fact_aliases_json: aliases.length ? aliases : null,
    fact_status: draft.status === 'active' ? 'approved' : 'draft',
    answer_mode: draft.answer_mode,
    draft_body: draftBody || answer || question || null,
    draft_normalized_text: draftBody || answer || question || null,
  }
}

function knowledgeKindLabel(kind: string | null | undefined) {
  if (kind === 'business_fact') return '客服问答'
  if (kind === 'faq') return '常见问题'
  if (kind === 'policy') return '规则政策'
  if (kind === 'sop') return '处理流程'
  if (kind === 'document') return '资料文档'
  return sanitizeDisplayText(kind || '知识')
}

function knowledgeKindDescription(kind: string | null | undefined) {
  return knowledgeKindOptions.find((item) => item.value === kind)?.description || '客服知识'
}

function errorCopy(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback
}

export function KnowledgePage() {
  const queryClient = useQueryClient()
  const initialDraft = useMemo(() => knowledgeDraftFromItem(), [])
  const [search, setSearch] = useState('')
  const deferredSearch = useDeferredValue(search)
  const [statusFilter, setStatusFilter] = useState<KnowledgeStatusFilter>('active')
  const [kindFilter, setKindFilter] = useState<KnowledgeKindFilter>('all')
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [isCreating, setIsCreating] = useState(false)
  const [draft, setDraft] = useState<KnowledgeDraft>(initialDraft)
  const [savedDraft, setSavedDraft] = useState<KnowledgeDraft>(initialDraft)
  const [savedMessage, setSavedMessage] = useState('')
  const [retrievalQuery, setRetrievalQuery] = useState('')
  const [publishReviewOpen, setPublishReviewOpen] = useState(false)
  const [discardDraftOpen, setDiscardDraftOpen] = useState(false)
  const pendingDraftActionRef = useRef<(() => void) | null>(null)
  const editorRef = useRef<HTMLDivElement | null>(null)
  const loadedItemIdRef = useRef<number | null>(null)
  const isDirty = useMemo(
    () => serializeKnowledgeDraft(draft) !== serializeKnowledgeDraft(savedDraft),
    [draft, savedDraft],
  )

  const studio = useQuery({
    queryKey: ['canonicalKnowledgeStatus'],
    queryFn: supportApi.knowledgeStudio,
    refetchInterval: 30_000,
    retry: false,
  })
  const items = useQuery({
    queryKey: ['canonicalKnowledgeItems', deferredSearch, statusFilter, kindFilter],
    queryFn: () => supportApi.knowledgeItems({
      q: deferredSearch,
      status: statusFilter === 'all' ? undefined : statusFilter,
      knowledge_kind: kindFilter === 'all' ? undefined : kindFilter,
    }),
    refetchInterval: 30_000,
    retry: false,
  })
  const selectedItem = useMemo(
    () => (items.data?.items ?? []).find((item) => item.id === selectedId) ?? null,
    [items.data?.items, selectedId],
  )

  useEffect(() => {
    if (isCreating || !selectedItem || loadedItemIdRef.current === selectedItem.id) return
    const nextDraft = knowledgeDraftFromItem(selectedItem)
    loadedItemIdRef.current = selectedItem.id
    setDraft(nextDraft)
    setSavedDraft(nextDraft)
    setSavedMessage('')
  }, [isCreating, selectedItem])

  useEffect(() => {
    if (!isDirty) return
    const warnBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', warnBeforeUnload)
    return () => window.removeEventListener('beforeunload', warnBeforeUnload)
  }, [isDirty])

  useEffect(() => {
    if (isCreating || selectedId !== null) return
    const firstItem = items.data?.items?.[0]
    if (firstItem) setSelectedId(firstItem.id)
  }, [isCreating, items.data?.items, selectedId])

  const scrollEditorIntoView = () => {
    window.setTimeout(() => {
      if (window.matchMedia('(max-width: 980px)').matches) {
        editorRef.current?.scrollIntoView({ block: 'start', behavior: 'smooth' })
      }
    }, 0)
  }

  const runWithDraftGuard = (action: () => void) => {
    if (!isDirty) {
      action()
      return
    }
    pendingDraftActionRef.current = action
    setDiscardDraftOpen(true)
  }

  const confirmDraftDiscard = () => {
    const action = pendingDraftActionRef.current
    pendingDraftActionRef.current = null
    setDiscardDraftOpen(false)
    action?.()
  }

  const resetForNew = () => runWithDraftGuard(() => {
    const nextDraft = knowledgeDraftFromItem()
    loadedItemIdRef.current = null
    setSelectedId(null)
    setIsCreating(true)
    setDraft(nextDraft)
    setSavedDraft(nextDraft)
    setSavedMessage('')
    scrollEditorIntoView()
  })

  const selectKnowledgeItem = (itemId: number) => {
    if (itemId === selectedId && !isCreating) return
    runWithDraftGuard(() => {
      loadedItemIdRef.current = null
      setSelectedId(itemId)
      setIsCreating(false)
      setSavedMessage('')
      scrollEditorIntoView()
    })
  }

  const invalidateKnowledge = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['canonicalKnowledgeStatus'] }),
      queryClient.invalidateQueries({ queryKey: ['canonicalKnowledgeItems'] }),
    ])
  }

  const saveMutation = useMutation({
    mutationFn: async (publish: boolean) => {
      const payload = knowledgePayloadFromDraft(draft)
      if (!payload.title) throw new Error('请填写知识标题')
      if (!payload.fact_question && !payload.fact_answer && !payload.draft_body) throw new Error('请填写客户问题或答案事实')
      let item: KnowledgeItem
      if (selectedId && !isCreating) {
        item = await supportApi.updateKnowledgeItem(selectedId, payload)
      } else {
        const itemKey = normalizeKnowledgeKey(draft.item_key) || createKnowledgeKey()
        item = await supportApi.createKnowledgeItem({ ...payload, item_key: itemKey })
      }
      if (publish) {
        await supportApi.publishKnowledgeItem(item.id, 'canonical knowledge publish')
        item = await supportApi.updateKnowledgeItem(item.id, { status: 'active', fact_status: 'approved' })
      }
      return item
    },
    onSuccess: async (item, publish) => {
      const committedDraft = knowledgeDraftFromItem(item)
      loadedItemIdRef.current = item.id
      setSelectedId(item.id)
      setIsCreating(false)
      setDraft(committedDraft)
      setSavedDraft(committedDraft)
      setPublishReviewOpen(false)
      setSavedMessage(publish ? '已提交发布。知识同步完成后，该版本才会用于后续处理。' : '草稿已保存。')
      await invalidateKnowledge()
    },
  })

  const publishMutation = useMutation({
    mutationFn: async () => {
      if (!selectedId) throw new Error('请先选择一条知识')
      await supportApi.publishKnowledgeItem(selectedId, 'canonical knowledge publish')
      return await supportApi.updateKnowledgeItem(selectedId, { status: 'active', fact_status: 'approved' })
    },
    onSuccess: async (item) => {
      const committedDraft = knowledgeDraftFromItem(item)
      loadedItemIdRef.current = item.id
      setDraft(committedDraft)
      setSavedDraft(committedDraft)
      setPublishReviewOpen(false)
      setSavedMessage('已提交发布。知识同步完成后，该版本才会用于后续处理。')
      await invalidateKnowledge()
    },
  })

  const retrievalMutation = useMutation({
    mutationFn: () => supportApi.testKnowledgeRetrieval({
      q: retrievalQuery.trim(),
      channel: draft.channel === 'all' ? null : draft.channel,
      audience_scope: draft.audience_scope || 'customer',
      language: draft.language.trim() || null,
      limit: 5,
    }),
  })

  const busy = saveMutation.isPending || publishMutation.isPending
  const saveError = saveMutation.error || publishMutation.error
  const retrievalHits = retrievalMutation.data?.hits ?? []
  const publicationReady = Boolean(
    draft.title.trim()
    && (draft.fact_question.trim() || draft.fact_answer.trim()),
  )

  const confirmPublication = () => {
    if (!publicationReady || busy) return
    if (isDirty || isCreating || !selectedId) saveMutation.mutate(true)
    else publishMutation.mutate()
  }

  return (
    <main className="nd-knowledge-page">
      <header className="nd-knowledge-header">
        <div>
          <h1>知识与处理规则</h1>
          <p>维护经过审核的客户问答、规则政策和处理流程。知识不能覆盖实时事实、账号权限或业务结果。</p>
        </div>
        {items.isFetching ? <Badge>正在刷新</Badge> : null}
      </header>

      <section className="nd-knowledge-layout" aria-label="知识维护工作区">
        <aside className="nd-knowledge-list" aria-labelledby="knowledge-list-title">
          <div className="nd-knowledge-panel-head">
            <h2 id="knowledge-list-title">知识列表</h2>
            <Button variant="primary" onClick={resetForNew}>新建知识</Button>
          </div>
          <div className="nd-knowledge-filters">
            <Field label="搜索知识">
              <Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="标题、客户问法或答案关键字" autoComplete="off" />
            </Field>
            <Field label="状态">
              <Select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value as KnowledgeStatusFilter)}>
                <option value="active">已上线</option>
                <option value="draft">草稿</option>
                <option value="archived">已归档</option>
                <option value="all">全部</option>
              </Select>
            </Field>
            <Field label="分类">
              <Select value={kindFilter} onChange={(event) => setKindFilter(event.target.value as KnowledgeKindFilter)}>
                {knowledgeKindOptions.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
              </Select>
            </Field>
          </div>
          {items.isError ? (
            <ErrorSummary title="知识列表不可用" errors={[errorCopy(items.error, '请稍后重试')]} />
          ) : (
            <div className="nd-knowledge-items">
              {(items.data?.items ?? []).map((item) => {
                const presentation = knowledgeStatusPresentation(item.status)
                return (
                  <button
                    type="button"
                    className={selectedId === item.id && !isCreating ? 'is-active' : ''}
                    key={item.id}
                    aria-pressed={selectedId === item.id && !isCreating}
                    onClick={() => selectKnowledgeItem(item.id)}
                  >
                    <span>
                      <strong>{sanitizeDisplayText(item.title)}</strong>
                      <small>{sanitizeDisplayText(item.fact_question || item.summary || item.item_key)}</small>
                    </span>
                    <span>
                      <Badge tone={presentation.tone}>{presentation.label}</Badge>
                      <small>{knowledgeKindLabel(item.knowledge_kind)} · v{item.published_version || 0}</small>
                    </span>
                  </button>
                )
              })}
              {!items.data?.items?.length ? <EmptyState title="没有找到知识" description="调整搜索条件，或新建一条知识。" /> : null}
            </div>
          )}
        </aside>

        <section className="nd-knowledge-editor" ref={editorRef} aria-labelledby="knowledge-editor-title">
          <div className="nd-knowledge-panel-head">
            <h2 id="knowledge-editor-title">{selectedId && !isCreating ? '编辑知识' : '新建知识'}</h2>
            <Badge tone={knowledgeStatusPresentation(draft.status).tone}>{knowledgeStatusPresentation(draft.status).label}</Badge>
          </div>
          {saveError ? <ErrorSummary title="保存失败" errors={[errorCopy(saveError, '请稍后重试')]} /> : null}
          {savedMessage ? <div className="nd-knowledge-confirmed" role="status" aria-live="polite"><strong>{savedMessage}</strong></div> : null}
          <div className="nd-knowledge-form">
            <Field label="知识标题" required>
              <Input value={draft.title} onChange={(event) => setDraft((current) => ({ ...current, title: event.target.value }))} placeholder="例如：末派失败怎么处理" autoComplete="off" />
            </Field>
            {isCreating || !selectedId ? (
              <Field label="内部编号" hint="系统内部使用，保存后通常不需要再修改。">
                <Input value={draft.item_key} onChange={(event) => setDraft((current) => ({ ...current, item_key: normalizeKnowledgeKey(event.target.value) }))} autoComplete="off" spellCheck={false} />
              </Field>
            ) : null}
            <Field label="客户会怎么问" required description="写客户可能发来的原话或问题。">
              <Textarea value={draft.fact_question} onChange={(event) => setDraft((current) => ({ ...current, fact_question: event.target.value }))} rows={4} placeholder="例如：我的包裹显示末派失败怎么办？" autoComplete="off" />
            </Field>
            <Field label="答案事实与处理规则" required description="写经过核实的事实、规则和处理步骤，不写固定话术。">
              <Textarea value={draft.fact_answer} onChange={(event) => setDraft((current) => ({ ...current, fact_answer: event.target.value }))} rows={8} placeholder="例如：先确认运单号和收件电话；若状态为末派失败，核实地址和联系方式；必要时转人工处理。" autoComplete="off" />
            </Field>
            <Field label="客户可能的其他说法" hint="一行一个，帮助系统找到正确知识。">
              <Textarea value={draft.fact_aliases} onChange={(event) => setDraft((current) => ({ ...current, fact_aliases: event.target.value }))} rows={4} placeholder={'包裹派送失败\n快递没有送到\n最后一公里配送异常'} autoComplete="off" />
            </Field>
            <div className="nd-knowledge-grid">
              <Field label="适用对象">
                <Select value={draft.audience_scope} onChange={(event) => setDraft((current) => ({ ...current, audience_scope: event.target.value }))}>
                  <option value="customer">客户问答</option>
                  <option value="internal">内部参考</option>
                </Select>
              </Field>
              <Field label="渠道">
                <Select value={draft.channel} onChange={(event) => setDraft((current) => ({ ...current, channel: event.target.value }))}>
                  <option value="all">全部渠道</option>
                  <option value="webchat">网页客服</option>
                  <option value="whatsapp">WhatsApp</option>
                </Select>
              </Field>
              <Field label="语言">
                <Input value={draft.language} onChange={(event) => setDraft((current) => ({ ...current, language: event.target.value }))} placeholder="空表示自动匹配" autoComplete="off" />
              </Field>
              <Field label="优先级">
                <Input value={draft.priority} type="number" min={0} max={10000} onChange={(event) => setDraft((current) => ({ ...current, priority: event.target.value }))} />
              </Field>
              <Field label="知识类型">
                <Select value={draft.knowledge_kind} onChange={(event) => setDraft((current) => ({ ...current, knowledge_kind: event.target.value }))}>
                  <option value="business_fact">客服问答</option>
                  <option value="faq">常见问题</option>
                  <option value="policy">规则政策</option>
                  <option value="sop">处理流程</option>
                  <option value="document">资料文档</option>
                </Select>
              </Field>
              <Field label="回答方式">
                <Select value={draft.answer_mode} onChange={(event) => setDraft((current) => ({ ...current, answer_mode: event.target.value }))}>
                  <option value="guided_answer">按事实组织回复</option>
                  <option value="direct_answer">答案事实优先</option>
                </Select>
              </Field>
            </div>
            <Field label="内部备注">
              <Textarea value={draft.summary} onChange={(event) => setDraft((current) => ({ ...current, summary: event.target.value }))} rows={3} placeholder="给维护人员看的备注，可不填。" autoComplete="off" />
            </Field>
            <div className="nd-knowledge-guidance">
              <strong>{knowledgeKindLabel(draft.knowledge_kind)}：{knowledgeKindDescription(draft.knowledge_kind)}</strong>
              <small>优先级数字越小越靠前。建议：规则政策 10–49，处理流程 50–99，普通客服问答 100，导入资料 200 以上。</small>
            </div>
            <div className="nd-knowledge-actions">
              <Button variant="primary" disabled={busy || !isDirty} loading={saveMutation.isPending && !publishReviewOpen} loadingLabel="保存中…" onClick={() => saveMutation.mutate(false)}>保存草稿</Button>
              <Button variant="secondary" disabled={busy || !publicationReady} onClick={() => setPublishReviewOpen(true)}>审核并发布</Button>
            </div>
          </div>
        </section>

        <aside className="nd-knowledge-side" aria-label="知识验证和同步状态">
          <section>
            <div className="nd-knowledge-panel-head"><h2>测试命中</h2>{retrievalMutation.isPending ? <Badge>测试中</Badge> : null}</div>
            <div className="nd-knowledge-form">
              <Field label="用一句客户问题测试">
                <Input value={retrievalQuery} onChange={(event) => setRetrievalQuery(event.target.value)} placeholder="例如：包裹末派失败怎么办？" autoComplete="off" />
              </Field>
              <Button disabled={!retrievalQuery.trim() || retrievalMutation.isPending} onClick={() => retrievalMutation.mutate()}>测试知识命中</Button>
              {retrievalMutation.error ? <ErrorSummary title="测试失败" errors={[errorCopy(retrievalMutation.error, '请稍后重试')]} /> : null}
              {retrievalMutation.data ? (
                <div className="nd-knowledge-hits">
                  <strong>{retrievalHits.length ? `命中 ${retrievalHits.length} 条知识` : '没有命中知识'}</strong>
                  <small>{retrievalMutation.data.grounding_would_apply ? '当前服务可以使用这些知识' : '当前条件下不会使用这些知识'}</small>
                  {retrievalHits.slice(0, 5).map((hit) => (
                    <article key={`${hit.item_id}-${hit.chunk_index}`}>
                      <span>{sanitizeDisplayText(hit.title)}</span>
                      <p>{sanitizeDisplayText(hit.direct_answer || hit.text).slice(0, 260)}</p>
                      <small>相关度 {typeof hit.score === 'number' ? hit.score.toFixed(3) : hit.score}</small>
                    </article>
                  ))}
                </div>
              ) : null}
            </div>
          </section>
          <section>
            <div className="nd-knowledge-panel-head"><h2>知识同步</h2>{studio.isFetching ? <Badge>正在刷新</Badge> : null}</div>
            <div className="nd-knowledge-form">
              {studio.isError ? <ErrorSummary title="同步状态不可用" errors={[errorCopy(studio.error, '请稍后重试')]} /> : (
                <dl className="nd-knowledge-metrics">
                  {(studio.data?.kpis ?? []).slice(0, 4).map((item) => <div key={item.key}><dt>{item.label}</dt><dd>{item.value}</dd></div>)}
                  {!studio.data?.kpis?.length ? <div><dt>知识条目</dt><dd>{items.data?.total ?? 0}</dd></div> : null}
                </dl>
              )}
            </div>
          </section>
        </aside>
      </section>

      <ConfirmDialog
        open={discardDraftOpen}
        title="放弃未保存的修改？"
        description="当前知识草稿还没有保存。继续后，这些修改将不会被保留。"
        confirmLabel="放弃修改"
        destructive
        onOpenChange={(open) => {
          setDiscardDraftOpen(open)
          if (!open) pendingDraftActionRef.current = null
        }}
        onConfirm={confirmDraftDiscard}
      />
      <ConfirmDialog
        open={publishReviewOpen}
        title="审核并发布知识"
        description="确认适用对象和答案事实后再发布。发布请求成功不代表知识同步已经完成。"
        confirmLabel="确认发布"
        busy={busy}
        onOpenChange={setPublishReviewOpen}
        onConfirm={confirmPublication}
      >
        <dl className="nd-knowledge-review">
          <div><dt>知识标题</dt><dd>{sanitizeDisplayText(draft.title || '未填写')}</dd></div>
          <div><dt>客户问题</dt><dd>{sanitizeDisplayText(draft.fact_question || '未填写')}</dd></div>
          <div><dt>答案事实</dt><dd>{sanitizeDisplayText(draft.fact_answer || '未填写')}</dd></div>
          <div><dt>适用对象</dt><dd>{draft.audience_scope === 'internal' ? '内部参考' : '客户问答'}</dd></div>
          <div><dt>渠道</dt><dd>{draft.channel === 'all' ? '全部渠道' : sanitizeDisplayText(draft.channel)}</dd></div>
          <div><dt>语言</dt><dd>{sanitizeDisplayText(draft.language || '自动匹配')}</dd></div>
        </dl>
        <p>发布后，知识同步完成才会影响后续客服处理；本确认不表示已有客户回复已经更新。</p>
      </ConfirmDialog>
    </main>
  )
}

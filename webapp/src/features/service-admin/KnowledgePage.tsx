import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { EmptyState } from '@/components/ui/EmptyState'
import { ErrorSummary } from '@/components/ui/ErrorSummary'
import { Field, Input, Select, Textarea } from '@/components/ui/Field'
import { ServiceAppShell } from '@/components/layout/ServiceAppShell'
import { useLogout, useSession } from '@/hooks/useAuth'
import { sanitizeDisplayText } from '@/lib/format'
import { supportApi } from '@/lib/supportApi'
import type { KnowledgeItem } from '@/lib/types'

interface KnowledgeDraft {
  item_key: string
  title: string
  question: string
  answer: string
  aliases: string
  summary: string
  status: string
  channel: string
  language: string
  priority: string
  kind: string
}

function createDraft(): KnowledgeDraft {
  return {
    item_key: `support.customer.${Date.now().toString(36)}`,
    title: '',
    question: '',
    answer: '',
    aliases: '',
    summary: '',
    status: 'draft',
    channel: 'all',
    language: '',
    priority: '100',
    kind: 'business_fact',
  }
}

function draftFromItem(item: KnowledgeItem): KnowledgeDraft {
  return {
    item_key: item.item_key,
    title: item.title || '',
    question: item.fact_question || '',
    answer: item.fact_answer || item.draft_body || item.published_body || '',
    aliases: (item.fact_aliases_json || []).join('\n'),
    summary: item.summary || '',
    status: item.status || 'draft',
    channel: item.channel || 'all',
    language: item.language || '',
    priority: String(item.priority ?? 100),
    kind: item.knowledge_kind || 'business_fact',
  }
}

function serializeDraft(draft: KnowledgeDraft) {
  return JSON.stringify(draft)
}

function payloadFromDraft(draft: KnowledgeDraft): Partial<KnowledgeItem> {
  const aliases = draft.aliases.split(/\r?\n/).map((value) => value.trim()).filter(Boolean).slice(0, 50)
  const question = draft.question.trim()
  const answer = draft.answer.trim()
  const body = [
    question ? `Customer question: ${question}` : '',
    answer ? `Answer guidance: ${answer}` : '',
    aliases.length ? `Alternative wording:\n${aliases.map((value) => `- ${value}`).join('\n')}` : '',
  ].filter(Boolean).join('\n\n')
  return {
    item_key: draft.item_key.trim(),
    title: draft.title.trim(),
    summary: draft.summary.trim() || null,
    status: draft.status,
    source_type: 'text',
    knowledge_kind: draft.kind,
    channel: draft.channel === 'all' ? null : draft.channel,
    audience_scope: 'customer',
    language: draft.language.trim() || null,
    priority: Math.max(0, Math.min(10000, Number.parseInt(draft.priority, 10) || 100)),
    fact_question: question || null,
    fact_answer: answer || null,
    fact_aliases_json: aliases.length ? aliases : null,
    fact_status: draft.status === 'active' ? 'approved' : 'draft',
    answer_mode: 'guided_answer',
    draft_body: body || answer || question || null,
    draft_normalized_text: body || answer || question || null,
  }
}

export function KnowledgePage() {
  const client = useQueryClient()
  const session = useSession()
  const logout = useLogout()
  const capabilities = useMemo(() => new Set(session.data?.capabilities ?? []), [session.data?.capabilities])
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState('all')
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [draft, setDraft] = useState<KnowledgeDraft>(() => createDraft())
  const [baseline, setBaseline] = useState(() => serializeDraft(draft))
  const [publishOpen, setPublishOpen] = useState(false)
  const [discardOpen, setDiscardOpen] = useState(false)
  const pendingNavigationRef = useRef<(() => void) | null>(null)
  const [testQuestion, setTestQuestion] = useState('')

  useEffect(() => { document.title = '知识与规则 · Nexus 客服中心' }, [])

  const canRead = capabilities.has('ai_config.read') || capabilities.has('ai_config.manage')
  const canManage = capabilities.has('ai_config.manage')
  const items = useQuery({
    queryKey: ['serviceKnowledge', query, status],
    queryFn: () => supportApi.knowledgeItems({ q: query, status: status === 'all' ? undefined : status }),
    enabled: Boolean(session.data && canRead),
    retry: false,
  })

  const selectedItem = items.data?.items.find((item) => item.id === selectedId) ?? null
  const dirty = serializeDraft(draft) !== baseline

  const runWithDraftGuard = (proceed: () => void) => {
    if (!dirty) {
      proceed()
      return
    }
    pendingNavigationRef.current = proceed
    setDiscardOpen(true)
  }

  const confirmDiscard = () => {
    const proceed = pendingNavigationRef.current
    pendingNavigationRef.current = null
    setDiscardOpen(false)
    proceed?.()
  }

  useEffect(() => {
    if (!selectedId && items.data?.items.length) setSelectedId(items.data.items[0].id)
  }, [items.data?.items, selectedId])

  useEffect(() => {
    if (!selectedItem) return
    const next = draftFromItem(selectedItem)
    setDraft(next)
    setBaseline(serializeDraft(next))
  }, [selectedItem])

  useEffect(() => {
    if (!dirty) return
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  const saveMutation = useMutation({
    mutationFn: async () => {
      const payload = payloadFromDraft(draft)
      if (selectedItem) return supportApi.updateKnowledgeItem(selectedItem.id, payload)
      return supportApi.createKnowledgeItem(payload)
    },
    onSuccess: async (saved) => {
      await client.invalidateQueries({ queryKey: ['serviceKnowledge'] })
      setSelectedId(saved.id)
      const next = draftFromItem(saved)
      setDraft(next)
      setBaseline(serializeDraft(next))
    },
  })

  const publishMutation = useMutation({
    mutationFn: () => {
      if (!selectedItem) throw new Error('请先保存知识草稿')
      return supportApi.publishKnowledgeItem(selectedItem.id, 'Reviewed and published from customer-service knowledge page')
    },
    onSuccess: async () => {
      setPublishOpen(false)
      await client.invalidateQueries({ queryKey: ['serviceKnowledge'] })
    },
  })

  const testMutation = useMutation({
    mutationFn: () => supportApi.testKnowledgeRetrieval({ q: testQuestion.trim(), audience_scope: 'customer', limit: 5 }),
  })

  if (!session.data && session.isLoading) return <main className="service-entry-state"><EmptyState title="正在验证账号" description="正在加载知识权限。" /></main>
  if (!session.data || session.isError) return <main className="service-entry-state"><ErrorSummary title="无法读取当前账号" errors={['请重新登录']} /></main>

  return (
    <ServiceAppShell
      active="knowledge"
      userName={session.data.display_name || session.data.username}
      capabilities={capabilities}
      title="知识与规则"
      description="维护客服可引用的事实、政策和处理流程。内容必须清晰、可核实、可执行。"
      meta={<span>{items.data?.total ?? 0} 条知识</span>}
      onLogout={logout}
      onNavigateRequest={runWithDraftGuard}
    >
      <div className="system-page">
        {!canRead ? <EmptyState title="当前账号不能查看知识" description="请联系管理员补充知识查看权限。" /> : null}
        {canRead ? (
          <div className="knowledge-layout">
            <aside className="knowledge-list-panel">
              <div className="workspace-section-heading">
                <div><h2>客服知识</h2><p>按客户问题查找和维护。</p></div>
                {canManage ? <Button variant="primary" onClick={() => runWithDraftGuard(() => { const next = createDraft(); setSelectedId(null); setDraft(next); setBaseline(serializeDraft(next)) })}>新建</Button> : null}
              </div>
              <div className="knowledge-filters">
                <Field label="搜索">
                  <Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="问题、标题或关键词" />
                </Field>
                <Field label="状态">
                  <Select value={status} onChange={(event) => setStatus(event.target.value)}>
                    <option value="all">全部</option>
                    <option value="active">已发布</option>
                    <option value="draft">草稿</option>
                    <option value="archived">已归档</option>
                  </Select>
                </Field>
              </div>
              {items.isError ? <ErrorSummary title="知识列表不可用" errors={[items.error instanceof Error ? items.error.message : '请稍后重试']} /> : null}
              <div className="knowledge-items">
                {(items.data?.items ?? []).map((item) => (
                  <button key={item.id} type="button" className={selectedId === item.id ? 'is-selected' : ''} onClick={() => runWithDraftGuard(() => setSelectedId(item.id))}>
                    <span><strong>{sanitizeDisplayText(item.title)}</strong><Badge tone={item.status === 'active' ? 'success' : item.status === 'draft' ? 'warning' : 'default'}>{item.status === 'active' ? '已发布' : item.status === 'draft' ? '草稿' : '已归档'}</Badge></span>
                    <small>{sanitizeDisplayText(item.fact_question || item.summary || '未填写客户问题')}</small>
                  </button>
                ))}
                {!items.isLoading && !items.data?.items.length ? <EmptyState title="没有匹配的知识" description="调整搜索条件或新建一条客服知识。" /> : null}
              </div>
            </aside>

            <section className="knowledge-editor-panel">
              <div className="workspace-section-heading">
                <div><h2>{selectedItem ? '编辑知识' : '新建知识'}</h2><p>先写客户怎么问，再写客服应该依据什么回答。</p></div>
                {dirty ? <Badge tone="warning">未保存</Badge> : <Badge tone="success">已保存</Badge>}
              </div>

              <div className="knowledge-editor-grid">
                <Field label="标题" required>
                  <Input value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} disabled={!canManage} />
                </Field>
                <Field label="分类">
                  <Select value={draft.kind} onChange={(event) => setDraft({ ...draft, kind: event.target.value })} disabled={!canManage}>
                    <option value="business_fact">客服问答</option>
                    <option value="faq">常见问题</option>
                    <option value="policy">规则政策</option>
                    <option value="sop">处理流程</option>
                    <option value="document">资料文档</option>
                  </Select>
                </Field>
                <Field label="客户会怎么问" required description="用客户真实表达方式写问题。">
                  <Textarea value={draft.question} onChange={(event) => setDraft({ ...draft, question: event.target.value })} rows={3} disabled={!canManage} />
                </Field>
                <Field label="客服应依据的答案" required description="写事实、条件和边界，不写空泛话术。">
                  <Textarea value={draft.answer} onChange={(event) => setDraft({ ...draft, answer: event.target.value })} rows={7} disabled={!canManage} />
                </Field>
                <Field label="同义问法" hint="每行一个，帮助系统匹配不同表达。">
                  <Textarea value={draft.aliases} onChange={(event) => setDraft({ ...draft, aliases: event.target.value })} rows={4} disabled={!canManage} />
                </Field>
                <Field label="内部摘要">
                  <Textarea value={draft.summary} onChange={(event) => setDraft({ ...draft, summary: event.target.value })} rows={3} disabled={!canManage} />
                </Field>
                <div className="knowledge-editor-columns">
                  <Field label="渠道">
                    <Select value={draft.channel} onChange={(event) => setDraft({ ...draft, channel: event.target.value })} disabled={!canManage}>
                      <option value="all">全部渠道</option>
                      <option value="webchat">网页客服</option>
                      <option value="whatsapp">WhatsApp</option>
                      <option value="email">邮件</option>
                    </Select>
                  </Field>
                  <Field label="语言">
                    <Input value={draft.language} onChange={(event) => setDraft({ ...draft, language: event.target.value })} placeholder="zh-CN" disabled={!canManage} />
                  </Field>
                  <Field label="优先级" hint="数字越小越优先。">
                    <Input type="number" value={draft.priority} onChange={(event) => setDraft({ ...draft, priority: event.target.value })} disabled={!canManage} />
                  </Field>
                </div>
              </div>

              {saveMutation.error ? <ErrorSummary title="保存失败" errors={[saveMutation.error instanceof Error ? saveMutation.error.message : '请稍后重试']} /> : null}
              {publishMutation.error ? <ErrorSummary title="发布失败" errors={[publishMutation.error instanceof Error ? publishMutation.error.message : '请稍后重试']} /> : null}
              <div className="service-action-buttons">
                <Button variant="primary" disabled={!canManage || !dirty || !draft.title.trim() || !draft.question.trim() || !draft.answer.trim()} loading={saveMutation.isPending} loadingLabel="保存中…" onClick={() => saveMutation.mutate()}>保存草稿</Button>
                <Button variant="secondary" disabled={!canManage || !selectedItem || dirty || selectedItem.status === 'active'} onClick={() => setPublishOpen(true)}>审核并发布</Button>
              </div>

              <section className="knowledge-test-panel">
                <div><h3>测试知识匹配</h3><p>输入一个客户问题，确认是否能找到正确知识。</p></div>
                <Field label="客户问题">
                  <Input value={testQuestion} onChange={(event) => { testMutation.reset(); setTestQuestion(event.target.value) }} placeholder="例如：我的包裹为什么还没到？" />
                </Field>
                <Button variant="secondary" disabled={!testQuestion.trim()} loading={testMutation.isPending} loadingLabel="测试中…" onClick={() => testMutation.mutate()}>开始测试</Button>
                {testMutation.data ? (
                  <div className="knowledge-test-results" role="status">
                    <strong>找到 {testMutation.data.total} 条匹配结果</strong>
                    {testMutation.data.hits.slice(0, 5).map((hit) => <p key={`${hit.item_id}-${hit.chunk_index}`}><b>{sanitizeDisplayText(hit.title)}</b><span>{sanitizeDisplayText(hit.direct_answer || hit.text)}</span></p>)}
                  </div>
                ) : null}
              </section>
            </section>
          </div>
        ) : null}
      </div>

      <ConfirmDialog
        open={discardOpen}
        title="放弃未保存的知识修改？"
        description="切换知识、页面或退出后，当前修改不会保留。"
        confirmLabel="放弃修改"
        destructive
        onOpenChange={(open) => {
          setDiscardOpen(open)
          if (!open) pendingNavigationRef.current = null
        }}
        onConfirm={confirmDiscard}
      />

      <ConfirmDialog
        open={publishOpen}
        title="发布这条客服知识？"
        description="发布后，客服查询和自动答复流程可以使用这条内容。请确认事实、条件和适用范围已经审核。"
        confirmLabel="确认发布"
        onOpenChange={setPublishOpen}
        onConfirm={() => publishMutation.mutate()}
      />
    </ServiceAppShell>
  )
}

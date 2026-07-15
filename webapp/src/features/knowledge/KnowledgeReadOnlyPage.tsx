import { useDeferredValue, useMemo, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/EmptyState'
import { ErrorSummary } from '@/components/ui/ErrorSummary'
import { Field, Input, Select } from '@/components/ui/Field'
import { sanitizeDisplayText } from '@/lib/format'
import { supportApi } from '@/lib/supportApi'
import { knowledgeStatusPresentation } from '@/lib/supportStatus'
import type { KnowledgeItem } from '@/lib/types'
import './knowledge.css'

type KnowledgeStatusFilter = 'active' | 'draft' | 'archived' | 'all'
type KnowledgeKindFilter = 'all' | 'business_fact' | 'faq' | 'policy' | 'sop' | 'document'

const kinds: Array<{ value: KnowledgeKindFilter; label: string }> = [
  { value: 'all', label: '全部分类' },
  { value: 'business_fact', label: '客服问答' },
  { value: 'faq', label: '常见问题' },
  { value: 'policy', label: '规则政策' },
  { value: 'sop', label: '处理流程' },
  { value: 'document', label: '资料文档' },
]

function errorCopy(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback
}

function kindLabel(value: string | null | undefined) {
  return kinds.find((item) => item.value === value)?.label ?? sanitizeDisplayText(value || '知识')
}

function KnowledgeDetail({ item }: { item: KnowledgeItem }) {
  const status = knowledgeStatusPresentation(item.status)
  return (
    <section className="nd-knowledge-editor" aria-labelledby="knowledge-readonly-title">
      <div className="nd-knowledge-panel-head">
        <h2 id="knowledge-readonly-title">知识详情</h2>
        <Badge tone={status.tone}>{status.label}</Badge>
      </div>
      <div className="nd-knowledge-form">
        <dl className="nd-knowledge-review">
          <div><dt>标题</dt><dd>{sanitizeDisplayText(item.title)}</dd></div>
          <div><dt>类型</dt><dd>{kindLabel(item.knowledge_kind)}</dd></div>
          <div><dt>客户问题</dt><dd>{sanitizeDisplayText(item.fact_question || '未提供')}</dd></div>
          <div><dt>答案事实</dt><dd>{sanitizeDisplayText(item.fact_answer || item.published_body || item.draft_body || '未提供')}</dd></div>
          <div><dt>适用对象</dt><dd>{item.audience_scope === 'internal' ? '内部参考' : '客户问答'}</dd></div>
          <div><dt>渠道</dt><dd>{sanitizeDisplayText(item.channel || '全部渠道')}</dd></div>
          <div><dt>语言</dt><dd>{sanitizeDisplayText(item.language || '自动匹配')}</dd></div>
          <div><dt>版本</dt><dd>v{item.published_version || 0}</dd></div>
        </dl>
        <p className="nd-knowledge-guidance">当前账号只有读取权限。新建、保存、发布和归档需要 <code>ai_config.manage</code>。</p>
      </div>
    </section>
  )
}

export function KnowledgeReadOnlyPage() {
  const [search, setSearch] = useState('')
  const deferredSearch = useDeferredValue(search)
  const [statusFilter, setStatusFilter] = useState<KnowledgeStatusFilter>('active')
  const [kindFilter, setKindFilter] = useState<KnowledgeKindFilter>('all')
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [retrievalQuery, setRetrievalQuery] = useState('')

  const items = useQuery({
    queryKey: ['canonicalKnowledgeItems', deferredSearch, statusFilter, kindFilter],
    queryFn: () => supportApi.knowledgeItems({
      q: deferredSearch,
      status: statusFilter === 'all' ? undefined : statusFilter,
      knowledge_kind: kindFilter === 'all' ? undefined : kindFilter,
    }),
    retry: false,
    refetchInterval: 30_000,
  })
  const selectedItem = useMemo(() => {
    const available = items.data?.items ?? []
    return available.find((item) => item.id === selectedId) ?? available[0] ?? null
  }, [items.data?.items, selectedId])

  const retrieval = useMutation({
    mutationFn: () => supportApi.testKnowledgeRetrieval({
      q: retrievalQuery.trim(),
      channel: selectedItem?.channel || null,
      audience_scope: selectedItem?.audience_scope || 'customer',
      language: selectedItem?.language || null,
      limit: 5,
    }),
  })

  return (
    <main className="nd-knowledge-page">
      <header className="nd-knowledge-header">
        <div><h1>知识与处理规则</h1><p>查看经过审核的客户问答、规则政策和处理流程。当前页面为只读投影。</p></div>
        {items.isFetching ? <Badge>正在刷新</Badge> : <Badge>只读</Badge>}
      </header>
      <section className="nd-knowledge-layout" aria-label="知识只读工作区">
        <aside className="nd-knowledge-list" aria-labelledby="knowledge-list-title">
          <div className="nd-knowledge-panel-head"><h2 id="knowledge-list-title">知识列表</h2></div>
          <div className="nd-knowledge-filters">
            <Field label="搜索知识"><Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="标题、问题或答案关键字" /></Field>
            <Field label="状态"><Select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value as KnowledgeStatusFilter)}><option value="active">已上线</option><option value="draft">草稿</option><option value="archived">已归档</option><option value="all">全部</option></Select></Field>
            <Field label="分类"><Select value={kindFilter} onChange={(event) => setKindFilter(event.target.value as KnowledgeKindFilter)}>{kinds.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</Select></Field>
          </div>
          {items.isError ? <ErrorSummary title="知识列表不可用" errors={[errorCopy(items.error, '请稍后重试')]} /> : (
            <div className="nd-knowledge-items">
              {(items.data?.items ?? []).map((item) => {
                const presentation = knowledgeStatusPresentation(item.status)
                return <button type="button" key={item.id} className={selectedItem?.id === item.id ? 'is-active' : ''} aria-pressed={selectedItem?.id === item.id} onClick={() => setSelectedId(item.id)}><span><strong>{sanitizeDisplayText(item.title)}</strong><small>{sanitizeDisplayText(item.fact_question || item.summary || item.item_key)}</small></span><span><Badge tone={presentation.tone}>{presentation.label}</Badge><small>{kindLabel(item.knowledge_kind)} · v{item.published_version || 0}</small></span></button>
              })}
              {!items.data?.items?.length ? <EmptyState title="没有找到知识" description="调整搜索条件后重试。" /> : null}
            </div>
          )}
        </aside>
        {selectedItem ? <KnowledgeDetail item={selectedItem} /> : <section className="nd-knowledge-editor"><EmptyState title="选择一条知识" description="从列表选择知识查看完整事实。" /></section>}
        <aside className="nd-knowledge-side" aria-label="知识只读验证">
          <section>
            <div className="nd-knowledge-panel-head"><h2>测试命中</h2>{retrieval.isPending ? <Badge>测试中</Badge> : null}</div>
            <div className="nd-knowledge-form">
              <Field label="用一句客户问题测试"><Input value={retrievalQuery} onChange={(event) => setRetrievalQuery(event.target.value)} placeholder="例如：包裹末派失败怎么办？" /></Field>
              <Button disabled={!retrievalQuery.trim() || retrieval.isPending} onClick={() => retrieval.mutate()}>测试知识命中</Button>
              {retrieval.error ? <ErrorSummary title="测试失败" errors={[errorCopy(retrieval.error, '请稍后重试')]} /> : null}
              {retrieval.data ? <div className="nd-knowledge-hits"><strong>{retrieval.data.hits.length ? `命中 ${retrieval.data.hits.length} 条知识` : '没有命中知识'}</strong>{retrieval.data.hits.slice(0, 5).map((hit) => <article key={`${hit.item_id}-${hit.chunk_index}`}><span>{sanitizeDisplayText(hit.title)}</span><p>{sanitizeDisplayText(hit.direct_answer || hit.text).slice(0, 260)}</p></article>)}</div> : null}
            </div>
          </section>
        </aside>
      </section>
    </main>
  )
}

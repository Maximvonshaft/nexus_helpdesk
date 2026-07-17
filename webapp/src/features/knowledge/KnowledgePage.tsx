import AddRoundedIcon from '@mui/icons-material/AddRounded'
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Divider,
  List,
  ListItemButton,
  MenuItem,
  Paper,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import { useDeferredValue, useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  OperatorEmptyState,
  OperatorErrorNotice,
  OperatorFactGrid,
  operatorScrollBehavior,
  operatorToneColor,
} from '@/app/OperatorPresentation'
import { sanitizeDisplayText } from '@/lib/format'
import { supportApi } from '@/lib/supportApi'
import { knowledgeStatusPresentation } from '@/lib/supportStatus'
import type { KnowledgeItem } from '@/lib/types'

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

const knowledgeKindOptions: Array<{ value: KnowledgeKindFilter; label: string }> = [
  { value: 'all', label: '全部分类' },
  { value: 'business_fact', label: '客服问答' },
  { value: 'faq', label: '常见问题' },
  { value: 'policy', label: '规则政策' },
  { value: 'sop', label: '处理流程' },
  { value: 'document', label: '资料文档' },
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
    .slice(0, 120);
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

function KnowledgeDetail({ item }: { item: KnowledgeItem }) {
  const status = knowledgeStatusPresentation(item.status)
  return (
    <Paper component="section" variant="outlined" aria-labelledby="knowledge-detail-title" sx={{ minWidth: 0, p: { xs: 2, md: 2.5 } }}>
      <Stack
        direction="row"
        spacing={2}
        sx={{
          alignItems: "flex-start",
          justifyContent: "space-between"
        }}>
        <Typography id="knowledge-detail-title" component="h2" variant="h3">知识详情</Typography>
        <Chip color={operatorToneColor(status.tone)} label={status.label} />
      </Stack>
      <Divider sx={{ my: 2 }} />
      <OperatorFactGrid
        columns={2}
        facts={[
          ['标题', sanitizeDisplayText(item.title)],
          ['类型', knowledgeKindLabel(item.knowledge_kind)],
          ['客户问题', sanitizeDisplayText(item.fact_question || '未提供')],
          ['标准答案', sanitizeDisplayText(item.fact_answer || item.published_body || item.draft_body || '未提供')],
          ['适用对象', item.audience_scope === 'internal' ? '内部参考' : '客户问答'],
          ['渠道', sanitizeDisplayText(item.channel || '全部渠道')],
          ['语言', sanitizeDisplayText(item.language || '自动匹配')],
          ['版本', `v${item.published_version || 0}`],
        ]}
      />
      <Alert severity="info" variant="outlined" sx={{ mt: 2.5 }}>只读权限</Alert>
    </Paper>
  );
}

export function KnowledgePage({ canManage }: { canManage: boolean }) {
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
  const isDirty = canManage && serializeKnowledgeDraft(draft) !== serializeKnowledgeDraft(savedDraft)

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
  const selectedItem = useMemo(() => {
    const available = items.data?.items ?? []
    return available.find((item) => item.id === selectedId) ?? available[0] ?? null
  }, [items.data?.items, selectedId])

  useEffect(() => {
    if (!selectedItem || isCreating || loadedItemIdRef.current === selectedItem.id) return
    loadedItemIdRef.current = selectedItem.id
    if (selectedId !== selectedItem.id) setSelectedId(selectedItem.id)
    if (!canManage) return
    const nextDraft = knowledgeDraftFromItem(selectedItem)
    setDraft(nextDraft)
    setSavedDraft(nextDraft)
    setSavedMessage('')
  }, [canManage, isCreating, selectedId, selectedItem])

  useEffect(() => {
    if (!isDirty) return
    const warnBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', warnBeforeUnload)
    return () => window.removeEventListener('beforeunload', warnBeforeUnload)
  }, [isDirty])

  const scrollEditorIntoView = () => {
    window.setTimeout(() => {
      if (window.matchMedia('(max-width: 980px)').matches) {
        editorRef.current?.scrollIntoView({ block: 'start', behavior: operatorScrollBehavior() })
      }
    }, 0)
  }

  const runWithDraftGuard = (action: () => void) => {
    if (!isDirty) return action()
    pendingDraftActionRef.current = action
    setDiscardDraftOpen(true)
  }

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

  const invalidateKnowledge = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['canonicalKnowledgeStatus'] }),
      queryClient.invalidateQueries({ queryKey: ['canonicalKnowledgeItems'] }),
    ])
  }

  const saveMutation = useMutation({
    mutationFn: async (publish: boolean) => {
      if (!canManage) throw new Error('当前账号没有编辑权限')
      const payload = knowledgePayloadFromDraft(draft)
      if (!payload.title) throw new Error('请填写知识标题')
      if (!payload.fact_question && !payload.fact_answer && !payload.draft_body) throw new Error('请填写客户问题或标准答案')
      let item: KnowledgeItem
      if (selectedId && !isCreating) item = await supportApi.updateKnowledgeItem(selectedId, payload)
      else item = await supportApi.createKnowledgeItem({ ...payload, item_key: normalizeKnowledgeKey(draft.item_key) || createKnowledgeKey() })
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
      setSavedMessage(publish ? '已提交发布，等待同步。' : '草稿已保存。')
      await invalidateKnowledge()
    },
  })

  const publishMutation = useMutation({
    mutationFn: async () => {
      if (!canManage) throw new Error('当前账号没有发布权限')
      if (!selectedId) throw new Error('请先选择一条知识')
      await supportApi.publishKnowledgeItem(selectedId, 'canonical knowledge publish')
      return supportApi.updateKnowledgeItem(selectedId, { status: 'active', fact_status: 'approved' })
    },
    onSuccess: async (item) => {
      const committedDraft = knowledgeDraftFromItem(item)
      loadedItemIdRef.current = item.id
      setDraft(committedDraft)
      setSavedDraft(committedDraft)
      setPublishReviewOpen(false)
      setSavedMessage('已提交发布，等待同步。')
      await invalidateKnowledge()
    },
  })

  const retrievalMutation = useMutation({
    mutationFn: () => supportApi.testKnowledgeRetrieval({
      q: retrievalQuery.trim(),
      channel: selectedItem?.channel || null,
      audience_scope: selectedItem?.audience_scope || 'customer',
      language: selectedItem?.language || null,
    }),
  })

  const retrievalHits = retrievalMutation.data?.hits ?? []
  const busy = saveMutation.isPending || publishMutation.isPending
  const saveError = saveMutation.error || publishMutation.error
  const publicationReady = Boolean(draft.title.trim() && (draft.fact_question.trim() || draft.fact_answer.trim()))
  const statusPresentation = knowledgeStatusPresentation(draft.status)

  const confirmPublication = () => {
    if (isDirty || isCreating) saveMutation.mutate(true)
    else publishMutation.mutate()
  }

  return (
    <Box component="main" sx={{ p: { xs: 1.5, md: 2.5 } }}>
      <Stack
        direction={{ xs: 'column', sm: 'row' }}
        spacing={2}
        sx={{
          alignItems: { xs: 'stretch', sm: 'flex-start' },
          justifyContent: "space-between",
          mb: 2.5
        }}>
        <Typography component="h1" variant="h1">知识与流程</Typography>
        <Stack direction="row" spacing={1} sx={{
          alignItems: "center"
        }}>
          {!canManage ? <Chip label="只读" /> : null}
          {items.isFetching ? <CircularProgress size={22} aria-label="正在刷新" /> : null}
        </Stack>
      </Stack>
      <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', xl: 'minmax(270px, 320px) minmax(0, 1fr) minmax(280px, 340px)' } }} aria-label="知识管理">
        <Paper component="aside" variant="outlined" aria-labelledby="knowledge-list-title" sx={{ minWidth: 0, p: 1.5 }}>
          <Stack
            direction="row"
            spacing={1}
            sx={{
              alignItems: "center",
              justifyContent: "space-between"
            }}>
            <Typography id="knowledge-list-title" component="h2" variant="h3">知识列表</Typography>
            {canManage ? <Button variant="contained" size="small" startIcon={<AddRoundedIcon />} onClick={resetForNew}>新建</Button> : null}
          </Stack>
          <Stack spacing={1.25} sx={{ mt: 2 }}>
            <TextField label="搜索" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="标题、问题或答案" autoComplete="off" />
            <TextField select label="状态" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value as KnowledgeStatusFilter)}>
              <MenuItem value="active">已上线</MenuItem>
              <MenuItem value="draft">草稿</MenuItem>
              <MenuItem value="archived">已归档</MenuItem>
              <MenuItem value="all">全部</MenuItem>
            </TextField>
            <TextField select label="分类" value={kindFilter} onChange={(event) => setKindFilter(event.target.value as KnowledgeKindFilter)}>
              {knowledgeKindOptions.map((item) => <MenuItem key={item.value} value={item.value}>{item.label}</MenuItem>)}
            </TextField>
          </Stack>
          <Divider sx={{ mt: 2 }} />
          {items.isError ? (
            <Box sx={{ mt: 2 }}><OperatorErrorNotice title="无法读取知识列表" error={items.error} fallback="请稍后重试" /></Box>
          ) : (
            <List disablePadding sx={{ mt: 1, maxHeight: { xl: 'calc(100dvh - 390px)' }, overflowY: 'auto' }}>
              {(items.data?.items ?? []).map((item) => {
                const presentation = knowledgeStatusPresentation(item.status)
                return (
                  <ListItemButton
                    component="button"
                    selected={selectedItem?.id === item.id && !isCreating}
                    key={item.id}
                    aria-pressed={selectedItem?.id === item.id && !isCreating}
                    onClick={() => selectKnowledgeItem(item.id)}
                    sx={{ borderBottom: 1, borderColor: 'divider', display: 'block', px: 1.25, py: 1.25, textAlign: 'left', width: '100%' }}
                  >
                    <Stack spacing={0.75}>
                      <Typography variant="subtitle2">{sanitizeDisplayText(item.title)}</Typography>
                      <Typography variant="caption" sx={{
                        color: "text.secondary"
                      }}>{sanitizeDisplayText(item.fact_question || item.summary || item.item_key)}</Typography>
                      <Stack
                        direction="row"
                        spacing={1}
                        sx={{
                          alignItems: "center",
                          justifyContent: "space-between"
                        }}>
                        <Chip color={operatorToneColor(presentation.tone)} label={presentation.label} />
                        <Typography variant="caption" sx={{
                          color: "text.secondary"
                        }}>{knowledgeKindLabel(item.knowledge_kind)} · v{item.published_version || 0}</Typography>
                      </Stack>
                    </Stack>
                  </ListItemButton>
                );
              })}
              {!items.data?.items?.length ? <OperatorEmptyState title="没有找到知识" description={canManage ? '请调整筛选或新建知识' : '请调整筛选'} /> : null}
            </List>
          )}
        </Paper>

        {canManage ? (
          <Paper component="section" ref={editorRef} variant="outlined" aria-labelledby="knowledge-editor-title" sx={{ minWidth: 0, p: { xs: 2, md: 2.5 } }}>
            <Stack
              direction="row"
              spacing={2}
              sx={{
                alignItems: "flex-start",
                justifyContent: "space-between"
              }}>
              <Typography id="knowledge-editor-title" component="h2" variant="h3">{selectedId && !isCreating ? '编辑知识' : '新建知识'}</Typography>
              <Chip color={operatorToneColor(statusPresentation.tone)} label={statusPresentation.label} />
            </Stack>
            <Divider sx={{ my: 2 }} />
            <Stack spacing={1.75}>
              {saveError ? <OperatorErrorNotice title="保存失败" error={saveError} fallback="请稍后重试" /> : null}
              {savedMessage ? <Alert severity="success" variant="outlined" role="status" aria-live="polite">{savedMessage}</Alert> : null}
              <TextField label="知识标题" required value={draft.title} onChange={(event) => setDraft((current) => ({ ...current, title: event.target.value }))} placeholder="例如：派送失败处理" autoComplete="off" />
              {isCreating || !selectedId ? <TextField label="知识编号" helperText="系统编号" value={draft.item_key} onChange={(event) => setDraft((current) => ({ ...current, item_key: normalizeKnowledgeKey(event.target.value) }))} autoComplete="off" spellCheck={false} /> : null}
              <TextField label="客户问题" required helperText="填写客户原话。" value={draft.fact_question} onChange={(event) => setDraft((current) => ({ ...current, fact_question: event.target.value }))} multiline minRows={4} placeholder="例如：我的包裹显示派送失败怎么办？" autoComplete="off" />
              <TextField label="标准答案与处理步骤" required helperText="填写核实后的答案和处理步骤。" value={draft.fact_answer} onChange={(event) => setDraft((current) => ({ ...current, fact_answer: event.target.value }))} multiline minRows={8} placeholder="例如：确认运单号和收件电话，核实地址与联系方式，必要时转人工处理。" autoComplete="off" />
              <TextField label="其他问法" helperText="每行一个。" value={draft.fact_aliases} onChange={(event) => setDraft((current) => ({ ...current, fact_aliases: event.target.value }))} multiline minRows={4} placeholder={'包裹派送失败\n快递没有送到\n末端配送异常'} autoComplete="off" />
              <Box sx={{ display: 'grid', gap: 1.5, gridTemplateColumns: { xs: '1fr', md: 'repeat(2, minmax(0, 1fr))' } }}>
                <TextField select label="适用对象" value={draft.audience_scope} onChange={(event) => setDraft((current) => ({ ...current, audience_scope: event.target.value }))}>
                  <MenuItem value="customer">客户问答</MenuItem><MenuItem value="internal">内部参考</MenuItem>
                </TextField>
                <TextField select label="渠道" value={draft.channel} onChange={(event) => setDraft((current) => ({ ...current, channel: event.target.value }))}>
                  <MenuItem value="all">全部渠道</MenuItem><MenuItem value="webchat">网页客服</MenuItem><MenuItem value="whatsapp">WhatsApp</MenuItem>
                </TextField>
                <TextField label="语言" value={draft.language} onChange={(event) => setDraft((current) => ({ ...current, language: event.target.value }))} placeholder="自动匹配" autoComplete="off" />
                <TextField label="优先级" helperText="数字越小越靠前。" value={draft.priority} type="number" slotProps={{ htmlInput: { min: 0, max: 10000 } }} onChange={(event) => setDraft((current) => ({ ...current, priority: event.target.value }))} />
                <TextField select label="知识类型" value={draft.knowledge_kind} onChange={(event) => setDraft((current) => ({ ...current, knowledge_kind: event.target.value }))}>
                  <MenuItem value="business_fact">客服问答</MenuItem><MenuItem value="faq">常见问题</MenuItem><MenuItem value="policy">规则政策</MenuItem><MenuItem value="sop">处理流程</MenuItem><MenuItem value="document">资料文档</MenuItem>
                </TextField>
                <TextField select label="回复方式" value={draft.answer_mode} onChange={(event) => setDraft((current) => ({ ...current, answer_mode: event.target.value }))}>
                  <MenuItem value="guided_answer">按事实组织回复</MenuItem><MenuItem value="direct_answer">直接使用标准答案</MenuItem>
                </TextField>
              </Box>
              <TextField label="内部备注" value={draft.summary} onChange={(event) => setDraft((current) => ({ ...current, summary: event.target.value }))} multiline minRows={3} placeholder="可选" autoComplete="off" />
              <Stack direction="row" spacing={1} useFlexGap sx={{
                flexWrap: "wrap"
              }}>
                <Button variant="contained" disabled={busy || !isDirty} startIcon={saveMutation.isPending && !publishReviewOpen ? <CircularProgress color="inherit" size={16} /> : undefined} onClick={() => saveMutation.mutate(false)}>
                  {saveMutation.isPending && !publishReviewOpen ? '保存中…' : '保存草稿'}
                </Button>
                <Button variant="outlined" color="inherit" disabled={busy || !publicationReady} onClick={() => setPublishReviewOpen(true)}>发布</Button>
              </Stack>
            </Stack>
          </Paper>
        ) : selectedItem ? <KnowledgeDetail item={selectedItem} /> : <Paper variant="outlined"><OperatorEmptyState title="选择一条知识" description="从列表中选择" /></Paper>}

        <Stack component="aside" spacing={2} aria-label="搜索测试和发布状态" sx={{ minWidth: 0, alignSelf: 'start' }}>
          <Paper variant="outlined" sx={{ p: 2 }}>
            <Stack spacing={1.5}>
              <Stack
                direction="row"
                spacing={1}
                sx={{
                  alignItems: "center",
                  justifyContent: "space-between"
                }}>
                <Typography component="h2" variant="h3">搜索测试</Typography>
                {retrievalMutation.isPending ? <CircularProgress size={18} aria-label="测试中" /> : null}
              </Stack>
              <TextField label="客户问题" value={retrievalQuery} onChange={(event) => setRetrievalQuery(event.target.value)} placeholder="例如：包裹派送失败怎么办？" autoComplete="off" />
              <Button variant="outlined" color="inherit" disabled={!retrievalQuery.trim() || retrievalMutation.isPending} onClick={() => retrievalMutation.mutate()}>测试搜索</Button>
              {retrievalMutation.error ? <OperatorErrorNotice title="测试失败" error={retrievalMutation.error} fallback="请稍后重试" /> : null}
              {retrievalMutation.data ? (
                <Stack spacing={1.25}>
                  <Box>
                    <Typography variant="subtitle2">{retrievalHits.length ? `找到 ${retrievalHits.length} 条` : '未找到结果'}</Typography>
                    <Typography variant="caption" sx={{
                      color: "text.secondary"
                    }}>{retrievalMutation.data.grounding_would_apply ? '可用于回复' : '当前不会用于回复'}</Typography>
                  </Box>
                  {retrievalHits.slice(0, 5).map((hit) => (
                    <Box component="article" key={`${hit.item_id}-${hit.chunk_index}`} sx={{ borderTop: 1, borderColor: 'divider', pt: 1.25 }}>
                      <Typography variant="subtitle2">{sanitizeDisplayText(hit.title)}</Typography>
                      <Typography
                        variant="body2"
                        sx={{
                          color: "text.secondary",
                          mt: 0.5
                        }}>{sanitizeDisplayText(hit.direct_answer || hit.text).slice(0, 260)}</Typography>
                      <Typography variant="caption" sx={{
                        color: "text.disabled"
                      }}>匹配度 {typeof hit.score === 'number' ? hit.score.toFixed(3) : hit.score}</Typography>
                    </Box>
                  ))}
                </Stack>
              ) : null}
            </Stack>
          </Paper>

          <Paper variant="outlined" sx={{ p: 2 }}>
            <Stack
              direction="row"
              spacing={1}
              sx={{
                alignItems: "center",
                justifyContent: "space-between"
              }}>
              <Typography component="h2" variant="h3">发布状态</Typography>
              {studio.isFetching ? <CircularProgress size={18} aria-label="正在刷新" /> : null}
            </Stack>
            {studio.isError ? <Box sx={{ mt: 1.5 }}><OperatorErrorNotice title="无法读取发布状态" error={studio.error} fallback="请稍后重试" /></Box> : (
              <OperatorFactGrid
                columns={2}
                facts={(studio.data?.kpis ?? []).slice(0, 4).length
                  ? (studio.data?.kpis ?? []).slice(0, 4).map((item) => [item.label, item.value])
                  : [['知识条目', items.data?.total ?? 0]]}
              />
            )}
          </Paper>
        </Stack>
      </Box>
      <Dialog open={discardDraftOpen} onClose={() => { setDiscardDraftOpen(false); pendingDraftActionRef.current = null }} aria-labelledby="knowledge-discard-title">
        <DialogTitle id="knowledge-discard-title">放弃未保存的修改？</DialogTitle>
        <DialogContent><DialogContentText>未保存的修改将丢失。</DialogContentText></DialogContent>
        <DialogActions>
          <Button color="inherit" onClick={() => { setDiscardDraftOpen(false); pendingDraftActionRef.current = null }}>继续编辑</Button>
          <Button color="error" variant="contained" onClick={() => { const action = pendingDraftActionRef.current; pendingDraftActionRef.current = null; setDiscardDraftOpen(false); action?.() }}>放弃修改</Button>
        </DialogActions>
      </Dialog>
      <Dialog open={publishReviewOpen} onClose={() => { if (!busy) setPublishReviewOpen(false) }} aria-labelledby="knowledge-publish-title">
        <DialogTitle id="knowledge-publish-title">发布知识</DialogTitle>
        <DialogContent>
          <DialogContentText>请确认以下内容。</DialogContentText>
          <OperatorFactGrid
            columns={1}
            facts={[
              ['知识标题', sanitizeDisplayText(draft.title || '未填写')],
              ['客户问题', sanitizeDisplayText(draft.fact_question || '未填写')],
              ['标准答案', sanitizeDisplayText(draft.fact_answer || '未填写')],
              ['适用对象', draft.audience_scope === 'internal' ? '内部参考' : '客户问答'],
              ['渠道', draft.channel === 'all' ? '全部渠道' : sanitizeDisplayText(draft.channel)],
              ['语言', sanitizeDisplayText(draft.language || '自动匹配')],
            ]}
          />
          <Alert severity="info" variant="outlined" sx={{ mt: 2 }}>提交后等待发布状态更新。</Alert>
        </DialogContent>
        <DialogActions>
          <Button color="inherit" disabled={busy} onClick={() => setPublishReviewOpen(false)}>取消</Button>
          <Button variant="contained" disabled={busy || !publicationReady} startIcon={busy ? <CircularProgress color="inherit" size={16} /> : undefined} onClick={confirmPublication}>
            {busy ? '发布中…' : '确认发布'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}

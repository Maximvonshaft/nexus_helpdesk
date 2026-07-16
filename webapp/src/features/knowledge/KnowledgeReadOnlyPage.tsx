import {
  Alert,
  AlertTitle,
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  List,
  ListItemButton,
  MenuItem,
  Paper,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import { useDeferredValue, useMemo, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { sanitizeDisplayText } from '@/lib/format'
import { supportApi } from '@/lib/supportApi'
import { knowledgeStatusPresentation } from '@/lib/supportStatus'
import type { KnowledgeItem } from '@/lib/types'

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

function statusColor(tone: string) {
  if (tone === 'success') return 'success'
  if (tone === 'warning') return 'warning'
  if (tone === 'danger') return 'error'
  return 'default'
}

function EmptyState({ title, description }: { title: string; description: string }) {
  return (
    <Stack role="status" alignItems="center" justifyContent="center" spacing={0.75} sx={{ minHeight: 150, p: 3, textAlign: 'center' }}>
      <Typography variant="subtitle2">{title}</Typography>
      <Typography variant="body2" color="text.secondary">{description}</Typography>
    </Stack>
  )
}

function KnowledgeDetail({ item }: { item: KnowledgeItem }) {
  const status = knowledgeStatusPresentation(item.status)
  const facts = [
    ['标题', sanitizeDisplayText(item.title)],
    ['类型', kindLabel(item.knowledge_kind)],
    ['客户问题', sanitizeDisplayText(item.fact_question || '未提供')],
    ['标准答案', sanitizeDisplayText(item.fact_answer || item.published_body || item.draft_body || '未提供')],
    ['适用对象', item.audience_scope === 'internal' ? '内部参考' : '客户问答'],
    ['渠道', sanitizeDisplayText(item.channel || '全部渠道')],
    ['语言', sanitizeDisplayText(item.language || '自动匹配')],
    ['版本', `v${item.published_version || 0}`],
  ]

  return (
    <Paper component="section" variant="outlined" aria-labelledby="knowledge-readonly-title" sx={{ minWidth: 0, p: { xs: 2, md: 2.5 } }}>
      <Stack direction="row" spacing={2} alignItems="flex-start" justifyContent="space-between">
        <Typography id="knowledge-readonly-title" component="h2" variant="h3">知识详情</Typography>
        <Chip color={statusColor(status.tone)} label={status.label} />
      </Stack>
      <Divider sx={{ my: 2 }} />
      <Box component="dl" sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', md: 'repeat(2, minmax(0, 1fr))' }, m: 0 }}>
        {facts.map(([label, value]) => (
          <Box key={label} sx={{ minWidth: 0 }}>
            <Typography component="dt" variant="caption" color="text.secondary">{label}</Typography>
            <Typography component="dd" variant="body2" sx={{ m: 0, mt: 0.5, overflowWrap: 'anywhere', whiteSpace: 'pre-wrap' }}>{value}</Typography>
          </Box>
        ))}
      </Box>
      <Alert severity="info" variant="outlined" sx={{ mt: 2.5 }}>只读权限</Alert>
    </Paper>
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
    <Box component="main" sx={{ p: { xs: 1.5, md: 2.5 } }}>
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} alignItems={{ xs: 'stretch', sm: 'flex-start' }} justifyContent="space-between" sx={{ mb: 2.5 }}>
        <Typography component="h1" variant="h1">知识与流程</Typography>
        {items.isFetching ? <CircularProgress size={22} aria-label="正在刷新" /> : <Chip label="只读" />}
      </Stack>

      <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', lg: 'minmax(260px, 320px) minmax(0, 1fr) minmax(260px, 320px)' } }} aria-label="知识查看">
        <Paper component="aside" variant="outlined" aria-labelledby="knowledge-list-title" sx={{ minWidth: 0, p: 1.5 }}>
          <Typography id="knowledge-list-title" component="h2" variant="h3">知识列表</Typography>
          <Stack spacing={1.25} sx={{ mt: 2 }}>
            <TextField label="搜索" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="标题、问题或答案" />
            <TextField select label="状态" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value as KnowledgeStatusFilter)}>
              <MenuItem value="active">已上线</MenuItem><MenuItem value="draft">草稿</MenuItem><MenuItem value="archived">已归档</MenuItem><MenuItem value="all">全部</MenuItem>
            </TextField>
            <TextField select label="分类" value={kindFilter} onChange={(event) => setKindFilter(event.target.value as KnowledgeKindFilter)}>
              {kinds.map((item) => <MenuItem key={item.value} value={item.value}>{item.label}</MenuItem>)}
            </TextField>
          </Stack>
          <Divider sx={{ mt: 2 }} />
          {items.isError ? (
            <Alert severity="error" variant="outlined" sx={{ mt: 2 }}><AlertTitle>无法读取知识列表</AlertTitle>{errorCopy(items.error, '请稍后重试')}</Alert>
          ) : (
            <List disablePadding sx={{ mt: 1, maxHeight: { lg: 'calc(100dvh - 390px)' }, overflowY: 'auto' }}>
              {(items.data?.items ?? []).map((item) => {
                const presentation = knowledgeStatusPresentation(item.status)
                return (
                  <ListItemButton
                    key={item.id}
                    component="button"
                    selected={selectedItem?.id === item.id}
                    aria-pressed={selectedItem?.id === item.id}
                    onClick={() => setSelectedId(item.id)}
                    sx={{ borderBottom: 1, borderColor: 'divider', display: 'block', px: 1.25, py: 1.25, textAlign: 'left', width: '100%' }}
                  >
                    <Stack spacing={0.75}>
                      <Typography variant="subtitle2">{sanitizeDisplayText(item.title)}</Typography>
                      <Typography variant="caption" color="text.secondary">{sanitizeDisplayText(item.fact_question || item.summary || item.item_key)}</Typography>
                      <Stack direction="row" spacing={1} alignItems="center" justifyContent="space-between">
                        <Chip color={statusColor(presentation.tone)} label={presentation.label} />
                        <Typography variant="caption" color="text.secondary">{kindLabel(item.knowledge_kind)} · v{item.published_version || 0}</Typography>
                      </Stack>
                    </Stack>
                  </ListItemButton>
                )
              })}
              {!items.data?.items?.length ? <EmptyState title="没有找到知识" description="请调整筛选条件" /> : null}
            </List>
          )}
        </Paper>

        {selectedItem ? <KnowledgeDetail item={selectedItem} /> : <Paper variant="outlined"><EmptyState title="选择一条知识" description="从列表中选择" /></Paper>}

        <Paper component="aside" variant="outlined" aria-label="知识搜索测试" sx={{ alignSelf: 'start', minWidth: 0, p: 2 }}>
          <Stack spacing={1.5}>
            <Stack direction="row" spacing={1} alignItems="center" justifyContent="space-between">
              <Typography component="h2" variant="h3">搜索测试</Typography>
              {retrieval.isPending ? <CircularProgress size={18} aria-label="测试中" /> : null}
            </Stack>
            <TextField label="客户问题" value={retrievalQuery} onChange={(event) => setRetrievalQuery(event.target.value)} placeholder="例如：包裹派送失败怎么办？" />
            <Button variant="outlined" color="inherit" disabled={!retrievalQuery.trim() || retrieval.isPending} onClick={() => retrieval.mutate()}>
              测试搜索
            </Button>
            {retrieval.error ? <Alert severity="error" variant="outlined"><AlertTitle>测试失败</AlertTitle>{errorCopy(retrieval.error, '请稍后重试')}</Alert> : null}
            {retrieval.data ? (
              <Stack spacing={1.25}>
                <Typography variant="subtitle2">{retrieval.data.hits.length ? `找到 ${retrieval.data.hits.length} 条` : '未找到结果'}</Typography>
                {retrieval.data.hits.slice(0, 5).map((hit) => (
                  <Box component="article" key={`${hit.item_id}-${hit.chunk_index}`} sx={{ borderTop: 1, borderColor: 'divider', pt: 1.25 }}>
                    <Typography variant="subtitle2">{sanitizeDisplayText(hit.title)}</Typography>
                    <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>{sanitizeDisplayText(hit.direct_answer || hit.text).slice(0, 260)}</Typography>
                  </Box>
                ))}
              </Stack>
            ) : null}
          </Stack>
        </Paper>
      </Box>
    </Box>
  )
}

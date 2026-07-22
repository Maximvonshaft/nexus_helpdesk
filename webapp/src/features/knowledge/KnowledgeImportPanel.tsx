import CloudUploadRoundedIcon from '@mui/icons-material/CloudUploadRounded'
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Collapse,
  MenuItem,
  Paper,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { OperatorErrorNotice } from '@/app/OperatorPresentation'
import { governanceApi, type KnowledgeImportBatch } from '@/lib/governanceApi'
import { supportApi } from '@/lib/supportApi'

export function KnowledgeImportPanel({ canManage }: { canManage: boolean }) {
  const queryClient = useQueryClient()
  const [expanded, setExpanded] = useState(false)
  const [files, setFiles] = useState<File[]>([])
  const [marketId, setMarketId] = useState<number | ''>('')
  const [channel, setChannel] = useState('all')
  const [audienceScope, setAudienceScope] = useState<'customer' | 'internal'>('customer')
  const [language, setLanguage] = useState('')
  const imports = useQuery({
    queryKey: ['governance', 'knowledge-imports'],
    queryFn: () => governanceApi.knowledgeImports(10),
    enabled: canManage,
  })
  const markets = useQuery({
    queryKey: ['markets'],
    queryFn: supportApi.identityMarkets,
    enabled: canManage,
  })
  const upload = useMutation({
    mutationFn: governanceApi.createKnowledgeImport,
    onSuccess: async () => {
      setFiles([])
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['governance', 'knowledge-imports'] }),
        queryClient.invalidateQueries({ queryKey: ['knowledge-items'] }),
      ])
    },
  })
  const latest = imports.data?.[0]
  const selectedBytes = useMemo(() => files.reduce((sum, file) => sum + file.size, 0), [files])

  if (!canManage) return null
  return (
    <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
      <Stack direction={{ xs: 'column', md: 'row' }} spacing={1} sx={{ alignItems: { md: 'center' }, justifyContent: 'space-between' }}>
        <Box>
          <Typography component="h2" variant="h2">批量导入知识文件</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            一次最多选择 20 个文件。导入结果会生成知识草稿，需要审核并发布后才会生效。
          </Typography>
        </Box>
        <Button variant={expanded ? 'outlined' : 'contained'} startIcon={<CloudUploadRoundedIcon />} onClick={() => setExpanded((value) => !value)}>
          {expanded ? '收起导入' : '开始导入'}
        </Button>
      </Stack>

      {latest ? (
        <Stack direction="row" useFlexGap spacing={1} sx={{ flexWrap: 'wrap', mt: 1.5 }}>
          <Chip label={`最近批次 #${latest.id}`} />
          <Chip color={importStatusColor(latest.status)} label={importStatusLabel(latest.status)} />
          <Chip label={`已创建草稿 ${latest.succeeded_files}`} />
          <Chip label={`重复 ${latest.duplicate_files}`} />
          <Chip label={`失败 ${latest.failed_files}`} />
        </Stack>
      ) : null}

      <Collapse in={expanded} unmountOnExit>
        <Stack spacing={2} sx={{ mt: 2 }}>
          <Button component="label" variant="outlined" startIcon={<CloudUploadRoundedIcon />}>
            选择文件
            <input
              hidden
              multiple
              type="file"
              onChange={(event) => setFiles(Array.from(event.target.files || []).slice(0, 20))}
            />
          </Button>
          <Typography variant="caption" color="text.secondary">
            已选择 {files.length} 个文件，共 {(selectedBytes / 1024 / 1024).toFixed(2)} MB。
          </Typography>
          {files.length ? (
            <Stack direction="row" useFlexGap spacing={1} sx={{ flexWrap: 'wrap' }}>
              {files.map((file) => <Chip key={`${file.name}-${file.lastModified}`} label={file.name} onDelete={() => setFiles((current) => current.filter((item) => item !== file))} />)}
            </Stack>
          ) : null}
          <Stack direction={{ xs: 'column', md: 'row' }} spacing={1}>
            <TextField select fullWidth label="市场" value={marketId} onChange={(event) => setMarketId(event.target.value ? Number(event.target.value) : '')}>
              <MenuItem value="">全部市场</MenuItem>
              {(markets.data || []).map((item) => <MenuItem key={item.id} value={item.id}>{item.name}</MenuItem>)}
            </TextField>
            <TextField select fullWidth label="渠道" value={channel} onChange={(event) => setChannel(event.target.value)}>
              <MenuItem value="all">全部渠道</MenuItem>
              <MenuItem value="webchat">网页客服</MenuItem>
              <MenuItem value="whatsapp">WhatsApp</MenuItem>
              <MenuItem value="email">邮件</MenuItem>
              <MenuItem value="voice">语音</MenuItem>
              <MenuItem value="website">网站</MenuItem>
            </TextField>
            <TextField select fullWidth label="适用对象" value={audienceScope} onChange={(event) => setAudienceScope(event.target.value as 'customer' | 'internal')}>
              <MenuItem value="customer">客户问答</MenuItem>
              <MenuItem value="internal">内部参考</MenuItem>
            </TextField>
            <TextField fullWidth label="语言（可选）" value={language} onChange={(event) => setLanguage(event.target.value)} placeholder="例如 en、de、zh" />
          </Stack>
          <Alert severity="info" variant="outlined">
            文件只会生成草稿，不会自动发布。系统会识别重复文件；整个批次失败时不会创建任何可用知识。
          </Alert>
          <Button
            variant="contained"
            disabled={!files.length || upload.isPending}
            startIcon={upload.isPending ? <CircularProgress color="inherit" size={16} /> : <CloudUploadRoundedIcon />}
            onClick={() => upload.mutate({ files, marketId: marketId || null, channel, audienceScope, language: language || null })}
          >
            {upload.isPending ? '正在导入…' : '创建知识草稿'}
          </Button>
          {upload.error ? <OperatorErrorNotice title="知识导入失败" error={upload.error} fallback="请检查文件类型、大小和市场状态" /> : null}
        </Stack>
      </Collapse>

      {imports.error ? <Box sx={{ mt: 2 }}><OperatorErrorNotice title="无法读取导入记录" error={imports.error} fallback="请稍后重试" /></Box> : null}
      {imports.isLoading ? <Stack sx={{ alignItems: 'center', py: 2 }}><CircularProgress size={24} /></Stack> : null}
      {imports.data?.length ? (
        <Stack spacing={1} sx={{ mt: 2 }}>
          {imports.data.map((batch) => (
            <Paper key={batch.id} variant="outlined" sx={{ p: 1.5 }}>
              <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} sx={{ justifyContent: 'space-between' }}>
                <Box>
                  <Typography variant="subtitle2">批次 #{batch.id} · {batch.total_files} 个文件</Typography>
                  <Typography variant="caption" color="text.secondary">{new Date(batch.created_at).toLocaleString()} · {channelLabel(batch.channel)} · {audienceLabel(batch.audience_scope)}</Typography>
                </Box>
                <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: 'wrap' }}>
                  <Chip size="small" color={importStatusColor(batch.status)} label={importStatusLabel(batch.status)} />
                  <Chip size="small" label={`已创建 ${batch.succeeded_files}`} />
                  <Chip size="small" label={`重复 ${batch.duplicate_files}`} />
                  <Chip size="small" label={`失败 ${batch.failed_files}`} />
                </Stack>
              </Stack>
              {batch.documents.some((document) => document.status === 'failed') ? (
                <Alert severity="warning" variant="outlined" sx={{ mt: 1 }}>
                  {batch.documents.filter((document) => document.status === 'failed').map((document) => `${document.file_name}: ${document.error_message || '处理失败'}`).join('；')}
                </Alert>
              ) : null}
            </Paper>
          ))}
        </Stack>
      ) : null}
    </Paper>
  )
}

function importStatusLabel(status: KnowledgeImportBatch['status']) {
  if (status === 'processing') return '处理中'
  if (status === 'ready') return '已完成'
  if (status === 'partial') return '部分完成'
  return '失败'
}

function importStatusColor(status: KnowledgeImportBatch['status']): 'success' | 'warning' | 'error' {
  if (status === 'ready') return 'success'
  if (status === 'failed') return 'error'
  return 'warning'
}

function channelLabel(channel: string) {
  if (channel === 'all') return '全部渠道'
  if (channel === 'webchat') return '网页客服'
  if (channel === 'email') return '邮件'
  if (channel === 'voice') return '语音'
  if (channel === 'website') return '网站'
  return channel
}

function audienceLabel(audience: KnowledgeImportBatch['audience_scope']) {
  return audience === 'internal' ? '内部参考' : '客户问答'
}

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
import { governanceApi } from '@/lib/governanceApi'
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
            一次最多 20 个文件。每个文件仍进入现有 KnowledgeItem 草稿、发布与索引链路，不创建第二套知识库。
          </Typography>
        </Box>
        <Button variant={expanded ? 'outlined' : 'contained'} startIcon={<CloudUploadRoundedIcon />} onClick={() => setExpanded((value) => !value)}>
          {expanded ? '收起导入' : '打开导入'}
        </Button>
      </Stack>

      {latest ? (
        <Stack direction="row" useFlexGap spacing={1} sx={{ flexWrap: 'wrap', mt: 1.5 }}>
          <Chip label={`最近批次 #${latest.id}`} />
          <Chip color={latest.status === 'ready' ? 'success' : latest.status === 'failed' ? 'error' : 'warning'} label={latest.status} />
          <Chip label={`草稿 ${latest.succeeded_files}`} />
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
              <MenuItem value="">全局知识</MenuItem>
              {(markets.data || []).map((item) => <MenuItem key={item.id} value={item.id}>{item.name}</MenuItem>)}
            </TextField>
            <TextField select fullWidth label="渠道" value={channel} onChange={(event) => setChannel(event.target.value)}>
              {['all', 'webchat', 'whatsapp', 'email', 'voice', 'website'].map((item) => <MenuItem key={item} value={item}>{item}</MenuItem>)}
            </TextField>
            <TextField select fullWidth label="受众" value={audienceScope} onChange={(event) => setAudienceScope(event.target.value as 'customer' | 'internal')}>
              <MenuItem value="customer">客户</MenuItem>
              <MenuItem value="internal">内部</MenuItem>
            </TextField>
            <TextField fullWidth label="语言（可选）" value={language} onChange={(event) => setLanguage(event.target.value)} placeholder="en / de / zh" />
          </Stack>
          <Alert severity="info" variant="outlined">
            文件内容只用于创建受控知识草稿；重复文件按 SHA-256 去重。批次失败不会发布任何知识项。
          </Alert>
          <Button
            variant="contained"
            disabled={!files.length || upload.isPending}
            startIcon={upload.isPending ? <CircularProgress color="inherit" size={16} /> : <CloudUploadRoundedIcon />}
            onClick={() => upload.mutate({ files, marketId: marketId || null, channel, audienceScope, language: language || null })}
          >
            创建知识草稿批次
          </Button>
          {upload.error ? <OperatorErrorNotice title="知识导入失败" error={upload.error} fallback="请检查文件类型、大小和市场状态" /> : null}
        </Stack>
      </Collapse>

      {imports.error ? <Box sx={{ mt: 2 }}><OperatorErrorNotice title="无法读取导入历史" error={imports.error} fallback="请稍后重试" /></Box> : null}
      {imports.isLoading ? <Stack sx={{ alignItems: 'center', py: 2 }}><CircularProgress size={24} /></Stack> : null}
      {imports.data?.length ? (
        <Stack spacing={1} sx={{ mt: 2 }}>
          {imports.data.map((batch) => (
            <Paper key={batch.id} variant="outlined" sx={{ p: 1.5 }}>
              <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} sx={{ justifyContent: 'space-between' }}>
                <Box>
                  <Typography variant="subtitle2">批次 #{batch.id} · {batch.total_files} 个文件</Typography>
                  <Typography variant="caption" color="text.secondary">{new Date(batch.created_at).toLocaleString()} · {batch.channel} · {batch.audience_scope}</Typography>
                </Box>
                <Stack direction="row" spacing={1}>
                  <Chip size="small" color={batch.status === 'ready' ? 'success' : batch.status === 'failed' ? 'error' : 'warning'} label={batch.status} />
                  <Chip size="small" label={`成功 ${batch.succeeded_files}`} />
                  <Chip size="small" label={`重复 ${batch.duplicate_files}`} />
                  <Chip size="small" label={`失败 ${batch.failed_files}`} />
                </Stack>
              </Stack>
              {batch.documents.some((document) => document.status === 'failed') ? (
                <Alert severity="warning" variant="outlined" sx={{ mt: 1 }}>
                  {batch.documents.filter((document) => document.status === 'failed').map((document) => `${document.file_name}: ${document.error_message || document.error_code || '处理失败'}`).join('；')}
                </Alert>
              ) : null}
            </Paper>
          ))}
        </Stack>
      ) : null}
    </Paper>
  )
}

import PlayArrowRoundedIcon from '@mui/icons-material/PlayArrowRounded'
import {
  Alert,
  Box,
  Button,
  Checkbox,
  CircularProgress,
  FormControlLabel,
  MenuItem,
  Paper,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { OperatorErrorNotice, OperatorFactGrid, OperatorTechnicalDisclosure } from '@/app/OperatorPresentation'
import { agentControlApi } from '@/lib/agentControlApi'
import type { AgentControlSnapshot } from '@/lib/types'

interface Props {
  snapshot: AgentControlSnapshot
  tenantKey: string
  setTenantKey: (value: string) => void
  channel: string
  setChannel: (value: string) => void
  language: string
  setLanguage: (value: string) => void
}

export function OverviewPanel({ snapshot, tenantKey, setTenantKey, channel, setChannel, language, setLanguage }: Props) {
  const [body, setBody] = useState('')
  const [customerId, setCustomerId] = useState('')
  const [executeModel, setExecuteModel] = useState(false)
  const playground = useMutation({
    mutationFn: () => agentControlApi.playground({
      tenant_key: tenantKey,
      body: body.trim(),
      channel,
      language: language.trim() || null,
      customer_id: customerId.trim() ? Number(customerId) : null,
      execute_model: executeModel,
    }),
  })
  const publishedResources = snapshot.resources.filter((item) => item.is_active && item.published_version > 0)
  const configuredTools = snapshot.tool_policies.filter((item) => item.enabled).length

  return (
    <Stack spacing={2}>
      <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: 'repeat(2, minmax(0, 1fr))', lg: 'repeat(6, minmax(0, 1fr))' } }}>
        {[
          ['已发布人格', snapshot.personas.filter((item) => item.is_active && item.published_version > 0).length],
          ['已发布配置', publishedResources.length],
          ['生效业务剧本', snapshot.resolved_playbooks.length],
          ['可执行工具', snapshot.tools.filter((item) => item.executable).length],
          ['工具策略', configuredTools],
          ['企业集成', snapshot.integrations.length],
        ].map(([label, value]) => (
          <Paper key={String(label)} variant="outlined" sx={{ p: 2, minWidth: 0 }}>
            <Typography variant="caption" color="text.secondary">{label}</Typography>
            <Typography variant="h2" sx={{ mt: 0.5, fontVariantNumeric: 'tabular-nums' }}>{value}</Typography>
          </Paper>
        ))}
      </Box>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography component="h2" variant="h3">作用域</Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>用于预览人格、业务剧本、模型和记忆策略的最终解析结果。</Typography>
        <Box sx={{ display: 'grid', gap: 1.5, gridTemplateColumns: { xs: '1fr', md: 'repeat(3, minmax(0, 1fr))' }, mt: 2 }}>
          <TextField label="租户" value={tenantKey} onChange={(event) => setTenantKey(event.target.value)} />
          <TextField select label="渠道" value={channel} onChange={(event) => setChannel(event.target.value)}>
            <MenuItem value="webchat">网页客服</MenuItem><MenuItem value="whatsapp">WhatsApp</MenuItem><MenuItem value="email">邮件</MenuItem><MenuItem value="voice">语音</MenuItem>
          </TextField>
          <TextField label="语言" value={language} onChange={(event) => setLanguage(event.target.value)} placeholder="例如 zh-CN；留空自动" />
        </Box>
      </Paper>

      <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', xl: 'minmax(0, 1.1fr) minmax(340px, .9fr)' } }}>
        <Paper component="section" variant="outlined" sx={{ p: 2 }}>
          <Typography component="h2" variant="h3">Agent 测试台</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>默认只解析配置；启用模型执行时仍只暴露只读工具，不执行写操作。</Typography>
          <Stack spacing={1.5} sx={{ mt: 2 }}>
            <TextField label="客户消息" multiline minRows={5} value={body} onChange={(event) => setBody(event.target.value)} placeholder="输入一条真实客户问题" />
            <TextField label="客户 ID" type="number" value={customerId} onChange={(event) => setCustomerId(event.target.value)} helperText="可选；用于验证长期记忆注入" />
            <FormControlLabel control={<Checkbox checked={executeModel} onChange={(event) => setExecuteModel(event.target.checked)} />} label="执行模型（只读沙箱）" />
            <Button variant="contained" startIcon={playground.isPending ? <CircularProgress size={16} color="inherit" /> : <PlayArrowRoundedIcon />} disabled={!body.trim() || playground.isPending} onClick={() => playground.mutate()}>
              {playground.isPending ? '正在执行…' : executeModel ? '运行 Agent' : '预览配置'}
            </Button>
            {playground.error ? <OperatorErrorNotice title="测试失败" error={playground.error} fallback="请检查配置" /> : null}
          </Stack>
        </Paper>

        <Paper component="aside" variant="outlined" sx={{ p: 2, minWidth: 0 }}>
          <Typography component="h2" variant="h3">解析结果</Typography>
          {!playground.data ? <Alert severity="info" variant="outlined" sx={{ mt: 2 }}>尚未运行测试。</Alert> : (
            <Stack spacing={2} sx={{ mt: 2 }}>
              <OperatorFactGrid facts={[
                ['人格', String((playground.data.persona as Record<string, unknown> | null)?.name || '未匹配')],
                ['业务剧本', playground.data.playbooks.length],
                ['可用工具', playground.data.tools.length],
                ['长期记忆', Number((playground.data.customer_memory as Record<string, unknown> | null)?.count || 0)],
                ['公告', playground.data.active_bulletins?.length || 0],
                ['模型执行', playground.data.model_executed ? '已执行' : '仅预览'],
              ]} />
              {playground.data.reply ? <Alert severity={playground.data.error_code ? 'warning' : 'success'} variant="outlined">{playground.data.reply}</Alert> : null}
              <OperatorTechnicalDisclosure title="运行证据" summary="人格、剧本、工具、记忆、公告和模型执行轨迹">
                <Box component="pre" sx={{ m: 0, maxHeight: 520, overflow: 'auto', whiteSpace: 'pre-wrap', fontSize: 12 }}>{JSON.stringify(playground.data, null, 2)}</Box>
              </OperatorTechnicalDisclosure>
            </Stack>
          )}
        </Paper>
      </Box>
    </Stack>
  )
}

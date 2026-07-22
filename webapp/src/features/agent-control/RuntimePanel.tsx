import PublishRoundedIcon from '@mui/icons-material/PublishRounded'
import SaveRoundedIcon from '@mui/icons-material/SaveRounded'
import {
  Alert,
  Box,
  Button,
  Checkbox,
  Chip,
  CircularProgress,
  FormControlLabel,
  MenuItem,
  Paper,
  Stack,
  Switch,
  TextField,
  Typography,
} from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { OperatorErrorNotice, OperatorFactGrid, OperatorTechnicalDisclosure } from '@/app/OperatorPresentation'
import { agentControlApi, type AgentConfigDraft } from '@/lib/agentControlApi'
import { supportApi } from '@/lib/supportApi'
import type { AgentConfigResource, AgentControlSnapshot } from '@/lib/types'
import { asBoolean, asNumber, asString, contentOf, resourceByType } from './formUtils'

type ModelDraft = {
  resource_key: string
  name: string
  is_active: boolean
  model: string
  endpoint_url: string
  credential_ref: string
  request_path: string
  request_shape: string
  temperature: string
  top_p: string
  max_prompt_chars: string
  max_output_chars: string
  num_predict: string
  num_ctx: string
  keep_alive: string
  timeout_seconds: string
  draft_summary: string
}

type RuntimeDraft = {
  resource_key: string
  name: string
  is_active: boolean
  max_tool_rounds: string
  provider_timeout_ms: string
  allow_high_risk_writes: boolean
  allowed_tools: string[]
  draft_summary: string
}

function modelDraft(resource?: AgentConfigResource | null): ModelDraft {
  const content = contentOf(resource)
  return {
    resource_key: resource?.resource_key || 'agent.model.private-default',
    name: resource?.name || 'Private Agent model',
    is_active: resource?.is_active ?? true,
    model: asString(content.model, 'qwen2.5:3b'),
    endpoint_url: asString(content.endpoint_url),
    credential_ref: asString(content.credential_ref),
    request_path: asString(content.request_path, '/api/chat'),
    request_shape: asString(content.request_shape, 'ollama_chat'),
    temperature: String(asNumber(content.temperature, 0.1)),
    top_p: String(asNumber(content.top_p, 0.85)),
    max_prompt_chars: String(asNumber(content.max_prompt_chars, 12000)),
    max_output_chars: String(asNumber(content.max_output_chars, 4000)),
    num_predict: String(asNumber(content.num_predict, 512)),
    num_ctx: String(asNumber(content.num_ctx, 8192)),
    keep_alive: asString(content.keep_alive, '24h'),
    timeout_seconds: String(asNumber(content.timeout_seconds, 12)),
    draft_summary: resource?.draft_summary || '',
  }
}

function runtimeDraft(resource?: AgentConfigResource | null): RuntimeDraft {
  const content = contentOf(resource)
  return {
    resource_key: resource?.resource_key || 'agent.runtime.default',
    name: resource?.name || 'Default Agent runtime policy',
    is_active: resource?.is_active ?? true,
    max_tool_rounds: String(asNumber(content.max_tool_rounds, 3)),
    provider_timeout_ms: String(asNumber(content.provider_timeout_ms, 15000)),
    allow_high_risk_writes: asBoolean(content.allow_high_risk_writes),
    allowed_tools: Array.isArray(content.allowed_tools) ? content.allowed_tools.map(String) : [],
    draft_summary: resource?.draft_summary || '',
  }
}

export function RuntimePanel({ snapshot, canManage }: { snapshot: AgentControlSnapshot; canManage: boolean }) {
  const queryClient = useQueryClient()
  const modelResource = resourceByType(snapshot.resources, 'model_profile')
  const runtimeResource = resourceByType(snapshot.resources, 'runtime_policy')
  const [model, setModel] = useState(() => modelDraft(modelResource))
  const [runtime, setRuntime] = useState(() => runtimeDraft(runtimeResource))
  useEffect(() => setModel(modelDraft(modelResource)), [modelResource])
  useEffect(() => setRuntime(runtimeDraft(runtimeResource)), [runtimeResource])
  const status = useQuery({
    queryKey: ['canonicalProviderRuntimeStatus'],
    queryFn: supportApi.providerRuntimeStatus,
    retry: false,
    refetchInterval: 15_000,
  })
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['agentControlSnapshot'] })

  const saveModel = useMutation({
    mutationFn: async (publish: boolean) => {
      const payload: AgentConfigDraft = {
        resource_key: model.resource_key,
        config_type: 'model_profile',
        name: model.name,
        description: 'Private enterprise Agent inference profile',
        scope_type: 'global',
        scope_value: null,
        market_id: null,
        is_active: model.is_active,
        draft_summary: model.draft_summary || null,
        draft_content_json: {
          schema_version: 'nexus.agent_model_profile.v1',
          provider: 'private_ai_runtime',
          endpoint_url: model.endpoint_url.trim() || null,
          credential_ref: model.credential_ref.trim() || null,
          request_path: model.request_path,
          request_shape: model.request_shape,
          model: model.model,
          temperature: Number(model.temperature),
          top_p: Number(model.top_p),
          max_prompt_chars: Number(model.max_prompt_chars),
          max_output_chars: Number(model.max_output_chars),
          num_predict: Number(model.num_predict),
          num_ctx: Number(model.num_ctx),
          keep_alive: model.keep_alive,
          timeout_seconds: Number(model.timeout_seconds),
          enabled: model.is_active,
        },
      }
      const item = modelResource
        ? await agentControlApi.updateConfig(modelResource.id, payload)
        : await agentControlApi.createConfig(payload)
      if (publish) await agentControlApi.publishConfig(item.id, 'Model profile publish')
      return item
    },
    onSuccess: invalidate,
  })

  const saveRuntime = useMutation({
    mutationFn: async (publish: boolean) => {
      const payload: AgentConfigDraft = {
        resource_key: runtime.resource_key,
        config_type: 'runtime_policy',
        name: runtime.name,
        description: 'Canonical Agent runtime policy',
        scope_type: 'global',
        scope_value: null,
        market_id: null,
        is_active: runtime.is_active,
        draft_summary: runtime.draft_summary || null,
        draft_content_json: {
          schema_version: 'nexus.agent_runtime_policy.v1',
          max_tool_rounds: Number(runtime.max_tool_rounds),
          allow_high_risk_writes: runtime.allow_high_risk_writes,
          allowed_tools: runtime.allowed_tools,
          provider_timeout_ms: Number(runtime.provider_timeout_ms),
          enabled: runtime.is_active,
        },
      }
      const item = runtimeResource
        ? await agentControlApi.updateConfig(runtimeResource.id, payload)
        : await agentControlApi.createConfig(payload)
      if (publish) await agentControlApi.publishConfig(item.id, 'Runtime policy publish')
      return item
    },
    onSuccess: invalidate,
  })

  const selectedProvider = status.data?.providers?.find((item) => item.selected)

  return (
    <Stack spacing={2}>
      <Paper component="section" variant="outlined" sx={{ p: 2 }}>
        <Stack direction={{ xs: 'column', md: 'row' }} spacing={2} sx={{ justifyContent: 'space-between' }}>
          <Typography component="h2" variant="h3">运行状态</Typography>
          <Chip color={status.data?.ok ? 'success' : 'warning'} label={status.isLoading ? '检查中' : status.data?.ok ? '可用' : '需要检查'} />
        </Stack>
        {status.error ? <Box sx={{ mt: 2 }}><OperatorErrorNotice title="无法读取运行状态" error={status.error} fallback="请检查模型服务和网络连接" /></Box> : null}
        <Box sx={{ mt: 2 }}>
          <OperatorFactGrid facts={[
            ['模型服务', selectedProvider?.name || status.data?.configured_provider || '未配置'],
            ['连接状态', selectedProvider?.configured ? '已配置' : '需要检查'],
            ['自动处理', status.data?.webchat_runtime_enabled ? '启用' : '停用'],
            ['备用服务', status.data?.fallback_provider || '无'],
          ]} />
        </Box>
      </Paper>

      <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', xl: 'repeat(2, minmax(0, 1fr))' } }}>
        <Paper component="section" variant="outlined" sx={{ p: 2 }}>
          <Typography component="h2" variant="h3">模型配置</Typography>
          {!canManage ? <Alert severity="info" variant="outlined" sx={{ mt: 2 }}>当前为只读视图。</Alert> : null}
          {saveModel.error ? <Box sx={{ mt: 2 }}><OperatorErrorNotice title="模型配置保存失败" error={saveModel.error} fallback="请检查模型名称和服务地址" /></Box> : null}
          <Stack spacing={1.5} sx={{ mt: 2 }}>
            <TextField label="模型名称" required disabled={!canManage} value={model.model} onChange={(event) => setModel((current) => ({ ...current, model: event.target.value }))} />
            <TextField label="模型服务地址" disabled={!canManage} value={model.endpoint_url} onChange={(event) => setModel((current) => ({ ...current, endpoint_url: event.target.value }))} helperText="留空使用系统默认地址" />
            <FormControlLabel control={<Switch disabled={!canManage} checked={model.is_active} onChange={(event) => setModel((current) => ({ ...current, is_active: event.target.checked }))} />} label="启用模型配置" />
            <OperatorTechnicalDisclosure title="高级参数" summary="连接、采样和容量限制">
              <Stack spacing={1.5}>
                <TextField label="凭据引用" disabled={!canManage} value={model.credential_ref} onChange={(event) => setModel((current) => ({ ...current, credential_ref: event.target.value }))} helperText="仅填写系统密钥引用，不填写真实密钥" />
                <Box sx={{ display: 'grid', gap: 1.25, gridTemplateColumns: { xs: '1fr', md: 'repeat(2, minmax(0, 1fr))' } }}>
                  <TextField label="请求路径" disabled={!canManage} value={model.request_path} onChange={(event) => setModel((current) => ({ ...current, request_path: event.target.value }))} />
                  <TextField select label="请求格式" disabled={!canManage} value={model.request_shape} onChange={(event) => setModel((current) => ({ ...current, request_shape: event.target.value }))}>
                    <MenuItem value="ollama_chat">Ollama Chat</MenuItem><MenuItem value="messages">Messages</MenuItem><MenuItem value="system_input">System/Input</MenuItem><MenuItem value="question">Question</MenuItem>
                  </TextField>
                  <TextField label="随机度" type="number" disabled={!canManage} value={model.temperature} onChange={(event) => setModel((current) => ({ ...current, temperature: event.target.value }))} />
                  <TextField label="采样范围" type="number" disabled={!canManage} value={model.top_p} onChange={(event) => setModel((current) => ({ ...current, top_p: event.target.value }))} />
                  <TextField label="上下文长度" type="number" disabled={!canManage} value={model.num_ctx} onChange={(event) => setModel((current) => ({ ...current, num_ctx: event.target.value }))} />
                  <TextField label="最大生成长度" type="number" disabled={!canManage} value={model.num_predict} onChange={(event) => setModel((current) => ({ ...current, num_predict: event.target.value }))} />
                  <TextField label="最大输入字符" type="number" disabled={!canManage} value={model.max_prompt_chars} onChange={(event) => setModel((current) => ({ ...current, max_prompt_chars: event.target.value }))} />
                  <TextField label="最大回复字符" type="number" disabled={!canManage} value={model.max_output_chars} onChange={(event) => setModel((current) => ({ ...current, max_output_chars: event.target.value }))} />
                  <TextField label="超时秒数" type="number" disabled={!canManage} value={model.timeout_seconds} onChange={(event) => setModel((current) => ({ ...current, timeout_seconds: event.target.value }))} />
                  <TextField label="保持加载时间" disabled={!canManage} value={model.keep_alive} onChange={(event) => setModel((current) => ({ ...current, keep_alive: event.target.value }))} />
                </Box>
              </Stack>
            </OperatorTechnicalDisclosure>
            <TextField label="版本摘要" multiline minRows={2} disabled={!canManage} value={model.draft_summary} onChange={(event) => setModel((current) => ({ ...current, draft_summary: event.target.value }))} />
            {canManage ? <Stack direction="row" spacing={1}>
              <Button variant="contained" disabled={saveModel.isPending} startIcon={saveModel.isPending ? <CircularProgress color="inherit" size={16} /> : <SaveRoundedIcon />} onClick={() => saveModel.mutate(false)}>保存草稿</Button>
              <Button variant="outlined" disabled={saveModel.isPending} startIcon={<PublishRoundedIcon />} onClick={() => saveModel.mutate(true)}>保存并发布</Button>
            </Stack> : null}
          </Stack>
        </Paper>

        <Paper component="section" variant="outlined" sx={{ p: 2 }}>
          <Typography component="h2" variant="h3">运行限制</Typography>
          {saveRuntime.error ? <Box sx={{ mt: 2 }}><OperatorErrorNotice title="运行限制保存失败" error={saveRuntime.error} fallback="请检查工具和风险设置" /></Box> : null}
          <Stack spacing={1.5} sx={{ mt: 2 }}>
            <Box sx={{ display: 'grid', gap: 1.25, gridTemplateColumns: { xs: '1fr', md: 'repeat(2, minmax(0, 1fr))' } }}>
              <TextField
                label="单次最多调用工具"
                type="number"
                slotProps={{ htmlInput: { min: 1, max: 6 } }}
                disabled={!canManage}
                value={runtime.max_tool_rounds}
                onChange={(event) => setRuntime((current) => ({ ...current, max_tool_rounds: event.target.value }))}
              />
              <TextField
                label="处理超时（毫秒）"
                type="number"
                slotProps={{ htmlInput: { min: 1000, max: 30000 } }}
                disabled={!canManage}
                value={runtime.provider_timeout_ms}
                onChange={(event) => setRuntime((current) => ({ ...current, provider_timeout_ms: event.target.value }))}
              />
            </Box>
            <TextField
              select
              label="允许使用的工具"
              slotProps={{
                select: {
                  multiple: true,
                  renderValue: (selected: unknown) => (
                    <Stack direction="row" spacing={0.5} useFlexGap sx={{ flexWrap: 'wrap' }}>
                      {(selected as string[]).map((item) => <Chip size="small" key={item} label={item} />)}
                    </Stack>
                  ),
                },
              }}
              disabled={!canManage}
              value={runtime.allowed_tools}
              onChange={(event) => setRuntime((current) => ({ ...current, allowed_tools: typeof event.target.value === 'string' ? event.target.value.split(',') : event.target.value }))}
              helperText="留空表示不额外限制"
            >
              {snapshot.tools.map((tool) => <MenuItem key={tool.name} value={tool.name}>{tool.name}</MenuItem>)}
            </TextField>
            <FormControlLabel control={<Checkbox disabled={!canManage} checked={runtime.allow_high_risk_writes} onChange={(event) => setRuntime((current) => ({ ...current, allow_high_risk_writes: event.target.checked }))} />} label="允许高风险操作" />
            {runtime.allow_high_risk_writes ? <Alert severity="warning" variant="outlined">高风险操作仍需相应权限和明确确认。</Alert> : null}
            <FormControlLabel control={<Switch disabled={!canManage} checked={runtime.is_active} onChange={(event) => setRuntime((current) => ({ ...current, is_active: event.target.checked }))} />} label="启用运行限制" />
            <TextField label="版本摘要" multiline minRows={2} disabled={!canManage} value={runtime.draft_summary} onChange={(event) => setRuntime((current) => ({ ...current, draft_summary: event.target.value }))} />
            {canManage ? <Stack direction="row" spacing={1}>
              <Button variant="contained" disabled={saveRuntime.isPending} startIcon={saveRuntime.isPending ? <CircularProgress color="inherit" size={16} /> : <SaveRoundedIcon />} onClick={() => saveRuntime.mutate(false)}>保存草稿</Button>
              <Button variant="outlined" disabled={saveRuntime.isPending} startIcon={<PublishRoundedIcon />} onClick={() => saveRuntime.mutate(true)}>保存并发布</Button>
            </Stack> : null}
            <OperatorTechnicalDisclosure title="系统信息" summary="安全边界和连接诊断">
              <Box component="pre" sx={{ m: 0, maxHeight: 360, overflow: 'auto', whiteSpace: 'pre-wrap', fontSize: 12 }}>{JSON.stringify({ boundary: status.data?.boundary, diagnostics: selectedProvider?.diagnostics }, null, 2)}</Box>
            </OperatorTechnicalDisclosure>
          </Stack>
        </Paper>
      </Box>
    </Stack>
  )
}

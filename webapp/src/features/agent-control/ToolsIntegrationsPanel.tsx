import AddRoundedIcon from '@mui/icons-material/AddRounded'
import DeleteOutlineRoundedIcon from '@mui/icons-material/DeleteOutlineRounded'
import PlayArrowRoundedIcon from '@mui/icons-material/PlayArrowRounded'
import PublishRoundedIcon from '@mui/icons-material/PublishRounded'
import SaveRoundedIcon from '@mui/icons-material/SaveRounded'
import {
  Alert,
  Box,
  Button,
  Checkbox,
  Chip,
  CircularProgress,
  Divider,
  FormControlLabel,
  List,
  ListItemButton,
  MenuItem,
  Paper,
  Stack,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from '@mui/material'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import {
  OperatorEmptyState,
  OperatorErrorNotice,
  OperatorTechnicalDisclosure,
} from '@/app/OperatorPresentation'
import {
  agentControlApi,
  type AgentConfigDraft,
  type ToolPolicyDraft,
} from '@/lib/agentControlApi'
import type {
  AgentConfigResource,
  AgentControlSnapshot,
  AgentToolPolicy,
} from '@/lib/types'
import {
  asBoolean,
  asNumber,
  asString,
  contentOf,
  lineText,
  lines,
  parseSchemaFields,
  schemaFieldsText,
} from './formUtils'

type PolicyDraft = ToolPolicyDraft & { id?: number }
type ScopeType = 'global' | 'market' | 'channel'
type IntegrationKind = 'http' | 'mcp_http'
type OperationMode = 'read' | 'write'

type OperationDraft = {
  key: string
  description: string
  mode: OperationMode
  method: string
  path: string
  schema_fields: string
  result_allowlist: string
  risk_level: 'low' | 'medium' | 'high'
  requires_confirmation: boolean
  enabled: boolean
}

type IntegrationDraft = {
  resource_key: string
  name: string
  description: string
  scope_type: ScopeType
  scope_value: string
  market_id: string
  is_active: boolean
  kind: IntegrationKind
  base_url: string
  credential_ref: string
  host_allowlist: string
  timeout_seconds: string
  max_response_bytes: string
  operations: OperationDraft[]
  draft_summary: string
}

function policyDraft(toolName: string, policy?: AgentToolPolicy | null): PolicyDraft {
  return {
    id: policy?.id,
    tool_name: toolName,
    country_code: policy?.country_code || 'GLOBAL',
    channel: policy?.channel || 'all',
    enabled: policy?.enabled ?? true,
    ai_auto_executable: policy?.ai_auto_executable ?? false,
    risk_level: policy?.risk_level || 'medium',
    requires_tracking_number: policy?.requires_tracking_number ?? false,
    requires_contact: policy?.requires_contact ?? false,
    requires_customer_confirmation: policy?.requires_customer_confirmation ?? false,
    requires_human_confirmation: policy?.requires_human_confirmation ?? false,
    allowed_channels_json: policy?.allowed_channels_json || null,
    allowed_countries_json: policy?.allowed_countries_json || null,
    audit_level: policy?.audit_level || 'detailed',
  }
}

function emptyOperation(): OperationDraft {
  return {
    key: '',
    description: '',
    mode: 'read',
    method: 'GET',
    path: '/',
    schema_fields: '',
    result_allowlist: '',
    risk_level: 'medium',
    requires_confirmation: false,
    enabled: true,
  }
}

function fromIntegration(resource?: AgentConfigResource | null): IntegrationDraft {
  const content = contentOf(resource)
  const rawOperations = Array.isArray(content.operations) ? content.operations : []
  const scopeType = resource?.scope_type ?? 'global'
  return {
    resource_key: resource?.resource_key || `agent.integration.${Date.now().toString(36)}`,
    name: resource?.name || '',
    description: resource?.description || '',
    scope_type: scopeType,
    scope_value: scopeType === 'channel' ? resource?.scope_value || '' : '',
    market_id: resource?.market_id == null ? '' : String(resource.market_id),
    is_active: resource?.is_active ?? true,
    kind: asString(content.kind, 'http') as IntegrationKind,
    base_url: asString(content.base_url),
    credential_ref: asString(content.credential_ref),
    host_allowlist: lineText(content.host_allowlist),
    timeout_seconds: String(asNumber(content.timeout_seconds, 12)),
    max_response_bytes: String(asNumber(content.max_response_bytes, 128000)),
    operations: rawOperations.map((raw) => {
      const item = raw as Record<string, unknown>
      const mode = asString(item.mode, 'read') as OperationMode
      return {
        key: asString(item.key),
        description: asString(item.description),
        mode,
        method: asString(item.method, mode === 'write' ? 'POST' : 'GET'),
        path: asString(item.path, '/'),
        schema_fields: schemaFieldsText(item.input_schema),
        result_allowlist: lineText(item.result_allowlist),
        risk_level: asString(item.risk_level, 'medium') as OperationDraft['risk_level'],
        requires_confirmation: mode === 'write' || asBoolean(item.requires_confirmation),
        enabled: item.enabled !== false,
      }
    }),
    draft_summary: resource?.draft_summary || '',
  }
}

function integrationPayload(draft: IntegrationDraft): AgentConfigDraft {
  const marketId = draft.market_id.trim() ? Number(draft.market_id) : null
  const scopeValue = draft.scope_type === 'global'
    ? null
    : draft.scope_type === 'market'
      ? String(marketId)
      : draft.scope_value.trim().toLowerCase()
  return {
    resource_key: draft.resource_key.trim().toLowerCase(),
    config_type: 'integration',
    name: draft.name.trim(),
    description: draft.description.trim() || null,
    scope_type: draft.scope_type,
    scope_value: scopeValue,
    market_id: draft.scope_type === 'market' ? marketId : null,
    is_active: draft.is_active,
    draft_summary: draft.draft_summary.trim() || draft.description.trim() || null,
    draft_content_json: {
      schema_version: 'nexus.agent_integration.v1',
      name: draft.name.trim(),
      kind: draft.kind,
      base_url: draft.base_url.trim(),
      credential_ref: draft.credential_ref.trim().toLowerCase() || null,
      host_allowlist: lines(draft.host_allowlist).map((item) => item.toLowerCase()),
      timeout_seconds: Number(draft.timeout_seconds) || 12,
      max_response_bytes: Number(draft.max_response_bytes) || 128000,
      enabled: draft.is_active,
      operations: draft.operations.map((item) => ({
        key: item.key.trim().toLowerCase(),
        description: item.description.trim(),
        mode: item.mode,
        method: draft.kind === 'mcp_http'
          ? 'POST'
          : item.mode === 'read'
            ? 'GET'
            : item.method,
        path: item.path.trim(),
        input_schema: parseSchemaFields(item.schema_fields),
        result_allowlist: lines(item.result_allowlist),
        risk_level: item.risk_level,
        requires_confirmation: item.mode === 'write' ? true : item.requires_confirmation,
        enabled: item.enabled,
      })),
    },
  }
}

export function ToolsIntegrationsPanel({
  snapshot,
  canManage,
  tenantKey,
}: {
  snapshot: AgentControlSnapshot
  canManage: boolean
  tenantKey: string
}) {
  const queryClient = useQueryClient()
  const [mode, setMode] = useState<'tools' | 'integrations'>('tools')
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['agentControlSnapshot'] })
  return (
    <Stack spacing={2}>
      <Paper variant="outlined" sx={{ p: 1 }}>
        <Stack direction="row" spacing={1}>
          <Button
            variant={mode === 'tools' ? 'contained' : 'text'}
            onClick={() => setMode('tools')}
          >
            工具权限
          </Button>
          <Button
            variant={mode === 'integrations' ? 'contained' : 'text'}
            onClick={() => setMode('integrations')}
          >
            外部系统
          </Button>
        </Stack>
      </Paper>
      {mode === 'tools' ? (
        <ToolGovernance
          snapshot={snapshot}
          canManage={canManage}
          tenantKey={tenantKey}
          invalidate={invalidate}
        />
      ) : (
        <IntegrationGovernance
          snapshot={snapshot}
          canManage={canManage}
          tenantKey={tenantKey}
          invalidate={invalidate}
        />
      )}
    </Stack>
  )
}

function ToolGovernance({
  snapshot,
  canManage,
  tenantKey,
  invalidate,
}: {
  snapshot: AgentControlSnapshot
  canManage: boolean
  tenantKey: string
  invalidate: () => Promise<unknown>
}) {
  const [selectedTool, setSelectedTool] = useState(snapshot.tools[0]?.name || '')
  const selectedContract = snapshot.tools.find((item) => item.name === selectedTool) || null
  const selectedPolicy = snapshot.tool_policies.find(
    (item) => item.tool_name === selectedTool && item.country_code === 'GLOBAL' && item.channel === 'all',
  ) || snapshot.tool_policies.find((item) => item.tool_name === selectedTool) || null
  const [draft, setDraft] = useState<PolicyDraft>(() => policyDraft(selectedTool, selectedPolicy))
  useEffect(() => setDraft(policyDraft(selectedTool, selectedPolicy)), [selectedTool, selectedPolicy])
  const save = useMutation({
    mutationFn: () => {
      const next = {
        ...draft,
        ai_auto_executable: Boolean(draft.ai_auto_executable && selectedContract?.executable),
        requires_customer_confirmation: selectedContract?.confirmation_required
          ? true
          : draft.requires_customer_confirmation,
      }
      return draft.id
        ? agentControlApi.updateToolPolicy(tenantKey, draft.id, next)
        : agentControlApi.createToolPolicy(tenantKey, next)
    },
    onSuccess: invalidate,
  })
  return (
    <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', xl: 'minmax(0, 1.5fr) 380px' } }}>
      <Paper component="section" variant="outlined" sx={{ p: 2, minWidth: 0 }}>
        <Typography component="h2" variant="h3">工具列表</Typography>
        <TableContainer sx={{ mt: 2 }}>
          <Table size="small" aria-label="工具列表">
            <TableHead>
              <TableRow>
                <TableCell>工具</TableCell>
                <TableCell>风险</TableCell>
                <TableCell>接入状态</TableCell>
                <TableCell>权限规则</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {snapshot.tools.map((tool) => {
                const policy = snapshot.tool_policies.find(
                  (item) => item.tool_name === tool.name && item.enabled,
                )
                return (
                  <TableRow
                    key={tool.name}
                    hover
                    selected={tool.name === selectedTool}
                    onClick={() => setSelectedTool(tool.name)}
                    sx={{ cursor: 'pointer' }}
                  >
                    <TableCell><Typography variant="subtitle2">{tool.name}</Typography></TableCell>
                    <TableCell>{riskLabel(tool.risk_level)}</TableCell>
                    <TableCell>
                      <Chip
                        size="small"
                        color={tool.executable ? 'success' : 'default'}
                        label={tool.executable ? '可用' : '未接入'}
                      />
                    </TableCell>
                    <TableCell>
                      <Chip
                        size="small"
                        color={policy ? 'success' : 'warning'}
                        label={policy ? '已配置' : '待配置'}
                      />
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </TableContainer>
      </Paper>
      <Paper component="aside" variant="outlined" sx={{ p: 2, alignSelf: 'start' }}>
        <Typography component="h2" variant="h3">工具权限</Typography>
        {!selectedContract ? (
          <OperatorEmptyState title="请选择一个工具" description="从左侧列表选择" />
        ) : (
          <Stack spacing={1.5} sx={{ mt: 2 }}>
            <Alert severity={selectedContract.executable ? 'success' : 'warning'} variant="outlined">
              {selectedContract.executable ? '该工具已接入，可配置使用权限。' : '该工具尚未接入，不能自动执行。'}
            </Alert>
            <TextField label="工具名称" value={draft.tool_name} disabled />
            <Box sx={{ display: 'grid', gap: 1.25, gridTemplateColumns: 'repeat(2, minmax(0, 1fr))' }}>
              <TextField
                label="国家或地区"
                disabled={!canManage}
                value={draft.country_code}
                onChange={(event) => setDraft((current) => ({ ...current, country_code: event.target.value }))}
              />
              <TextField
                label="渠道"
                disabled={!canManage}
                value={draft.channel}
                onChange={(event) => setDraft((current) => ({ ...current, channel: event.target.value }))}
              />
              <TextField
                select
                label="风险等级"
                disabled={!canManage}
                value={draft.risk_level}
                onChange={(event) => setDraft((current) => ({ ...current, risk_level: event.target.value }))}
              >
                <MenuItem value="low">低</MenuItem>
                <MenuItem value="medium">中</MenuItem>
                <MenuItem value="high">高</MenuItem>
                <MenuItem value="critical">关键</MenuItem>
              </TextField>
              <TextField
                select
                label="记录级别"
                disabled={!canManage}
                value={draft.audit_level}
                onChange={(event) => setDraft((current) => ({ ...current, audit_level: event.target.value }))}
              >
                <MenuItem value="standard">标准</MenuItem>
                <MenuItem value="detailed">详细</MenuItem>
              </TextField>
            </Box>
            <FormControlLabel
              control={(
                <Switch
                  disabled={!canManage}
                  checked={draft.enabled}
                  onChange={(event) => setDraft((current) => ({ ...current, enabled: event.target.checked }))}
                />
              )}
              label="启用此规则"
            />
            <FormControlLabel
              control={(
                <Switch
                  disabled={!canManage || !selectedContract.executable}
                  checked={draft.ai_auto_executable && selectedContract.executable}
                  onChange={(event) => setDraft((current) => ({ ...current, ai_auto_executable: event.target.checked }))}
                />
              )}
              label="允许自动执行"
            />
            <FormControlLabel control={<Checkbox disabled={!canManage} checked={draft.requires_tracking_number} onChange={(event) => setDraft((current) => ({ ...current, requires_tracking_number: event.target.checked }))} />} label="必须提供运单号" />
            <FormControlLabel control={<Checkbox disabled={!canManage} checked={draft.requires_contact} onChange={(event) => setDraft((current) => ({ ...current, requires_contact: event.target.checked }))} />} label="必须提供联系方式" />
            <FormControlLabel control={<Checkbox disabled={!canManage || selectedContract.confirmation_required} checked={selectedContract.confirmation_required || draft.requires_customer_confirmation} onChange={(event) => setDraft((current) => ({ ...current, requires_customer_confirmation: event.target.checked }))} />} label="必须获得客户确认" />
            <FormControlLabel control={<Checkbox disabled={!canManage} checked={draft.requires_human_confirmation} onChange={(event) => setDraft((current) => ({ ...current, requires_human_confirmation: event.target.checked }))} />} label="必须由人工确认" />
            {save.error ? <OperatorErrorNotice title="权限规则保存失败" error={save.error} fallback="请检查规则是否冲突" /> : null}
            {canManage ? (
              <Button variant="contained" disabled={save.isPending} startIcon={save.isPending ? <CircularProgress color="inherit" size={16} /> : <SaveRoundedIcon />} onClick={() => save.mutate()}>
                保存权限规则
              </Button>
            ) : null}
            <OperatorTechnicalDisclosure title="系统信息" summary="工具契约和内部标识">
              <Box component="pre" sx={{ m: 0, maxHeight: 360, overflow: 'auto', whiteSpace: 'pre-wrap', fontSize: 12 }}>
                {JSON.stringify(selectedContract, null, 2)}
              </Box>
            </OperatorTechnicalDisclosure>
          </Stack>
        )}
      </Paper>
    </Box>
  )
}

function IntegrationGovernance({
  snapshot,
  canManage,
  tenantKey,
  invalidate,
}: {
  snapshot: AgentControlSnapshot
  canManage: boolean
  tenantKey: string
  invalidate: () => Promise<unknown>
}) {
  const resources = useMemo(
    () => snapshot.resources.filter((item) => item.config_type === 'integration'),
    [snapshot.resources],
  )
  const [selectedId, setSelectedId] = useState<number | null>(resources[0]?.id ?? null)
  const [creating, setCreating] = useState(false)
  const selected = resources.find((item) => item.id === selectedId) || null
  const [draft, setDraft] = useState<IntegrationDraft>(() => fromIntegration(selected))
  const [testOperation, setTestOperation] = useState('')
  const [testArguments, setTestArguments] = useState('{}')
  useEffect(() => {
    setDraft(fromIntegration(creating ? null : selected))
    setTestOperation('')
  }, [creating, selected])

  const save = useMutation({
    mutationFn: async (publish: boolean) => {
      if (!draft.name.trim() || !draft.base_url.trim()) throw new Error('请填写系统名称和连接地址')
      if (!lines(draft.host_allowlist).length) throw new Error('请至少填写一个允许访问的主机名')
      if (!draft.operations.length) throw new Error('请至少配置一个可用操作')
      if (draft.scope_type === 'market' && !draft.market_id.trim()) throw new Error('按市场生效时必须选择市场')
      if (draft.scope_type === 'channel' && !draft.scope_value.trim()) throw new Error('按渠道生效时必须填写渠道')
      for (const operation of draft.operations) {
        if (!operation.key.trim() || !operation.description.trim() || !operation.path.trim()) {
          throw new Error('每个操作都必须填写编号、说明和路径')
        }
        if (operation.mode === 'write' && !operation.requires_confirmation) {
          throw new Error('写入操作必须获得客户确认')
        }
      }
      const data = integrationPayload(draft)
      const item = creating || !selected
        ? await agentControlApi.createConfig(data)
        : await agentControlApi.updateConfig(selected.id, data)
      if (publish) await agentControlApi.publishConfig(item.id, 'External system publish')
      return item
    },
    onSuccess: async (item) => {
      setCreating(false)
      setSelectedId(item.id)
      await invalidate()
    },
  })
  const test = useMutation({
    mutationFn: () => agentControlApi.testIntegration({
      tenant_key: tenantKey,
      environment: snapshot.scope.environment,
      market_id: snapshot.scope.market_id,
      channel: snapshot.scope.channel,
      language: snapshot.scope.language,
      case_type: snapshot.scope.case_type,
      integration_key: selected?.resource_key || draft.resource_key,
      operation: testOperation,
      arguments: JSON.parse(testArguments || '{}'),
    }),
  })
  const updateOperation = (index: number, patch: Partial<OperationDraft>) => {
    setDraft((current) => ({
      ...current,
      operations: current.operations.map((item, itemIndex) => {
        if (itemIndex !== index) return item
        const next = { ...item, ...patch }
        if (patch.mode === 'read') {
          next.method = 'GET'
          next.requires_confirmation = false
        } else if (patch.mode === 'write') {
          next.method = next.method === 'GET' ? 'POST' : next.method
          next.requires_confirmation = true
        }
        if (current.kind === 'mcp_http') next.method = 'POST'
        return next
      }),
    }))
  }

  return (
    <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', xl: '280px minmax(0, 1fr) 360px' } }}>
      <Paper component="aside" variant="outlined" sx={{ p: 1.5 }}>
        <Stack direction="row" spacing={1} sx={{ alignItems: 'center', justifyContent: 'space-between' }}>
          <Typography component="h2" variant="h3">外部系统</Typography>
          {canManage ? <Button size="small" variant="contained" startIcon={<AddRoundedIcon />} onClick={() => { setCreating(true); setSelectedId(null) }}>新建</Button> : null}
        </Stack>
        <List disablePadding sx={{ mt: 1.5 }}>
          {resources.map((item) => (
            <ListItemButton key={item.id} selected={!creating && item.id === selectedId} onClick={() => { setCreating(false); setSelectedId(item.id) }} sx={{ display: 'block', borderBottom: 1, borderColor: 'divider' }}>
              <Typography variant="subtitle2">{item.name}</Typography>
              <Typography variant="caption" color="text.secondary">v{item.published_version}</Typography>
            </ListItemButton>
          ))}
          {!resources.length ? <OperatorEmptyState title="尚未连接外部系统" description="新建一个连接配置" /> : null}
        </List>
      </Paper>

      <Paper component="section" variant="outlined" sx={{ p: 2, minWidth: 0 }}>
        <Typography component="h2" variant="h3">{creating ? '新建外部系统' : '编辑外部系统'}</Typography>
        {save.error ? <Box sx={{ mt: 2 }}><OperatorErrorNotice title="保存失败" error={save.error} fallback="请检查连接地址、访问范围和操作设置" /></Box> : null}
        <Stack spacing={1.5} sx={{ mt: 2 }}>
          <Box sx={{ display: 'grid', gap: 1.5, gridTemplateColumns: { xs: '1fr', md: 'repeat(2, minmax(0, 1fr))' } }}>
            <TextField label="配置编号" required disabled={!creating || !canManage} value={draft.resource_key} onChange={(event) => setDraft((current) => ({ ...current, resource_key: event.target.value.replace(/[^a-zA-Z0-9_.:-]+/g, '-').toLowerCase() }))} />
            <TextField label="系统名称" required disabled={!canManage} value={draft.name} onChange={(event) => setDraft((current) => ({ ...current, name: event.target.value }))} />
            <TextField select label="连接方式" disabled={!canManage} value={draft.kind} onChange={(event) => {
              const kind = event.target.value as IntegrationKind
              setDraft((current) => ({
                ...current,
                kind,
                operations: current.operations.map((item) => ({
                  ...item,
                  method: kind === 'mcp_http' ? 'POST' : item.mode === 'read' ? 'GET' : item.method,
                })),
              }))
            }}>
              <MenuItem value="http">HTTP API</MenuItem><MenuItem value="mcp_http">MCP over HTTP</MenuItem>
            </TextField>
            <TextField label="连接地址" required disabled={!canManage} value={draft.base_url} onChange={(event) => setDraft((current) => ({ ...current, base_url: event.target.value }))} placeholder="https://api.example.com" />
            <TextField select label="生效范围" disabled={!canManage} value={draft.scope_type} onChange={(event) => setDraft((current) => ({ ...current, scope_type: event.target.value as ScopeType, scope_value: '', market_id: '' }))}>
              <MenuItem value="global">全部范围</MenuItem><MenuItem value="market">指定市场</MenuItem><MenuItem value="channel">指定渠道</MenuItem>
            </TextField>
            {draft.scope_type === 'market' ? <TextField label="市场编号" type="number" required disabled={!canManage} value={draft.market_id} onChange={(event) => setDraft((current) => ({ ...current, market_id: event.target.value }))} /> : null}
            {draft.scope_type === 'channel' ? <TextField label="渠道" required disabled={!canManage} value={draft.scope_value} onChange={(event) => setDraft((current) => ({ ...current, scope_value: event.target.value }))} /> : null}
          </Box>
          <TextField label="说明" multiline minRows={2} disabled={!canManage} value={draft.description} onChange={(event) => setDraft((current) => ({ ...current, description: event.target.value }))} />
          <OperatorTechnicalDisclosure title="连接设置" summary="凭据、白名单和响应限制">
            <Stack spacing={1.5}>
              <TextField label="凭据引用" disabled={!canManage} value={draft.credential_ref} onChange={(event) => setDraft((current) => ({ ...current, credential_ref: event.target.value }))} helperText="仅填写系统密钥引用，不填写真实密钥" />
              <TextField label="允许访问的主机" helperText="每行一个精确主机名" multiline minRows={2} disabled={!canManage} value={draft.host_allowlist} onChange={(event) => setDraft((current) => ({ ...current, host_allowlist: event.target.value }))} />
              <Box sx={{ display: 'grid', gap: 1.25, gridTemplateColumns: { xs: '1fr', md: 'repeat(2, minmax(0, 1fr))' } }}>
                <TextField label="超时秒数" type="number" disabled={!canManage} value={draft.timeout_seconds} onChange={(event) => setDraft((current) => ({ ...current, timeout_seconds: event.target.value }))} />
                <TextField label="最大响应字节" type="number" disabled={!canManage} value={draft.max_response_bytes} onChange={(event) => setDraft((current) => ({ ...current, max_response_bytes: event.target.value }))} />
              </Box>
            </Stack>
          </OperatorTechnicalDisclosure>
          <Divider><Chip label="可用操作" /></Divider>
          {draft.operations.map((operation, index) => (
            <Paper key={`${operation.key}-${index}`} variant="outlined" sx={{ p: 1.5 }}>
              <Stack spacing={1.25}>
                <Stack direction="row" spacing={1} sx={{ alignItems: 'center', justifyContent: 'space-between' }}>
                  <Typography variant="subtitle2">操作 {index + 1}</Typography>
                  {canManage ? <Button size="small" color="error" startIcon={<DeleteOutlineRoundedIcon />} onClick={() => setDraft((current) => ({ ...current, operations: current.operations.filter((_, itemIndex) => itemIndex !== index) }))}>删除</Button> : null}
                </Stack>
                <Box sx={{ display: 'grid', gap: 1.25, gridTemplateColumns: { xs: '1fr', md: 'repeat(2, minmax(0, 1fr))' } }}>
                  <TextField label="操作编号" required disabled={!canManage} value={operation.key} onChange={(event) => updateOperation(index, { key: event.target.value })} />
                  <TextField select label="操作类型" disabled={!canManage} value={operation.mode} onChange={(event) => updateOperation(index, { mode: event.target.value as OperationMode })}>
                    <MenuItem value="read">查询</MenuItem><MenuItem value="write">修改</MenuItem>
                  </TextField>
                  <TextField select label="请求方法" disabled={!canManage || draft.kind === 'mcp_http' || operation.mode === 'read'} value={draft.kind === 'mcp_http' ? 'POST' : operation.method} onChange={(event) => updateOperation(index, { method: event.target.value })}>
                    {['POST', 'PUT', 'PATCH', 'DELETE'].map((method) => <MenuItem key={method} value={method}>{method}</MenuItem>)}
                    {operation.mode === 'read' ? <MenuItem value="GET">GET</MenuItem> : null}
                  </TextField>
                  <TextField label="请求路径" required disabled={!canManage} value={operation.path} onChange={(event) => updateOperation(index, { path: event.target.value })} />
                  <TextField select label="风险等级" disabled={!canManage} value={operation.risk_level} onChange={(event) => updateOperation(index, { risk_level: event.target.value as OperationDraft['risk_level'] })}>
                    <MenuItem value="low">低</MenuItem><MenuItem value="medium">中</MenuItem><MenuItem value="high">高</MenuItem>
                  </TextField>
                </Box>
                <TextField label="操作说明" required disabled={!canManage} value={operation.description} onChange={(event) => updateOperation(index, { description: event.target.value })} />
                <OperatorTechnicalDisclosure title="数据字段" summary="输入格式和返回字段">
                  <Stack spacing={1.25}>
                    <TextField label="输入字段" helperText="每行 name:type:required" multiline minRows={3} disabled={!canManage} value={operation.schema_fields} onChange={(event) => updateOperation(index, { schema_fields: event.target.value })} />
                    <TextField label="允许返回的字段" helperText="每行一个 JSON 路径；留空使用系统脱敏结果" multiline minRows={2} disabled={!canManage} value={operation.result_allowlist} onChange={(event) => updateOperation(index, { result_allowlist: event.target.value })} />
                  </Stack>
                </OperatorTechnicalDisclosure>
                <Stack direction="row" spacing={2} useFlexGap sx={{ flexWrap: 'wrap' }}>
                  <FormControlLabel control={<Checkbox disabled={!canManage || operation.mode === 'write'} checked={operation.mode === 'write' || operation.requires_confirmation} onChange={(event) => updateOperation(index, { requires_confirmation: event.target.checked })} />} label="要求客户确认" />
                  <FormControlLabel control={<Checkbox disabled={!canManage} checked={operation.enabled} onChange={(event) => updateOperation(index, { enabled: event.target.checked })} />} label="启用" />
                </Stack>
              </Stack>
            </Paper>
          ))}
          {canManage ? <Button variant="outlined" startIcon={<AddRoundedIcon />} onClick={() => setDraft((current) => ({ ...current, operations: [...current.operations, emptyOperation()] }))}>添加操作</Button> : null}
          <TextField label="版本摘要" multiline minRows={2} disabled={!canManage} value={draft.draft_summary} onChange={(event) => setDraft((current) => ({ ...current, draft_summary: event.target.value }))} />
          <FormControlLabel control={<Switch disabled={!canManage} checked={draft.is_active} onChange={(event) => setDraft((current) => ({ ...current, is_active: event.target.checked }))} />} label="启用外部系统" />
          {canManage ? <Stack direction="row" spacing={1}><Button variant="contained" disabled={save.isPending} startIcon={save.isPending ? <CircularProgress color="inherit" size={16} /> : <SaveRoundedIcon />} onClick={() => save.mutate(false)}>保存草稿</Button><Button variant="outlined" disabled={save.isPending} startIcon={<PublishRoundedIcon />} onClick={() => save.mutate(true)}>保存并发布</Button></Stack> : null}
        </Stack>
      </Paper>

      <Paper component="aside" variant="outlined" sx={{ p: 2, alignSelf: 'start' }}>
        <Typography component="h2" variant="h3">连接测试</Typography>
        <Stack spacing={1.5} sx={{ mt: 2 }}>
          <TextField select label="操作" value={testOperation} onChange={(event) => setTestOperation(event.target.value)}>
            {draft.operations.map((item) => <MenuItem key={item.key} value={item.key}>{item.description || item.key || '未命名操作'} · {item.mode === 'read' ? '查询' : '修改'}</MenuItem>)}
          </TextField>
          <TextField label="测试参数" helperText="JSON 格式" multiline minRows={6} value={testArguments} onChange={(event) => setTestArguments(event.target.value)} />
          <Button variant="outlined" disabled={!selected || selected.published_version <= 0 || !testOperation || test.isPending} startIcon={test.isPending ? <CircularProgress size={16} /> : <PlayArrowRoundedIcon />} onClick={() => test.mutate()}>
            测试已发布版本
          </Button>
          {test.error ? <OperatorErrorNotice title="测试失败" error={test.error} fallback="请检查凭据、连接地址、适用范围和测试参数" /> : null}
          {test.data ? <OperatorTechnicalDisclosure title="测试结果"><Box component="pre" sx={{ m: 0, maxHeight: 420, overflow: 'auto', whiteSpace: 'pre-wrap', fontSize: 12 }}>{JSON.stringify(test.data, null, 2)}</Box></OperatorTechnicalDisclosure> : null}
        </Stack>
      </Paper>
    </Box>
  )
}

function riskLabel(value: string) {
  const labels: Record<string, string> = { low: '低', medium: '中', high: '高', critical: '关键' }
  return labels[value] || value
}

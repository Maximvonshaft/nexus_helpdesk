import FactCheckRoundedIcon from '@mui/icons-material/FactCheckRounded'
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  MenuItem,
  Paper,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import { useMutation } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import {
  OperatorEmptyState,
  OperatorErrorNotice,
  OperatorTechnicalDisclosure,
} from '@/app/OperatorPresentation'
import { agentRuntimeApi } from '@/lib/agentRuntimeApi'
import type { AgentControlSnapshot } from '@/lib/types'
import { asString, contentOf } from './formUtils'

export function DiagnosticsPanel({
  snapshot,
  tenantKey,
}: {
  snapshot: AgentControlSnapshot
  tenantKey: string
}) {
  const mcpResources = useMemo(
    () => snapshot.resources.filter(
      (item) => item.config_type === 'integration'
        && item.published_version > 0
        && asString(contentOf(item).kind) === 'mcp_http',
    ),
    [snapshot.resources],
  )
  const [integrationKey, setIntegrationKey] = useState(mcpResources[0]?.resource_key || '')
  useEffect(() => {
    if (!mcpResources.some((item) => item.resource_key === integrationKey)) {
      setIntegrationKey(mcpResources[0]?.resource_key || '')
    }
  }, [integrationKey, mcpResources])

  const doctor = useMutation({
    mutationFn: () => agentRuntimeApi.doctorMcp({
      tenant_key: tenantKey,
      environment: snapshot.scope.environment,
      market_id: snapshot.scope.market_id,
      channel: snapshot.scope.channel,
      language: snapshot.scope.language,
      case_type: snapshot.scope.case_type,
      integration_key: integrationKey,
    }),
  })

  return (
    <Stack spacing={2}>
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
          <FactCheckRoundedIcon color="primary" />
          <Typography component="h2" variant="h2">运行诊断</Typography>
        </Stack>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 0.75 }}>
          使用当前作用域已部署的不可变 Agent Release 验证 MCP 生命周期、协议版本、Tool 发现和 Schema 漂移。诊断不会发布资源、扩展 Tool 权限或执行任何业务操作。
        </Typography>
        {snapshot.resolved_agent ? (
          <Alert severity="info" variant="outlined" sx={{ mt: 2 }}>
            当前诊断绑定 Deployment 与 Release 摘要 {snapshot.resolved_agent_digest?.slice(0, 12)}。
          </Alert>
        ) : (
          <Alert severity="warning" sx={{ mt: 2 }}>
            当前作用域尚未解析到已部署 Agent Release，无法执行权威诊断。
          </Alert>
        )}
      </Paper>

      {!mcpResources.length ? (
        <OperatorEmptyState
          title="没有已发布的 MCP 集成"
          description="先在工具与集成中创建、发布 MCP over HTTP 资源，并绑定到 Agent Release。"
        />
      ) : (
        <Paper variant="outlined" sx={{ p: 2 }}>
          <Stack spacing={1.5}>
            <TextField
              select
              label="MCP 集成"
              value={integrationKey}
              onChange={(event) => setIntegrationKey(event.target.value)}
            >
              {mcpResources.map((item) => (
                <MenuItem key={item.id} value={item.resource_key}>
                  {item.name} · {item.resource_key} · v{item.published_version}
                </MenuItem>
              ))}
            </TextField>
            <Button
              variant="contained"
              disabled={!snapshot.resolved_agent || !integrationKey || doctor.isPending}
              startIcon={doctor.isPending ? <CircularProgress color="inherit" size={16} /> : <FactCheckRoundedIcon />}
              onClick={() => doctor.mutate()}
            >
              运行 MCP Doctor
            </Button>
            {doctor.error ? (
              <OperatorErrorNotice
                title="MCP 诊断失败"
                error={doctor.error}
                fallback="请检查当前 Deployment、凭据、主机白名单和 MCP Server"
              />
            ) : null}
          </Stack>
        </Paper>
      )}

      {doctor.data ? (
        <Paper variant="outlined" sx={{ p: 2 }}>
          <Stack
            direction={{ xs: 'column', sm: 'row' }}
            spacing={1}
            sx={{ alignItems: { sm: 'center' }, justifyContent: 'space-between' }}
          >
            <Box>
              <Typography component="h2" variant="h3">诊断结果</Typography>
              <Typography variant="caption" color="text.secondary">
                Release v{doctor.data.agent_release_version} · {doctor.data.protocol_version || '协议未协商'} · {doctor.data.elapsed_ms} ms
              </Typography>
            </Box>
            <Chip
              color={doctor.data.healthy ? 'success' : 'error'}
              label={doctor.data.healthy ? '健康' : '阻断发布/运行'}
            />
          </Stack>
          <Stack spacing={1} sx={{ mt: 2 }}>
            {doctor.data.checks.map((check) => (
              <Alert key={check.label} severity={check.passed ? 'success' : 'error'} variant="outlined">
                {check.label}：{check.detail || (check.passed ? '通过' : '失败')}
              </Alert>
            ))}
          </Stack>
          <Box
            sx={{
              display: 'grid',
              gridTemplateColumns: { xs: '1fr', md: 'repeat(3, minmax(0, 1fr))' },
              gap: 1,
              mt: 2,
            }}
          >
            <Paper variant="outlined" sx={{ p: 1.5 }}>
              <Typography variant="caption" color="text.secondary">已配置 / 已发现 Tool</Typography>
              <Typography variant="h3">{doctor.data.configured_tool_count} / {doctor.data.discovered_tool_count}</Typography>
            </Paper>
            <Paper variant="outlined" sx={{ p: 1.5 }}>
              <Typography variant="caption" color="text.secondary">缺失 / Schema 漂移</Typography>
              <Typography variant="h3">{doctor.data.missing_tools.length} / {doctor.data.schema_mismatches.length}</Typography>
            </Paper>
            <Paper variant="outlined" sx={{ p: 1.5 }}>
              <Typography variant="caption" color="text.secondary">未纳管 Tool</Typography>
              <Typography variant="h3">{doctor.data.unmanaged_tools.length}</Typography>
            </Paper>
          </Box>
          {doctor.data.unmanaged_tools.length ? (
            <Alert severity="info" variant="outlined" sx={{ mt: 2 }}>
              Server 发现了未绑定到 Release 的 Tool：{doctor.data.unmanaged_tools.join(', ')}。这些 Tool 已被隔离，不会自动进入 Agent。
            </Alert>
          ) : null}
          <OperatorTechnicalDisclosure title="诊断证据">
            <Box component="pre" sx={{ m: 0, maxHeight: 480, overflow: 'auto', whiteSpace: 'pre-wrap', fontSize: 12 }}>
              {JSON.stringify(doctor.data, null, 2)}
            </Box>
          </OperatorTechnicalDisclosure>
        </Paper>
      ) : null}
    </Stack>
  )
}

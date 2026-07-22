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
      <Paper component="section" variant="outlined" sx={{ p: 2 }}>
        <Stack direction="row" spacing={1} sx={{ alignItems: 'center', justifyContent: 'space-between' }}>
          <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
            <FactCheckRoundedIcon color="primary" aria-hidden="true" />
            <Typography component="h2" variant="h2">连接诊断</Typography>
          </Stack>
          <Chip
            color={snapshot.resolved_agent ? 'success' : 'warning'}
            label={snapshot.resolved_agent ? '配置可检查' : '当前范围未生效'}
          />
        </Stack>
      </Paper>

      {!mcpResources.length ? (
        <OperatorEmptyState
          title="没有可检查的外部系统"
          description="请先在工具与集成中发布连接配置，并将其加入处理方案。"
        />
      ) : (
        <Paper component="section" variant="outlined" sx={{ p: 2 }}>
          <Stack spacing={1.5}>
            <TextField
              select
              label="外部系统"
              value={integrationKey}
              onChange={(event) => setIntegrationKey(event.target.value)}
            >
              {mcpResources.map((item) => (
                <MenuItem key={item.id} value={item.resource_key}>
                  {item.name} · v{item.published_version}
                </MenuItem>
              ))}
            </TextField>
            <Button
              variant="contained"
              disabled={!snapshot.resolved_agent || !integrationKey || doctor.isPending}
              startIcon={doctor.isPending ? <CircularProgress color="inherit" size={16} /> : <FactCheckRoundedIcon />}
              onClick={() => doctor.mutate()}
            >
              检查连接
            </Button>
            {doctor.error ? (
              <OperatorErrorNotice
                title="连接检查失败"
                error={doctor.error}
                fallback="请检查连接地址、凭据和访问范围"
              />
            ) : null}
          </Stack>
        </Paper>
      )}

      {doctor.data ? (
        <Paper component="section" variant="outlined" sx={{ p: 2 }}>
          <Stack
            direction={{ xs: 'column', sm: 'row' }}
            spacing={1}
            sx={{ alignItems: { sm: 'center' }, justifyContent: 'space-between' }}
          >
            <Box>
              <Typography component="h2" variant="h3">检查结果</Typography>
              <Typography variant="caption" color="text.secondary">
                用时 {doctor.data.elapsed_ms} ms
              </Typography>
            </Box>
            <Chip
              color={doctor.data.healthy ? 'success' : 'error'}
              label={doctor.data.healthy ? '可用' : '需要处理'}
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
              <Typography variant="caption" color="text.secondary">已配置 / 已发现工具</Typography>
              <Typography variant="h3">{doctor.data.configured_tool_count} / {doctor.data.discovered_tool_count}</Typography>
            </Paper>
            <Paper variant="outlined" sx={{ p: 1.5 }}>
              <Typography variant="caption" color="text.secondary">缺失 / 配置不一致</Typography>
              <Typography variant="h3">{doctor.data.missing_tools.length} / {doctor.data.schema_mismatches.length}</Typography>
            </Paper>
            <Paper variant="outlined" sx={{ p: 1.5 }}>
              <Typography variant="caption" color="text.secondary">未纳入配置</Typography>
              <Typography variant="h3">{doctor.data.unmanaged_tools.length}</Typography>
            </Paper>
          </Box>
          {doctor.data.unmanaged_tools.length ? (
            <Alert severity="info" variant="outlined" sx={{ mt: 2 }}>
              发现 {doctor.data.unmanaged_tools.length} 个未纳入当前配置的工具，系统不会调用这些工具。
            </Alert>
          ) : null}
          <OperatorTechnicalDisclosure title="诊断详情" summary="协议、版本和检查证据">
            <Box component="pre" sx={{ m: 0, maxHeight: 480, overflow: 'auto', whiteSpace: 'pre-wrap', fontSize: 12 }}>
              {JSON.stringify(doctor.data, null, 2)}
            </Box>
          </OperatorTechnicalDisclosure>
        </Paper>
      ) : null}
    </Stack>
  )
}

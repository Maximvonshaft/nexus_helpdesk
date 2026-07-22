import PsychologyRoundedIcon from '@mui/icons-material/PsychologyRounded'
import {
  Alert,
  Box,
  CircularProgress,
  Paper,
  Stack,
  Tab,
  Tabs,
  Typography,
} from '@mui/material'
import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { OperatorErrorNotice } from '@/app/OperatorPresentation'
import { agentControlApi } from '@/lib/agentControlApi'
import { DiagnosticsPanel } from './DiagnosticsPanel'
import { OverviewPanel } from './OverviewPanel'
import { PersonaPanel } from './PersonaPanel'
import { PlaybookPanel } from './PlaybookPanel'
import { ReleaseDeliveryPanel } from './ReleaseDeliveryPanel'
import { RunExplorerPanel } from './RunExplorerPanel'
import { ToolsIntegrationsPanel } from './ToolsIntegrationsPanel'
import { RuntimePanel } from './RuntimePanel'

export type AgentControlTab = 'overview' | 'delivery' | 'persona' | 'playbooks' | 'tools' | 'runtime' | 'diagnostics'

export function AgentControlPage({ canManage }: { canManage: boolean }) {
  const [tab, setTab] = useState<AgentControlTab>('overview')
  const [tenantKey, setTenantKey] = useState('')
  const [environment, setEnvironment] = useState<'test' | 'staging' | 'production'>('production')
  const [marketId, setMarketId] = useState('')
  const [channel, setChannel] = useState('webchat')
  const [language, setLanguage] = useState('')
  const [caseType, setCaseType] = useState('')
  const parsedMarketId = marketId.trim() ? Number(marketId) : null
  const snapshot = useQuery({
    queryKey: [
      'agentControlSnapshot',
      tenantKey,
      environment,
      parsedMarketId,
      channel,
      language,
      caseType,
    ],
    queryFn: () => agentControlApi.snapshot({
      tenantKey: tenantKey || undefined,
      environment,
      marketId: Number.isFinite(parsedMarketId) ? parsedMarketId : null,
      channel,
      language: language || null,
      caseType: caseType || null,
    }),
    refetchInterval: 30_000,
    retry: false,
  })

  useEffect(() => { document.title = 'Agent 控制面 · Nexus OSR' }, [])
  useEffect(() => {
    if (!tenantKey && snapshot.data?.tenant_key) setTenantKey(snapshot.data.tenant_key)
  }, [snapshot.data?.tenant_key, tenantKey])

  return (
    <Box component="main" sx={{ p: { xs: 1.5, md: 2.5 } }}>
      <Stack
        direction={{ xs: 'column', sm: 'row' }}
        spacing={2}
        sx={{
          alignItems: { xs: 'stretch', sm: 'flex-start' },
          justifyContent: 'space-between',
        }}
      >
        <Box>
          <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
            <PsychologyRoundedIcon color="primary" aria-hidden="true" />
            <Typography component="h1" variant="h1">Agent 控制面</Typography>
          </Stack>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.75 }}>
            统一管理 Agent 定义、不可变发布、渠道部署、人格、业务剧本、工具、模型、运行策略和运行诊断；知识继续由唯一知识库维护，并在 Agent Definition 中按版本绑定。
          </Typography>
        </Box>
        <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
          {!canManage ? <Alert severity="info" variant="outlined">只读</Alert> : null}
          {snapshot.isFetching ? <CircularProgress size={22} aria-label="正在刷新" /> : null}
        </Stack>
      </Stack>

      <Paper variant="outlined" sx={{ mt: 2.5, overflow: 'hidden' }}>
        <Tabs
          value={tab}
          onChange={(_, next: AgentControlTab) => setTab(next)}
          variant="scrollable"
          scrollButtons="auto"
          aria-label="Agent 控制面分类"
        >
          <Tab value="overview" label="定义、发布与测试" />
          <Tab value="delivery" label="小范围发布" />
          <Tab value="persona" label="人格" />
          <Tab value="playbooks" label="业务剧本" />
          <Tab value="tools" label="工具与集成" />
          <Tab value="runtime" label="模型与运行" />
          <Tab value="diagnostics" label="运行诊断" />
        </Tabs>
      </Paper>

      {snapshot.isError ? (
        <Box sx={{ mt: 2 }}>
          <OperatorErrorNotice
            title="无法读取 Agent 控制面"
            error={snapshot.error}
            fallback="请检查控制面服务"
          />
        </Box>
      ) : snapshot.isLoading || !snapshot.data ? (
        <Stack sx={{ minHeight: 320, alignItems: 'center', justifyContent: 'center' }}>
          <CircularProgress />
        </Stack>
      ) : (
        <Box sx={{ mt: 2 }}>
          {tab === 'overview' ? (
            <OverviewPanel
              snapshot={{
                ...snapshot.data,
                resolution_error: deploymentResolutionMessage(snapshot.data.resolution_error),
              }}
              tenantKey={tenantKey || snapshot.data.tenant_key}
              setTenantKey={setTenantKey}
              environment={environment}
              setEnvironment={setEnvironment}
              marketId={marketId}
              setMarketId={setMarketId}
              channel={channel}
              setChannel={setChannel}
              language={language}
              setLanguage={setLanguage}
              caseType={caseType}
              setCaseType={setCaseType}
              canManage={canManage && snapshot.data.capabilities.can_manage}
              canDeploy={snapshot.data.capabilities.can_deploy}
            />
          ) : null}
          {tab === 'delivery' ? (
            <ReleaseDeliveryPanel
              snapshot={snapshot.data}
              canDeploy={snapshot.data.capabilities.can_deploy}
            />
          ) : null}
          {tab === 'persona' ? (
            <PersonaPanel
              snapshot={snapshot.data}
              tenantKey={tenantKey || snapshot.data.tenant_key}
              canManage={canManage && snapshot.data.capabilities.can_manage}
              canDeploy={snapshot.data.capabilities.can_deploy}
            />
          ) : null}
          {tab === 'playbooks' ? (
            <PlaybookPanel
              snapshot={snapshot.data}
              canManage={canManage && snapshot.data.capabilities.can_manage}
            />
          ) : null}
          {tab === 'tools' ? (
            <ToolsIntegrationsPanel
              snapshot={snapshot.data}
              canManage={canManage && snapshot.data.capabilities.can_manage}
              tenantKey={tenantKey || snapshot.data.tenant_key}
            />
          ) : null}
          {tab === 'runtime' ? (
            <RuntimePanel
              snapshot={snapshot.data}
              canManage={canManage && snapshot.data.capabilities.can_manage}
            />
          ) : null}
          {tab === 'diagnostics' ? (
            <Stack spacing={2}>
              <DiagnosticsPanel
                snapshot={snapshot.data}
                tenantKey={tenantKey || snapshot.data.tenant_key}
              />
              <RunExplorerPanel
                tenantKey={tenantKey || snapshot.data.tenant_key}
                scope={snapshot.data.scope}
                canExecute={snapshot.data.capabilities.can_deploy}
              />
            </Stack>
          ) : null}
        </Box>
      )}
    </Box>
  )
}

function deploymentResolutionMessage(value?: string | null) {
  if (!value) return null
  if (
    value === 'agent_deployment_not_found'
    || value === 'agent_deployment_unavailable'
  ) return '未找到匹配当前范围的 Agent Deployment。'
  if (value === 'ambiguous_agent_deployment_scope') return '当前范围命中了多个 Agent Deployment，请收敛作用域。'
  return '当前 Agent Deployment 暂不可用，请检查已发布 Release 和部署范围。'
}

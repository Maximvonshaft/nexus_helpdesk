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
import { useEffect, useState, type ReactNode } from 'react'
import { OperatorErrorNotice } from '@/app/OperatorPresentation'
import { agentControlApi } from '@/lib/agentControlApi'
import { OverviewPanel } from './OverviewPanel'
import { PersonaPanel } from './PersonaPanel'
import { PlaybookPanel } from './PlaybookPanel'
import { ToolsIntegrationsPanel } from './ToolsIntegrationsPanel'
import { RuntimePanel } from './RuntimePanel'
import { MemoryPanel } from './MemoryPanel'

type AgentControlTab = 'overview' | 'persona' | 'knowledge' | 'playbooks' | 'tools' | 'runtime' | 'memory'

export function AgentControlPage({ canManage, knowledgePage }: { canManage: boolean; knowledgePage: ReactNode }) {
  const [tab, setTab] = useState<AgentControlTab>('overview')
  const [tenantKey, setTenantKey] = useState('default')
  const [channel, setChannel] = useState('webchat')
  const [language, setLanguage] = useState('')
  const snapshot = useQuery({
    queryKey: ['agentControlSnapshot', tenantKey, channel, language],
    queryFn: () => agentControlApi.snapshot({ tenantKey, channel, language: language || null }),
    refetchInterval: 30_000,
    retry: false,
  })

  useEffect(() => { document.title = 'Agent 配置 · Nexus OSR' }, [])

  return (
    <Box component="main" sx={{ p: { xs: 1.5, md: 2.5 } }}>
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} sx={{ alignItems: { xs: 'stretch', sm: 'flex-start' }, justifyContent: 'space-between' }}>
        <Box>
          <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
            <PsychologyRoundedIcon color="primary" aria-hidden="true" />
            <Typography component="h1" variant="h1">Agent 配置</Typography>
          </Stack>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.75 }}>
            统一管理人格、知识、业务剧本、工具与集成、模型运行和客户记忆。所有发布内容进入同一 Agent Runtime。
          </Typography>
        </Box>
        <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
          {!canManage ? <Alert severity="info" variant="outlined">只读</Alert> : null}
          {snapshot.isFetching ? <CircularProgress size={22} aria-label="正在刷新" /> : null}
        </Stack>
      </Stack>

      <Paper variant="outlined" sx={{ mt: 2.5, overflow: 'hidden' }}>
        <Tabs value={tab} onChange={(_, next: AgentControlTab) => setTab(next)} variant="scrollable" scrollButtons="auto" aria-label="Agent 配置分类">
          <Tab value="overview" label="总览与测试" />
          <Tab value="persona" label="人格" />
          <Tab value="knowledge" label="知识" />
          <Tab value="playbooks" label="业务剧本" />
          <Tab value="tools" label="工具与集成" />
          <Tab value="runtime" label="模型与运行" />
          <Tab value="memory" label="记忆" />
        </Tabs>
      </Paper>

      {snapshot.isError ? (
        <Box sx={{ mt: 2 }}><OperatorErrorNotice title="无法读取 Agent 配置" error={snapshot.error} fallback="请检查控制面服务" /></Box>
      ) : snapshot.isLoading || !snapshot.data ? (
        <Stack sx={{ minHeight: 320, alignItems: 'center', justifyContent: 'center' }}><CircularProgress /></Stack>
      ) : (
        <Box sx={{ mt: 2 }}>
          {tab === 'overview' ? <OverviewPanel snapshot={snapshot.data} tenantKey={tenantKey} setTenantKey={setTenantKey} channel={channel} setChannel={setChannel} language={language} setLanguage={setLanguage} /> : null}
          {tab === 'persona' ? <PersonaPanel snapshot={snapshot.data} canManage={canManage} /> : null}
          {tab === 'knowledge' ? knowledgePage : null}
          {tab === 'playbooks' ? <PlaybookPanel snapshot={snapshot.data} canManage={canManage} /> : null}
          {tab === 'tools' ? <ToolsIntegrationsPanel snapshot={snapshot.data} canManage={canManage} tenantKey={tenantKey} /> : null}
          {tab === 'runtime' ? <RuntimePanel snapshot={snapshot.data} canManage={canManage} /> : null}
          {tab === 'memory' ? <MemoryPanel snapshot={snapshot.data} canManage={canManage} tenantKey={tenantKey} /> : null}
        </Box>
      )}
    </Box>
  )
}

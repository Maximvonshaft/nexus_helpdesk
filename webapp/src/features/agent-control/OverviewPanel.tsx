import CheckCircleRoundedIcon from '@mui/icons-material/CheckCircleRounded'
import PlayArrowRoundedIcon from '@mui/icons-material/PlayArrowRounded'
import PublishRoundedIcon from '@mui/icons-material/PublishRounded'
import RocketLaunchRoundedIcon from '@mui/icons-material/RocketLaunchRounded'
import ScienceRoundedIcon from '@mui/icons-material/ScienceRounded'
import {
  Alert,
  Box,
  Button,
  Checkbox,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Divider,
  FormControlLabel,
  MenuItem,
  Paper,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import { useMutation, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { OperatorErrorNotice, OperatorTechnicalDisclosure } from '@/app/OperatorPresentation'
import { agentControlApi } from '@/lib/agentControlApi'
import type {
  AgentConfigResource,
  AgentControlSnapshot,
  AgentDefinition,
  AgentRelease,
} from '@/lib/types'

interface Props {
  snapshot: AgentControlSnapshot
  tenantKey: string
  setTenantKey: (value: string) => void
  environment: 'test' | 'staging' | 'production'
  setEnvironment: (value: 'test' | 'staging' | 'production') => void
  marketId: string
  setMarketId: (value: string) => void
  channel: string
  setChannel: (value: string) => void
  language: string
  setLanguage: (value: string) => void
  caseType: string
  setCaseType: (value: string) => void
  canManage: boolean
  canDeploy: boolean
}

type ResourceRef = { resource_key: string; version: number }
type PersonaRef = { profile_key: string; version: number }
type KnowledgeRef = { item_key: string; version: number }

interface ComposerState {
  definitionKey: string
  name: string
  purpose: string
  persona: string
  playbooks: string[]
  integrations: string[]
  modelProfile: string
  runtimePolicy: string
  knowledge: string[]
}

const EMPTY_COMPOSER: ComposerState = {
  definitionKey: '',
  name: '',
  purpose: '',
  persona: '',
  playbooks: [],
  integrations: [],
  modelProfile: '',
  runtimePolicy: '',
  knowledge: [],
}

export function OverviewPanel({
  snapshot,
  tenantKey,
  setTenantKey,
  environment,
  setEnvironment,
  marketId,
  setMarketId,
  channel,
  setChannel,
  language,
  setLanguage,
  caseType,
  setCaseType,
  canManage,
  canDeploy,
}: Props) {
  const queryClient = useQueryClient()
  const [composer, setComposer] = useState<ComposerState>(EMPTY_COMPOSER)
  const [editingDefinitionId, setEditingDefinitionId] = useState<number | null>(null)
  const [testMessage, setTestMessage] = useState('我的包裹在哪里？')
  const [generateReply, setGenerateReply] = useState(false)
  const [pendingVersion, setPendingVersion] = useState<AgentRelease | null>(null)

  const publishedPlaybooks = useMemo(
    () => resources(snapshot.resources, 'playbook'),
    [snapshot.resources],
  )
  const publishedIntegrations = useMemo(
    () => resources(snapshot.resources, 'integration'),
    [snapshot.resources],
  )
  const publishedModels = useMemo(
    () => resources(snapshot.resources, 'model_profile'),
    [snapshot.resources],
  )
  const publishedRuntimePolicies = useMemo(
    () => resources(snapshot.resources, 'runtime_policy'),
    [snapshot.resources],
  )
  const publishedPersonas = useMemo(
    () => snapshot.personas.filter((item) => item.is_active && item.published_version > 0),
    [snapshot.personas],
  )
  const publishedKnowledge = useMemo(
    () => snapshot.knowledge.filter(
      (item) => item.published_version > 0 && item.indexed_version >= item.published_version,
    ),
    [snapshot.knowledge],
  )

  useEffect(() => {
    setComposer((current) => ({
      ...current,
      playbooks: current.playbooks.length
        ? current.playbooks
        : publishedPlaybooks.map(resourceValue),
      modelProfile: current.modelProfile || resourceValue(publishedModels[0]),
      runtimePolicy: current.runtimePolicy || resourceValue(publishedRuntimePolicies[0]),
    }))
  }, [publishedModels, publishedPlaybooks, publishedRuntimePolicies])

  const createOrUpdate = useMutation({
    mutationFn: async () => {
      const manifest = buildManifest(composer)
      if (editingDefinitionId) {
        return agentControlApi.updateDefinition(editingDefinitionId, tenantKey, {
          name: composer.name,
          purpose: composer.purpose || null,
          draft_manifest: manifest,
        })
      }
      return agentControlApi.createDefinition({
        tenant_key: tenantKey,
        definition_key: composer.definitionKey,
        name: composer.name,
        purpose: composer.purpose || null,
        draft_manifest: manifest,
      })
    },
    onSuccess: async () => {
      setEditingDefinitionId(null)
      setComposer(defaultComposer(snapshot))
      await invalidate(queryClient)
    },
  })

  const publishVersion = useMutation({
    mutationFn: (definition: AgentDefinition) =>
      agentControlApi.releaseDefinition(definition.id, tenantKey),
    onSuccess: () => invalidate(queryClient),
  })

  const applyVersion = useMutation({
    mutationFn: (target: AgentRelease) => agentControlApi.deployRelease({
      tenant_key: tenantKey,
      environment,
      release_id: target.id,
      market_id: parsedMarketId(marketId),
      channel: channel || null,
      language: language || null,
      case_type: caseType || null,
    }),
    onSuccess: async () => {
      setPendingVersion(null)
      await invalidate(queryClient)
    },
  })

  const testReply = useMutation({
    mutationFn: () => agentControlApi.playground({
      tenant_key: tenantKey,
      environment,
      market_id: parsedMarketId(marketId),
      channel,
      language: language || null,
      case_type: caseType || null,
      cohort_key: 'operator-playground',
      body: testMessage,
      execute_model: generateReply,
    }),
  })

  const activeReleaseId = useMemo(() => {
    const deployment = snapshot.deployments.find(
      (item) => item.environment === environment
        && nullableNumber(item.market_id) === parsedMarketId(marketId)
        && (item.channel || '') === channel
        && (item.language || '') === language
        && (item.case_type || '') === caseType,
    )
    return deployment?.active_release_id ?? null
  }, [caseType, channel, environment, language, marketId, snapshot.deployments])

  const readyToSave = Boolean(
    composer.name.trim()
      && (editingDefinitionId || composer.definitionKey.trim())
      && composer.modelProfile
      && composer.runtimePolicy
      && composer.playbooks.length,
  )

  return (
    <Stack spacing={2}>
      <Paper component="section" variant="outlined" sx={{ p: 2 }}>
        <Typography component="h2" variant="h2">适用范围</Typography>
        <Box
          sx={{
            display: 'grid',
            gridTemplateColumns: { xs: '1fr', md: 'repeat(3, minmax(0, 1fr))' },
            gap: 1.5,
            mt: 2,
          }}
        >
          <TextField label="租户编号" value={tenantKey} onChange={(event) => setTenantKey(event.target.value)} />
          <TextField
            select
            label="环境"
            value={environment}
            onChange={(event) => setEnvironment(event.target.value as Props['environment'])}
          >
            <MenuItem value="test">测试</MenuItem>
            <MenuItem value="staging">预发布</MenuItem>
            <MenuItem value="production">生产</MenuItem>
          </TextField>
          <TextField
            label="市场编号（可选）"
            value={marketId}
            onChange={(event) => setMarketId(event.target.value.replace(/[^0-9]/g, ''))}
          />
          <TextField label="渠道" value={channel} onChange={(event) => setChannel(event.target.value)} />
          <TextField label="语言（可选）" value={language} onChange={(event) => setLanguage(event.target.value)} />
          <TextField label="业务类型（可选）" value={caseType} onChange={(event) => setCaseType(event.target.value)} />
        </Box>
        <Stack
          direction={{ xs: 'column', sm: 'row' }}
          spacing={1}
          sx={{ mt: 2, alignItems: { sm: 'center' } }}
        >
          {snapshot.resolved_agent ? (
            <Alert severity="success" icon={<CheckCircleRoundedIcon />} sx={{ flex: 1 }}>
              当前生效版本：{releaseLabel(snapshot.resolved_agent)}
            </Alert>
          ) : (
            <Alert severity="warning" sx={{ flex: 1 }}>
              {snapshot.resolution_error || '当前范围尚未配置已发布版本。'}
            </Alert>
          )}
          <Chip label={`处理方案 ${snapshot.definitions.length}`} variant="outlined" />
          <Chip label={`已发布版本 ${snapshot.releases.length}`} variant="outlined" />
          <Chip label={`生效范围 ${snapshot.deployments.length}`} variant="outlined" />
        </Stack>
        {snapshot.resolved_agent_digest ? (
          <OperatorTechnicalDisclosure title="系统信息" summary="当前版本标识" compact>
            <Typography component="code" variant="caption">
              {snapshot.resolved_agent_digest}
            </Typography>
          </OperatorTechnicalDisclosure>
        ) : null}
      </Paper>

      <Paper component="section" variant="outlined" sx={{ p: 2 }}>
        <Stack
          direction={{ xs: 'column', sm: 'row' }}
          spacing={1}
          sx={{ justifyContent: 'space-between' }}
        >
          <Typography component="h2" variant="h2">处理方案</Typography>
          {editingDefinitionId ? (
            <Button
              onClick={() => {
                setEditingDefinitionId(null)
                setComposer(defaultComposer(snapshot))
              }}
            >
              取消编辑
            </Button>
          ) : null}
        </Stack>

        <Box
          sx={{
            display: 'grid',
            gridTemplateColumns: { xs: '1fr', md: 'repeat(2, minmax(0, 1fr))' },
            gap: 1.5,
            mt: 2,
          }}
        >
          <TextField
            label="方案编号"
            value={composer.definitionKey}
            disabled={Boolean(editingDefinitionId)}
            onChange={(event) => setComposer({
              ...composer,
              definitionKey: normalizeKey(event.target.value),
            })}
          />
          <TextField
            label="方案名称"
            value={composer.name}
            onChange={(event) => setComposer({ ...composer, name: event.target.value })}
          />
          <TextField
            label="用途说明"
            value={composer.purpose}
            multiline
            minRows={2}
            onChange={(event) => setComposer({ ...composer, purpose: event.target.value })}
            sx={{ gridColumn: { md: '1 / -1' } }}
          />
          <TextField
            select
            label="回复风格（可选）"
            value={composer.persona}
            onChange={(event) => setComposer({ ...composer, persona: event.target.value })}
          >
            <MenuItem value="">不指定</MenuItem>
            {publishedPersonas.map((item) => (
              <MenuItem key={item.id} value={`${item.profile_key}@${item.published_version}`}>
                {item.name} · v{item.published_version}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            select
            label="模型配置"
            value={composer.modelProfile}
            onChange={(event) => setComposer({ ...composer, modelProfile: event.target.value })}
          >
            {publishedModels.map((item) => (
              <MenuItem key={item.id} value={resourceValue(item)}>
                {item.name} · v{item.published_version}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            select
            label="运行限制"
            value={composer.runtimePolicy}
            onChange={(event) => setComposer({ ...composer, runtimePolicy: event.target.value })}
          >
            {publishedRuntimePolicies.map((item) => (
              <MenuItem key={item.id} value={resourceValue(item)}>
                {item.name} · v{item.published_version}
              </MenuItem>
            ))}
          </TextField>
        </Box>

        <ResourceChecklist
          title="业务规则（至少选择一个）"
          resources={publishedPlaybooks}
          selected={composer.playbooks}
          onChange={(playbooks) => setComposer({ ...composer, playbooks })}
        />
        <ResourceChecklist
          title="外部系统（可选）"
          resources={publishedIntegrations}
          selected={composer.integrations}
          onChange={(integrations) => setComposer({ ...composer, integrations })}
        />
        <KnowledgeChecklist
          items={publishedKnowledge}
          selected={composer.knowledge}
          onChange={(knowledge) => setComposer({ ...composer, knowledge })}
        />

        {createOrUpdate.isError ? (
          <Box sx={{ mt: 2 }}>
            <OperatorErrorNotice
              title="无法保存处理方案"
              error={createOrUpdate.error}
              fallback="请检查必填项和所选版本"
            />
          </Box>
        ) : null}
        <Button
          variant="contained"
          startIcon={<PublishRoundedIcon />}
          disabled={!canManage || !readyToSave || createOrUpdate.isPending}
          onClick={() => createOrUpdate.mutate()}
          sx={{ mt: 2 }}
        >
          {editingDefinitionId ? '保存方案草稿' : '创建处理方案'}
        </Button>
      </Paper>

      <Paper component="section" variant="outlined" sx={{ p: 2 }}>
        <Typography component="h2" variant="h2">版本与生效范围</Typography>
        <Stack spacing={1.5} sx={{ mt: 2 }}>
          {snapshot.definitions.length ? snapshot.definitions.map((definition) => {
            const definitionReleases = snapshot.releases.filter(
              (item) => item.definition_id === definition.id,
            )
            return (
              <Paper key={definition.id} variant="outlined" sx={{ p: 1.5 }}>
                <Stack
                  direction={{ xs: 'column', md: 'row' }}
                  spacing={1.5}
                  sx={{ justifyContent: 'space-between' }}
                >
                  <Box>
                    <Typography variant="subtitle1">{definition.name}</Typography>
                    <Typography variant="caption" color="text.secondary">
                      {definition.purpose || '未填写用途说明'}
                    </Typography>
                  </Box>
                  <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: 'wrap' }}>
                    <Button
                      size="small"
                      disabled={!canManage}
                      onClick={() => {
                        setEditingDefinitionId(definition.id)
                        setComposer(composerFromDefinition(definition))
                      }}
                    >
                      编辑方案
                    </Button>
                    <Button
                      size="small"
                      variant="outlined"
                      startIcon={<PublishRoundedIcon />}
                      disabled={!canDeploy || publishVersion.isPending}
                      onClick={() => publishVersion.mutate(definition)}
                    >
                      发布新版本
                    </Button>
                  </Stack>
                </Stack>
                <Divider sx={{ my: 1.5 }} />
                <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: 'wrap' }}>
                  {definitionReleases.length ? definitionReleases.map((item) => (
                    <Button
                      key={item.id}
                      size="small"
                      variant={item.id === activeReleaseId ? 'contained' : 'outlined'}
                      color={item.id === activeReleaseId ? 'success' : 'primary'}
                      startIcon={<RocketLaunchRoundedIcon />}
                      disabled={!canDeploy || applyVersion.isPending || item.id === activeReleaseId}
                      onClick={() => setPendingVersion(item)}
                    >
                      v{item.version} {item.id === activeReleaseId ? '当前生效' : '应用此版本'}
                    </Button>
                  )) : (
                    <Typography variant="body2" color="text.secondary">尚未发布版本</Typography>
                  )}
                </Stack>
                <OperatorTechnicalDisclosure title="系统信息" summary={definition.definition_key} compact>
                  <Typography component="code" variant="caption">{definition.definition_key}</Typography>
                </OperatorTechnicalDisclosure>
              </Paper>
            )
          }) : (
            <Alert severity="info">尚无处理方案。请先创建方案。</Alert>
          )}
        </Stack>
        {publishVersion.isError || applyVersion.isError ? (
          <Box sx={{ mt: 2 }}>
            <OperatorErrorNotice
              title="版本操作失败"
              error={publishVersion.error || applyVersion.error}
              fallback="请检查发布权限、配置完整性和适用范围"
            />
          </Box>
        ) : null}
      </Paper>

      <Paper component="section" variant="outlined" sx={{ p: 2 }}>
        <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
          <ScienceRoundedIcon color="primary" aria-hidden="true" />
          <Typography component="h2" variant="h2">回复测试</Typography>
        </Stack>
        <TextField
          label="客户消息"
          value={testMessage}
          onChange={(event) => setTestMessage(event.target.value)}
          multiline
          minRows={3}
          fullWidth
          sx={{ mt: 2 }}
        />
        <Stack
          direction={{ xs: 'column', sm: 'row' }}
          spacing={1}
          sx={{ mt: 1.5, alignItems: { sm: 'center' } }}
        >
          <FormControlLabel
            control={(
              <Checkbox
                checked={generateReply}
                onChange={(event) => setGenerateReply(event.target.checked)}
                disabled={!canDeploy}
              />
            )}
            label="生成测试回复"
          />
          <Button
            variant="contained"
            startIcon={<PlayArrowRoundedIcon />}
            disabled={!testMessage.trim() || testReply.isPending}
            onClick={() => testReply.mutate()}
          >
            开始测试
          </Button>
        </Stack>
        {testReply.isError ? (
          <Box sx={{ mt: 2 }}>
            <OperatorErrorNotice
              title="测试失败"
              error={testReply.error}
              fallback="请先为当前范围应用一个已发布版本"
            />
          </Box>
        ) : null}
        {testReply.data ? (
          <Box sx={{ mt: 2 }}>
            {testReply.data.resolution_error ? (
              <Alert severity="warning">{testReply.data.resolution_error}</Alert>
            ) : (
              <Stack spacing={1}>
                <Alert severity={testReply.data.model_executed ? 'success' : 'info'}>
                  {testReply.data.model_executed
                    ? '测试回复已生成。'
                    : `配置检查完成：${testReply.data.playbooks.length} 条业务规则，${testReply.data.tools.length} 个可用工具。`}
                </Alert>
                {testReply.data.reply ? (
                  <Paper variant="outlined" sx={{ p: 1.5 }}>
                    <Typography variant="caption" color="text.secondary">客户回复</Typography>
                    <Typography sx={{ mt: 0.5, whiteSpace: 'pre-wrap' }}>
                      {testReply.data.reply}
                    </Typography>
                  </Paper>
                ) : null}
                <OperatorTechnicalDisclosure title="测试详情" summary="版本、规则和运行证据">
                  <Box component="pre" sx={{ m: 0, maxHeight: 420, overflow: 'auto', whiteSpace: 'pre-wrap', fontSize: 12 }}>
                    {JSON.stringify(testReply.data, null, 2)}
                  </Box>
                </OperatorTechnicalDisclosure>
              </Stack>
            )}
          </Box>
        ) : null}
      </Paper>

      <Dialog
        open={Boolean(pendingVersion)}
        onClose={() => { if (!applyVersion.isPending) setPendingVersion(null) }}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>应用版本 v{pendingVersion?.version}</DialogTitle>
        <DialogContent>
          <DialogContentText>
            该版本将应用到当前选择的环境、市场、渠道、语言和业务类型。保存后，新进入的请求将使用此版本。
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button color="inherit" disabled={applyVersion.isPending} onClick={() => setPendingVersion(null)}>取消</Button>
          <Button
            variant="contained"
            disabled={!pendingVersion || applyVersion.isPending}
            onClick={() => { if (pendingVersion) applyVersion.mutate(pendingVersion) }}
          >
            {applyVersion.isPending ? '正在应用…' : '确认应用'}
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  )
}

function ResourceChecklist({
  title,
  resources: items,
  selected,
  onChange,
}: {
  title: string
  resources: AgentConfigResource[]
  selected: string[]
  onChange: (value: string[]) => void
}) {
  return (
    <Box sx={{ mt: 2 }}>
      <Typography variant="subtitle2">{title}</Typography>
      <Stack direction="row" sx={{ flexWrap: 'wrap', gap: 0.5, mt: 0.5 }}>
        {items.length ? items.map((item) => {
          const value = resourceValue(item)
          return (
            <FormControlLabel
              key={item.id}
              control={(
                <Checkbox
                  size="small"
                  checked={selected.includes(value)}
                  onChange={(event) => onChange(toggle(selected, value, event.target.checked))}
                />
              )}
              label={`${item.name} · v${item.published_version}`}
            />
          )
        }) : (
          <Typography variant="body2" color="text.secondary">暂无已发布项目</Typography>
        )}
      </Stack>
    </Box>
  )
}

function KnowledgeChecklist({
  items,
  selected,
  onChange,
}: {
  items: AgentControlSnapshot['knowledge']
  selected: string[]
  onChange: (value: string[]) => void
}) {
  return (
    <Box sx={{ mt: 2 }}>
      <Typography variant="subtitle2">知识库（可选）</Typography>
      <Stack direction="row" sx={{ flexWrap: 'wrap', gap: 0.5, mt: 0.5 }}>
        {items.length ? items.map((item) => {
          const value = `${item.item_key}@${item.published_version}`
          return (
            <FormControlLabel
              key={item.id}
              control={(
                <Checkbox
                  size="small"
                  checked={selected.includes(value)}
                  onChange={(event) => onChange(toggle(selected, value, event.target.checked))}
                />
              )}
              label={`${item.title} · v${item.published_version}`}
            />
          )
        }) : (
          <Typography variant="body2" color="text.secondary">暂无可用知识</Typography>
        )}
      </Stack>
    </Box>
  )
}

function resources(all: AgentConfigResource[], type: AgentConfigResource['config_type']) {
  return all.filter(
    (item) => item.config_type === type && item.is_active && item.published_version > 0,
  )
}

function resourceValue(resource?: AgentConfigResource) {
  return resource ? `${resource.resource_key}@${resource.published_version}` : ''
}

function parseResource(value: string): ResourceRef {
  const [resourceKey, version] = splitVersion(value)
  return { resource_key: resourceKey, version }
}

function parsePersona(value: string): PersonaRef | null {
  if (!value) return null
  const [profileKey, version] = splitVersion(value)
  return { profile_key: profileKey, version }
}

function parseKnowledge(value: string): KnowledgeRef {
  const [itemKey, version] = splitVersion(value)
  return { item_key: itemKey, version }
}

function splitVersion(value: string): [string, number] {
  const index = value.lastIndexOf('@')
  return [value.slice(0, index), Number(value.slice(index + 1))]
}

function buildManifest(state: ComposerState) {
  return {
    schema_version: 'nexus.agent_release.v1',
    persona: parsePersona(state.persona),
    playbooks: state.playbooks.map(parseResource),
    integrations: state.integrations.map(parseResource),
    model_profile: parseResource(state.modelProfile),
    runtime_policy: parseResource(state.runtimePolicy),
    knowledge: state.knowledge.map(parseKnowledge),
    metadata: { authored_from: 'agent_control_ui' },
  }
}

function composerFromDefinition(definition: AgentDefinition): ComposerState {
  const manifest = definition.draft_manifest
  return {
    definitionKey: definition.definition_key,
    name: definition.name,
    purpose: definition.purpose || '',
    persona: referenceValue(manifest.persona, 'profile_key'),
    playbooks: referenceValues(manifest.playbooks, 'resource_key'),
    integrations: referenceValues(manifest.integrations, 'resource_key'),
    modelProfile: referenceValue(manifest.model_profile, 'resource_key'),
    runtimePolicy: referenceValue(manifest.runtime_policy, 'resource_key'),
    knowledge: referenceValues(manifest.knowledge, 'item_key'),
  }
}

function defaultComposer(snapshot: AgentControlSnapshot): ComposerState {
  const playbooks = resources(snapshot.resources, 'playbook')
  return {
    ...EMPTY_COMPOSER,
    playbooks: playbooks.map(resourceValue),
    modelProfile: resourceValue(resources(snapshot.resources, 'model_profile')[0]),
    runtimePolicy: resourceValue(resources(snapshot.resources, 'runtime_policy')[0]),
  }
}

function referenceValue(value: unknown, key: string) {
  if (!value || typeof value !== 'object') return ''
  const record = value as Record<string, unknown>
  return typeof record[key] === 'string' && typeof record.version === 'number'
    ? `${record[key]}@${record.version}`
    : ''
}

function referenceValues(value: unknown, key: string) {
  return Array.isArray(value)
    ? value.map((item) => referenceValue(item, key)).filter(Boolean)
    : []
}

function toggle(values: string[], value: string, checked: boolean) {
  return checked
    ? Array.from(new Set([...values, value]))
    : values.filter((item) => item !== value)
}

function normalizeKey(value: string) {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9_.-]+/g, '-')
    .replace(/^-+|-+$/g, '')
}

function parsedMarketId(value: string) {
  if (!value.trim()) return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function nullableNumber(value?: number | null) {
  return typeof value === 'number' ? value : null
}

function releaseLabel(value: unknown) {
  if (!value || typeof value !== 'object') return '未配置'
  const record = value as Record<string, unknown>
  const release = record.release
  if (!release || typeof release !== 'object') return '未知版本'
  const item = release as Record<string, unknown>
  return `v${String(item.version || '?')}`
}

async function invalidate(queryClient: QueryClient) {
  await queryClient.invalidateQueries({ queryKey: ['agentControlSnapshot'] })
}

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
import { OperatorErrorNotice } from '@/app/OperatorPresentation'
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
  const [playgroundBody, setPlaygroundBody] = useState('Where is my shipment?')
  const [executeModel, setExecuteModel] = useState(false)

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

  const release = useMutation({
    mutationFn: (definition: AgentDefinition) =>
      agentControlApi.releaseDefinition(definition.id, tenantKey),
    onSuccess: () => invalidate(queryClient),
  })

  const deploy = useMutation({
    mutationFn: (target: AgentRelease) => agentControlApi.deployRelease({
      tenant_key: tenantKey,
      environment,
      release_id: target.id,
      market_id: parsedMarketId(marketId),
      channel: channel || null,
      language: language || null,
      case_type: caseType || null,
    }),
    onSuccess: () => invalidate(queryClient),
  })

  const playground = useMutation({
    mutationFn: () => agentControlApi.playground({
      tenant_key: tenantKey,
      environment,
      market_id: parsedMarketId(marketId),
      channel,
      language: language || null,
      case_type: caseType || null,
      cohort_key: 'operator-playground',
      body: playgroundBody,
      execute_model: executeModel,
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
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="h2">解析作用域</Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
          所有预览、Playground 与 Deployment 使用同一个服务端 Resolver。空值表示通配作用域。
        </Typography>
        <Box
          sx={{
            display: 'grid',
            gridTemplateColumns: { xs: '1fr', md: 'repeat(3, minmax(0, 1fr))' },
            gap: 1.5,
            mt: 2,
          }}
        >
          <TextField label="Tenant" value={tenantKey} onChange={(event) => setTenantKey(event.target.value)} />
          <TextField
            select
            label="Environment"
            value={environment}
            onChange={(event) => setEnvironment(event.target.value as Props['environment'])}
          >
            <MenuItem value="test">Test</MenuItem>
            <MenuItem value="staging">Staging</MenuItem>
            <MenuItem value="production">Production</MenuItem>
          </TextField>
          <TextField
            label="Market ID（可选）"
            value={marketId}
            onChange={(event) => setMarketId(event.target.value.replace(/[^0-9]/g, ''))}
          />
          <TextField label="Channel" value={channel} onChange={(event) => setChannel(event.target.value)} />
          <TextField label="Language（可选）" value={language} onChange={(event) => setLanguage(event.target.value)} />
          <TextField label="Case Type（可选）" value={caseType} onChange={(event) => setCaseType(event.target.value)} />
        </Box>
        <Stack
          direction={{ xs: 'column', sm: 'row' }}
          spacing={1}
          sx={{ mt: 2, alignItems: { sm: 'center' } }}
        >
          {snapshot.resolved_agent ? (
            <Alert severity="success" icon={<CheckCircleRoundedIcon />} sx={{ flex: 1 }}>
              已解析 Agent Release：{releaseLabel(snapshot.resolved_agent)}；摘要 {snapshot.resolved_agent_digest?.slice(0, 12)}
            </Alert>
          ) : (
            <Alert severity="warning" sx={{ flex: 1 }}>
              当前作用域尚未部署 Agent Release：{snapshot.resolution_error || 'agent_deployment_not_found'}
            </Alert>
          )}
          <Chip label={`${snapshot.definitions.length} Definitions`} variant="outlined" />
          <Chip label={`${snapshot.releases.length} Releases`} variant="outlined" />
          <Chip label={`${snapshot.deployments.length} Deployments`} variant="outlined" />
        </Stack>
      </Paper>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Stack
          direction={{ xs: 'column', sm: 'row' }}
          spacing={1}
          sx={{ justifyContent: 'space-between' }}
        >
          <Box>
            <Typography variant="h2">Agent Definition 组合器</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
              只组合已发布、版本固定的资源。保存 Definition 不会影响运行；创建 Release 后才可部署。
            </Typography>
          </Box>
          {editingDefinitionId ? (
            <Button
              onClick={() => {
                setEditingDefinitionId(null)
                setComposer(defaultComposer(snapshot))
              }}
            >
              退出编辑
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
            label="Definition Key"
            value={composer.definitionKey}
            disabled={Boolean(editingDefinitionId)}
            onChange={(event) => setComposer({
              ...composer,
              definitionKey: normalizeKey(event.target.value),
            })}
          />
          <TextField
            label="名称"
            value={composer.name}
            onChange={(event) => setComposer({ ...composer, name: event.target.value })}
          />
          <TextField
            label="目的"
            value={composer.purpose}
            multiline
            minRows={2}
            onChange={(event) => setComposer({ ...composer, purpose: event.target.value })}
            sx={{ gridColumn: { md: '1 / -1' } }}
          />
          <TextField
            select
            label="Persona（可选）"
            value={composer.persona}
            onChange={(event) => setComposer({ ...composer, persona: event.target.value })}
          >
            <MenuItem value="">不绑定</MenuItem>
            {publishedPersonas.map((item) => (
              <MenuItem key={item.id} value={`${item.profile_key}@${item.published_version}`}>
                {item.name} · v{item.published_version}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            select
            label="Model Profile"
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
            label="Runtime Policy"
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
          title="Business Playbooks（至少一个）"
          resources={publishedPlaybooks}
          selected={composer.playbooks}
          onChange={(playbooks) => setComposer({ ...composer, playbooks })}
        />
        <ResourceChecklist
          title="Integrations（可选）"
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
              title="无法保存 Agent Definition"
              error={createOrUpdate.error}
              fallback="请检查资源版本与租户范围"
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
          {editingDefinitionId ? '保存 Definition 草稿' : '创建 Agent Definition'}
        </Button>
      </Paper>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="h2">Release 与 Deployment</Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
          Release 是不可变产物。将旧 Release 重新部署到相同作用域即为原子回滚，不复制配置。
        </Typography>
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
                      {definition.definition_key} · {definition.purpose || '未填写目的'}
                    </Typography>
                  </Box>
                  <Stack direction="row" spacing={1}>
                    <Button
                      size="small"
                      disabled={!canManage}
                      onClick={() => {
                        setEditingDefinitionId(definition.id)
                        setComposer(composerFromDefinition(definition))
                      }}
                    >
                      编辑组合
                    </Button>
                    <Button
                      size="small"
                      variant="outlined"
                      startIcon={<PublishRoundedIcon />}
                      disabled={!canDeploy || release.isPending}
                      onClick={() => release.mutate(definition)}
                    >
                      创建 Release
                    </Button>
                  </Stack>
                </Stack>
                <Divider sx={{ my: 1.5 }} />
                <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap', rowGap: 1 }}>
                  {definitionReleases.length ? definitionReleases.map((item) => (
                    <Button
                      key={item.id}
                      size="small"
                      variant={item.id === activeReleaseId ? 'contained' : 'outlined'}
                      color={item.id === activeReleaseId ? 'success' : 'primary'}
                      startIcon={<RocketLaunchRoundedIcon />}
                      disabled={!canDeploy || deploy.isPending}
                      onClick={() => deploy.mutate(item)}
                    >
                      v{item.version} {item.id === activeReleaseId ? '已部署' : '部署/回滚至此版本'}
                    </Button>
                  )) : (
                    <Typography variant="body2" color="text.secondary">尚无 Release</Typography>
                  )}
                </Stack>
              </Paper>
            )
          }) : (
            <Alert severity="info">尚无 Agent Definition。先通过上方组合器创建。</Alert>
          )}
        </Stack>
        {release.isError || deploy.isError ? (
          <Box sx={{ mt: 2 }}>
            <OperatorErrorNotice
              title="Release 或 Deployment 失败"
              error={release.error || deploy.error}
              fallback="需要 runtime.manage 权限，且所有引用版本必须有效"
            />
          </Box>
        ) : null}
      </Paper>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
          <ScienceRoundedIcon color="primary" />
          <Typography variant="h2">Agent Playground</Typography>
        </Stack>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
          使用当前作用域的真实 Deployment、Resolver、Persona、Playbooks、Tools、Model Profile 与 Runtime Policy。写工具永不执行。
        </Typography>
        <TextField
          label="客户消息"
          value={playgroundBody}
          onChange={(event) => setPlaygroundBody(event.target.value)}
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
                checked={executeModel}
                onChange={(event) => setExecuteModel(event.target.checked)}
                disabled={!canDeploy}
              />
            )}
            label="执行模型（仅 runtime.manage；仍为只读 Tool）"
          />
          <Button
            variant="contained"
            startIcon={<PlayArrowRoundedIcon />}
            disabled={!playgroundBody.trim() || playground.isPending}
            onClick={() => playground.mutate()}
          >
            运行 Playground
          </Button>
        </Stack>
        {playground.isError ? (
          <Box sx={{ mt: 2 }}>
            <OperatorErrorNotice
              title="Playground 失败"
              error={playground.error}
              fallback="请先部署 Agent Release"
            />
          </Box>
        ) : null}
        {playground.data ? (
          <Box sx={{ mt: 2 }}>
            {playground.data.resolution_error ? (
              <Alert severity="warning">{playground.data.resolution_error}</Alert>
            ) : (
              <Stack spacing={1}>
                <Alert severity={playground.data.model_executed ? 'success' : 'info'}>
                  Release {releaseLabel(playground.data.agent_release)} · {playground.data.playbooks.length} Playbooks · {playground.data.tools.length} read Tools
                </Alert>
                {playground.data.reply ? (
                  <Paper variant="outlined" sx={{ p: 1.5 }}>
                    <Typography variant="caption" color="text.secondary">最终客户回复</Typography>
                    <Typography sx={{ mt: 0.5, whiteSpace: 'pre-wrap' }}>
                      {playground.data.reply}
                    </Typography>
                  </Paper>
                ) : null}
                <TextField
                  label="解析与运行证据"
                  value={JSON.stringify(playground.data, null, 2)}
                  multiline
                  minRows={8}
                  fullWidth
                  slotProps={{ input: { readOnly: true } }}
                />
              </Stack>
            )}
          </Box>
        ) : null}
      </Paper>
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
          <Typography variant="body2" color="text.secondary">暂无已发布资源</Typography>
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
      <Typography variant="subtitle2">Knowledge（已发布且已索引，可选）</Typography>
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
          <Typography variant="body2" color="text.secondary">暂无可绑定的已索引知识</Typography>
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
  if (!value || typeof value !== 'object') return '未部署'
  const record = value as Record<string, unknown>
  const release = record.release
  if (!release || typeof release !== 'object') return '未知 Release'
  const item = release as Record<string, unknown>
  return `#${String(item.id || '?')} v${String(item.version || '?')}`
}

async function invalidate(queryClient: QueryClient) {
  await queryClient.invalidateQueries({ queryKey: ['agentControlSnapshot'] })
}

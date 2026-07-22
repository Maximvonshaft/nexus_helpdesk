import AddRoundedIcon from '@mui/icons-material/AddRounded'
import PublishRoundedIcon from '@mui/icons-material/PublishRounded'
import RestoreRoundedIcon from '@mui/icons-material/RestoreRounded'
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
  TextField,
  Typography,
} from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { OperatorEmptyState, OperatorErrorNotice } from '@/app/OperatorPresentation'
import { agentControlApi, type AgentConfigDraft } from '@/lib/agentControlApi'
import type { AgentConfigResource, AgentControlSnapshot } from '@/lib/types'
import { asNumber, asString, contentOf, lineText, lines } from './formUtils'

type ScopeType = 'global' | 'market' | 'channel'

type Draft = {
  resource_key: string
  name: string
  display_name: string
  description: string
  scope_type: ScopeType
  scope_value: string
  market_id: string
  is_active: boolean
  priority: string
  channels: string
  languages: string
  instructions: string
  tools: string[]
  draft_summary: string
}

function fromResource(resource?: AgentConfigResource | null): Draft {
  const content = contentOf(resource)
  const scopeType = resource?.scope_type ?? 'global'
  return {
    resource_key: resource?.resource_key || `agent.playbook.${Date.now().toString(36)}`,
    name: asString(content.name, resource?.resource_key.split('.').pop() || ''),
    display_name: asString(content.display_name, resource?.name || ''),
    description: asString(content.description, resource?.description || ''),
    scope_type: scopeType,
    scope_value: scopeType === 'channel' ? resource?.scope_value || '' : '',
    market_id: resource?.market_id == null ? '' : String(resource.market_id),
    is_active: resource?.is_active ?? true,
    priority: String(asNumber(content.priority, 100)),
    channels: lineText(content.channels),
    languages: lineText(content.languages),
    instructions: lineText(content.instructions),
    tools: lines(content.tools),
    draft_summary: resource?.draft_summary || '',
  }
}

function payload(draft: Draft): AgentConfigDraft {
  const marketId = draft.market_id.trim() ? Number(draft.market_id) : null
  const scopeValue = draft.scope_type === 'global'
    ? null
    : draft.scope_type === 'market'
      ? String(marketId)
      : draft.scope_value.trim().toLowerCase()
  return {
    resource_key: draft.resource_key.trim().toLowerCase(),
    config_type: 'playbook',
    name: draft.display_name.trim() || draft.name.trim(),
    description: draft.description.trim() || null,
    scope_type: draft.scope_type,
    scope_value: scopeValue,
    market_id: draft.scope_type === 'market' ? marketId : null,
    is_active: draft.is_active,
    draft_summary: draft.draft_summary.trim() || draft.description.trim() || null,
    draft_content_json: {
      schema_version: 'nexus.agent_playbook.v1',
      name: draft.name.trim().toLowerCase(),
      display_name: draft.display_name.trim() || draft.name.trim(),
      description: draft.description.trim(),
      tools: draft.tools,
      instructions: lines(draft.instructions),
      priority: Number(draft.priority) || 100,
      channels: lines(draft.channels),
      languages: lines(draft.languages),
      enabled: draft.is_active,
    },
  }
}

export function PlaybookPanel({
  snapshot,
  canManage,
}: {
  snapshot: AgentControlSnapshot
  canManage: boolean
}) {
  const queryClient = useQueryClient()
  const resources = useMemo(
    () => snapshot.resources.filter((item) => item.config_type === 'playbook'),
    [snapshot.resources],
  )
  const [selectedId, setSelectedId] = useState<number | null>(resources[0]?.id ?? null)
  const [creating, setCreating] = useState(false)
  const selected = resources.find((item) => item.id === selectedId) ?? null
  const [draft, setDraft] = useState<Draft>(() => fromResource(selected))
  useEffect(() => { setDraft(fromResource(creating ? null : selected)) }, [creating, selected])

  const versions = useQuery({
    queryKey: ['agentConfigVersions', selectedId],
    queryFn: () => agentControlApi.configVersions(selectedId as number),
    enabled: Boolean(selectedId && !creating),
    retry: false,
  })
  const invalidate = async () => {
    await queryClient.invalidateQueries({ queryKey: ['agentControlSnapshot'] })
  }
  const save = useMutation({
    mutationFn: async (publish: boolean) => {
      if (!draft.display_name.trim() || !draft.name.trim()) {
        throw new Error('请填写业务规则名称')
      }
      if (!draft.description.trim()) throw new Error('请填写业务规则说明')
      if (!lines(draft.instructions).length) throw new Error('请至少填写一条处理要求')
      if (draft.scope_type === 'market' && !draft.market_id.trim()) {
        throw new Error('按市场生效时必须选择市场')
      }
      if (draft.scope_type === 'channel' && !draft.scope_value.trim()) {
        throw new Error('按渠道生效时必须填写渠道')
      }
      const data = payload(draft)
      const item = creating || !selected
        ? await agentControlApi.createConfig(data)
        : await agentControlApi.updateConfig(selected.id, data)
      if (publish) await agentControlApi.publishConfig(item.id, 'Business rule publish')
      return item
    },
    onSuccess: async (item) => {
      setCreating(false)
      setSelectedId(item.id)
      await invalidate()
    },
  })
  const rollback = useMutation({
    mutationFn: (version: number) => agentControlApi.rollbackConfig(
      selected!.id,
      version,
      `Restore business rule draft from v${version}`,
    ),
    onSuccess: async () => {
      await invalidate()
      await versions.refetch()
    },
  })
  const busy = save.isPending || rollback.isPending

  return (
    <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', xl: '300px minmax(0, 1fr) 340px' } }}>
      <Paper component="aside" variant="outlined" sx={{ p: 1.5 }}>
        <Stack direction="row" spacing={1} sx={{ alignItems: 'center', justifyContent: 'space-between' }}>
          <Typography component="h2" variant="h3">业务规则</Typography>
          {canManage ? (
            <Button
              size="small"
              variant="contained"
              startIcon={<AddRoundedIcon />}
              onClick={() => { setCreating(true); setSelectedId(null) }}
            >
              新建
            </Button>
          ) : null}
        </Stack>
        <List disablePadding sx={{ mt: 1.5 }}>
          {resources.map((item) => {
            const content = contentOf(item)
            return (
              <ListItemButton
                key={item.id}
                selected={!creating && selectedId === item.id}
                onClick={() => { setCreating(false); setSelectedId(item.id) }}
                sx={{ display: 'block', borderBottom: 1, borderColor: 'divider' }}
              >
                <Typography variant="subtitle2">{asString(content.display_name, item.name)}</Typography>
                <Typography variant="caption" color="text.secondary">
                  {lines(content.tools).length} 个工具 · v{item.published_version}
                </Typography>
              </ListItemButton>
            )
          })}
          {!resources.length ? (
            <OperatorEmptyState title="尚无业务规则" description="新建第一条自动处理规则" />
          ) : null}
        </List>
      </Paper>

      <Paper component="section" variant="outlined" sx={{ p: 2, minWidth: 0 }}>
        <Typography component="h2" variant="h3">
          {creating ? '新建业务规则' : '编辑业务规则'}
        </Typography>
        {!canManage ? (
          <Alert severity="info" variant="outlined" sx={{ mt: 2 }}>当前为只读视图。</Alert>
        ) : null}
        {save.error ? (
          <Box sx={{ mt: 2 }}>
            <OperatorErrorNotice title="保存失败" error={save.error} fallback="请检查必填项和所选工具" />
          </Box>
        ) : null}
        <Stack spacing={1.5} sx={{ mt: 2 }}>
          <Box sx={{ display: 'grid', gap: 1.5, gridTemplateColumns: { xs: '1fr', md: 'repeat(2, minmax(0, 1fr))' } }}>
            <TextField
              label="规则编号"
              required
              disabled={!creating || !canManage}
              value={draft.resource_key}
              onChange={(event) => setDraft((current) => ({
                ...current,
                resource_key: event.target.value.replace(/[^a-zA-Z0-9_.:-]+/g, '-').toLowerCase(),
              }))}
            />
            <TextField
              label="内部名称"
              required
              disabled={!canManage}
              value={draft.name}
              onChange={(event) => setDraft((current) => ({
                ...current,
                name: event.target.value.replace(/[^a-zA-Z0-9_.:-]+/g, '_').toLowerCase(),
              }))}
            />
            <TextField
              label="显示名称"
              required
              disabled={!canManage}
              value={draft.display_name}
              onChange={(event) => setDraft((current) => ({ ...current, display_name: event.target.value }))}
            />
            <TextField
              label="优先级"
              type="number"
              helperText="数字越小越优先"
              disabled={!canManage}
              value={draft.priority}
              onChange={(event) => setDraft((current) => ({ ...current, priority: event.target.value }))}
            />
            <TextField
              select
              label="生效范围"
              disabled={!canManage}
              value={draft.scope_type}
              onChange={(event) => setDraft((current) => ({
                ...current,
                scope_type: event.target.value as ScopeType,
                scope_value: '',
                market_id: '',
              }))}
            >
              <MenuItem value="global">全部范围</MenuItem>
              <MenuItem value="market">指定市场</MenuItem>
              <MenuItem value="channel">指定渠道</MenuItem>
            </TextField>
            {draft.scope_type === 'market' ? (
              <TextField
                label="市场编号"
                required
                type="number"
                disabled={!canManage}
                value={draft.market_id}
                onChange={(event) => setDraft((current) => ({ ...current, market_id: event.target.value }))}
              />
            ) : null}
            {draft.scope_type === 'channel' ? (
              <TextField
                label="渠道"
                required
                disabled={!canManage}
                value={draft.scope_value}
                onChange={(event) => setDraft((current) => ({ ...current, scope_value: event.target.value }))}
              />
            ) : null}
            <FormControlLabel
              control={(
                <Checkbox
                  disabled={!canManage}
                  checked={draft.is_active}
                  onChange={(event) => setDraft((current) => ({ ...current, is_active: event.target.checked }))}
                />
              )}
              label="启用"
            />
          </Box>
          <TextField
            label="业务说明"
            required
            multiline
            minRows={3}
            disabled={!canManage}
            value={draft.description}
            onChange={(event) => setDraft((current) => ({ ...current, description: event.target.value }))}
          />
          <TextField
            label="适用渠道"
            helperText="每行一个；留空表示全部渠道"
            multiline
            minRows={2}
            disabled={!canManage}
            value={draft.channels}
            onChange={(event) => setDraft((current) => ({ ...current, channels: event.target.value }))}
          />
          <TextField
            label="适用语言"
            helperText="每行一个；留空表示全部语言"
            multiline
            minRows={2}
            disabled={!canManage}
            value={draft.languages}
            onChange={(event) => setDraft((current) => ({ ...current, languages: event.target.value }))}
          />
          <TextField
            label="处理要求"
            required
            helperText="每行一条明确、可验证的要求"
            multiline
            minRows={8}
            disabled={!canManage}
            value={draft.instructions}
            onChange={(event) => setDraft((current) => ({ ...current, instructions: event.target.value }))}
          />
          <TextField
            select
            label="允许使用的工具"
            slotProps={{
              select: {
                multiple: true,
                renderValue: (selectedTools: unknown) => (
                  <Stack direction="row" spacing={0.5} useFlexGap sx={{ flexWrap: 'wrap' }}>
                    {(selectedTools as string[]).map((tool) => (
                      <Chip key={tool} size="small" label={tool} />
                    ))}
                  </Stack>
                ),
              },
            }}
            disabled={!canManage}
            value={draft.tools}
            onChange={(event) => setDraft((current) => ({
              ...current,
              tools: typeof event.target.value === 'string'
                ? event.target.value.split(',')
                : event.target.value,
            }))}
          >
            {snapshot.tools.map((tool) => (
              <MenuItem key={tool.name} value={tool.name} disabled={!tool.executable}>
                {tool.name}{!tool.executable ? ' · 暂不可用' : ''}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            label="版本摘要"
            multiline
            minRows={2}
            disabled={!canManage}
            value={draft.draft_summary}
            onChange={(event) => setDraft((current) => ({ ...current, draft_summary: event.target.value }))}
          />
          {canManage ? (
            <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: 'wrap' }}>
              <Button
                variant="contained"
                disabled={busy}
                startIcon={save.isPending ? <CircularProgress color="inherit" size={16} /> : <SaveRoundedIcon />}
                onClick={() => save.mutate(false)}
              >
                保存草稿
              </Button>
              <Button
                variant="outlined"
                disabled={busy}
                startIcon={<PublishRoundedIcon />}
                onClick={() => save.mutate(true)}
              >
                保存并发布
              </Button>
            </Stack>
          ) : null}
        </Stack>
      </Paper>

      <Paper component="aside" variant="outlined" sx={{ p: 2, minWidth: 0, alignSelf: 'start' }}>
        <Typography component="h2" variant="h3">版本记录</Typography>
        <Divider sx={{ my: 1.5 }} />
        {versions.isError ? (
          <OperatorErrorNotice title="无法读取版本" error={versions.error} fallback="请稍后重试" />
        ) : null}
        <Stack spacing={1.25}>
          {(versions.data ?? []).map((raw) => {
            const version = Number(raw.version || 0)
            return (
              <Box key={String(raw.id || version)} sx={{ borderBottom: 1, borderColor: 'divider', pb: 1.25 }}>
                <Stack direction="row" spacing={1} sx={{ alignItems: 'center', justifyContent: 'space-between' }}>
                  <Typography variant="subtitle2">v{version}</Typography>
                  {canManage && selected && version !== selected.published_version ? (
                    <Button
                      size="small"
                      startIcon={<RestoreRoundedIcon />}
                      disabled={rollback.isPending}
                      onClick={() => rollback.mutate(version)}
                    >
                      恢复为草稿
                    </Button>
                  ) : null}
                </Stack>
                <Typography variant="caption" color="text.secondary">
                  {String(raw.summary || raw.notes || '无摘要')}
                </Typography>
              </Box>
            )
          })}
          {!creating && selected && !versions.isLoading && !(versions.data ?? []).length ? (
            <Typography variant="body2" color="text.secondary">尚无发布版本。</Typography>
          ) : null}
        </Stack>
      </Paper>
    </Box>
  )
}

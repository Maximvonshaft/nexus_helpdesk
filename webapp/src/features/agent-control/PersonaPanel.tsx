import AddRoundedIcon from '@mui/icons-material/AddRounded'
import PublishRoundedIcon from '@mui/icons-material/PublishRounded'
import SaveRoundedIcon from '@mui/icons-material/SaveRounded'
import {
  Alert,
  Box,
  Button,
  Checkbox,
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
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { OperatorEmptyState, OperatorErrorNotice, OperatorTechnicalDisclosure } from '@/app/OperatorPresentation'
import { agentControlApi, type PersonaDraft } from '@/lib/agentControlApi'
import type { AgentControlSnapshot, AgentPersona } from '@/lib/types'
import { asString, lineText, lines } from './formUtils'

type Draft = {
  profile_key: string
  name: string
  description: string
  market_id: string
  channel: string
  language: string
  is_active: boolean
  draft_summary: string
  brand_name: string
  assistant_name: string
  role_label: string
  identity_statement: string
  identity_answer_rule: string
  tone: string
  handoff_boundary: string
  capabilities: string
  guardrails: string
  disallowed_identity_claims: string
}

function fromPersona(item?: AgentPersona | null): Draft {
  const content = (item?.draft_content_json && typeof item.draft_content_json === 'object' ? item.draft_content_json : item?.published_content_json && typeof item.published_content_json === 'object' ? item.published_content_json : {}) as Record<string, unknown>
  return {
    profile_key: item?.profile_key || `agent.persona.${Date.now().toString(36)}`,
    name: item?.name || '',
    description: item?.description || '',
    market_id: item?.market_id == null ? '' : String(item.market_id),
    channel: item?.channel || '',
    language: item?.language || '',
    is_active: item?.is_active ?? true,
    draft_summary: item?.draft_summary || '',
    brand_name: asString(content.brand_name),
    assistant_name: asString(content.assistant_name),
    role_label: asString(content.role_label),
    identity_statement: asString(content.identity_statement),
    identity_answer_rule: asString(content.identity_answer_rule),
    tone: asString(content.tone),
    handoff_boundary: asString(content.handoff_boundary),
    capabilities: lineText(content.capabilities),
    guardrails: lineText(content.guardrails),
    disallowed_identity_claims: lineText(content.disallowed_identity_claims),
  }
}

function payload(draft: Draft): PersonaDraft {
  return {
    profile_key: draft.profile_key.trim().toLowerCase(),
    name: draft.name.trim(),
    description: draft.description.trim() || null,
    market_id: draft.market_id.trim() ? Number(draft.market_id) : null,
    channel: draft.channel || null,
    language: draft.language.trim() || null,
    is_active: draft.is_active,
    draft_summary: draft.draft_summary.trim() || null,
    draft_content_json: {
      schema_version: 'nexus.persona.v1',
      brand_name: draft.brand_name.trim() || null,
      assistant_name: draft.assistant_name.trim() || null,
      role_label: draft.role_label.trim() || null,
      identity_statement: draft.identity_statement.trim() || null,
      identity_answer_rule: draft.identity_answer_rule.trim() || null,
      tone: draft.tone.trim() || null,
      handoff_boundary: draft.handoff_boundary.trim() || null,
      capabilities: lines(draft.capabilities),
      guardrails: lines(draft.guardrails),
      disallowed_identity_claims: lines(draft.disallowed_identity_claims),
    },
  }
}

export function PersonaPanel({ snapshot, canManage }: { snapshot: AgentControlSnapshot; canManage: boolean }) {
  const queryClient = useQueryClient()
  const [selectedId, setSelectedId] = useState<number | null>(snapshot.personas[0]?.id ?? null)
  const [creating, setCreating] = useState(false)
  const selected = useMemo(() => snapshot.personas.find((item) => item.id === selectedId) ?? null, [selectedId, snapshot.personas])
  const [draft, setDraft] = useState<Draft>(() => fromPersona(selected))
  const [evidence, setEvidence] = useState<Record<string, unknown> | null>(null)

  useEffect(() => { setDraft(fromPersona(creating ? null : selected)); setEvidence(null) }, [creating, selected])
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['agentControlSnapshot'] })
  const save = useMutation({
    mutationFn: async (publish: boolean) => {
      const data = payload(draft)
      if (!data.name) throw new Error('请填写人格名称')
      const item = creating || !selected ? await agentControlApi.createPersona(data) : await agentControlApi.updatePersona(selected.id, data)
      if (publish) await agentControlApi.publishPersona(item.id, 'Agent control publish')
      return item
    },
    onSuccess: async (item) => { setCreating(false); setSelectedId(item.id); await invalidate() },
  })
  const testEvidence = useMutation({
    mutationFn: () => agentControlApi.personaRuntimeEvidence({
      tenant_key: 'default', body: 'Who are you and what can you help with?', market_id: draft.market_id ? Number(draft.market_id) : null,
      channel: draft.channel || 'webchat', language: draft.language || null, audience_scope: 'customer', expected_profile_key: draft.profile_key,
    }),
    onSuccess: (result) => setEvidence(result),
  })
  const busy = save.isPending || testEvidence.isPending

  return (
    <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', xl: '300px minmax(0, 1fr) 340px' } }}>
      <Paper component="aside" variant="outlined" sx={{ p: 1.5 }}>
        <Stack direction="row" spacing={1} sx={{ alignItems: 'center', justifyContent: 'space-between' }}>
          <Typography component="h2" variant="h3">人格列表</Typography>
          {canManage ? <Button size="small" variant="contained" startIcon={<AddRoundedIcon />} onClick={() => { setCreating(true); setSelectedId(null) }}>新建</Button> : null}
        </Stack>
        <List disablePadding sx={{ mt: 1.5 }}>
          {snapshot.personas.map((item) => (
            <ListItemButton key={item.id} selected={!creating && item.id === selectedId} onClick={() => { setCreating(false); setSelectedId(item.id) }} sx={{ display: 'block', borderBottom: 1, borderColor: 'divider' }}>
              <Typography variant="subtitle2">{item.name}</Typography>
              <Typography variant="caption" color="text.secondary">{item.channel || '全渠道'} · {item.language || '全语言'} · v{item.published_version}</Typography>
            </ListItemButton>
          ))}
          {!snapshot.personas.length ? <OperatorEmptyState title="尚未配置人格" description="创建第一个企业 Agent 人格" /> : null}
        </List>
      </Paper>

      <Paper component="section" variant="outlined" sx={{ p: 2, minWidth: 0 }}>
        <Typography component="h2" variant="h3">{creating ? '新建人格' : '编辑人格'}</Typography>
        {!canManage ? <Alert severity="info" variant="outlined" sx={{ mt: 2 }}>当前为只读视图。</Alert> : null}
        {save.error ? <Box sx={{ mt: 2 }}><OperatorErrorNotice title="保存失败" error={save.error} fallback="请检查字段" /></Box> : null}
        <Stack spacing={1.5} sx={{ mt: 2 }}>
          <Box sx={{ display: 'grid', gap: 1.5, gridTemplateColumns: { xs: '1fr', md: 'repeat(2, minmax(0, 1fr))' } }}>
            <TextField label="人格编号" required disabled={!creating || !canManage} value={draft.profile_key} onChange={(event) => setDraft((current) => ({ ...current, profile_key: event.target.value.replace(/[^a-zA-Z0-9_.-]+/g, '-').toLowerCase() }))} />
            <TextField label="人格名称" required disabled={!canManage} value={draft.name} onChange={(event) => setDraft((current) => ({ ...current, name: event.target.value }))} />
            <TextField label="品牌名称" disabled={!canManage} value={draft.brand_name} onChange={(event) => setDraft((current) => ({ ...current, brand_name: event.target.value }))} />
            <TextField label="助手名称" disabled={!canManage} value={draft.assistant_name} onChange={(event) => setDraft((current) => ({ ...current, assistant_name: event.target.value }))} />
            <TextField label="角色名称" disabled={!canManage} value={draft.role_label} onChange={(event) => setDraft((current) => ({ ...current, role_label: event.target.value }))} />
            <TextField label="语气" disabled={!canManage} value={draft.tone} onChange={(event) => setDraft((current) => ({ ...current, tone: event.target.value }))} placeholder="例如：准确、克制、友好" />
            <TextField label="市场 ID" type="number" disabled={!canManage} value={draft.market_id} onChange={(event) => setDraft((current) => ({ ...current, market_id: event.target.value }))} />
            <TextField select label="渠道" disabled={!canManage} value={draft.channel} onChange={(event) => setDraft((current) => ({ ...current, channel: event.target.value }))}>
              <MenuItem value="">全部渠道</MenuItem><MenuItem value="webchat">网页客服</MenuItem><MenuItem value="whatsapp">WhatsApp</MenuItem><MenuItem value="email">邮件</MenuItem><MenuItem value="voice">语音</MenuItem>
            </TextField>
            <TextField label="语言" disabled={!canManage} value={draft.language} onChange={(event) => setDraft((current) => ({ ...current, language: event.target.value }))} placeholder="留空为全部语言" />
            <FormControlLabel control={<Checkbox disabled={!canManage} checked={draft.is_active} onChange={(event) => setDraft((current) => ({ ...current, is_active: event.target.checked }))} />} label="启用" />
          </Box>
          <TextField label="说明" multiline minRows={2} disabled={!canManage} value={draft.description} onChange={(event) => setDraft((current) => ({ ...current, description: event.target.value }))} />
          <TextField label="身份陈述" multiline minRows={3} disabled={!canManage} value={draft.identity_statement} onChange={(event) => setDraft((current) => ({ ...current, identity_statement: event.target.value }))} />
          <TextField label="身份问题回答规则" multiline minRows={2} disabled={!canManage} value={draft.identity_answer_rule} onChange={(event) => setDraft((current) => ({ ...current, identity_answer_rule: event.target.value }))} />
          <TextField label="转人工边界" multiline minRows={3} disabled={!canManage} value={draft.handoff_boundary} onChange={(event) => setDraft((current) => ({ ...current, handoff_boundary: event.target.value }))} />
          <TextField label="可提供能力" helperText="每行一项" multiline minRows={3} disabled={!canManage} value={draft.capabilities} onChange={(event) => setDraft((current) => ({ ...current, capabilities: event.target.value }))} />
          <TextField label="行为边界" helperText="每行一项" multiline minRows={4} disabled={!canManage} value={draft.guardrails} onChange={(event) => setDraft((current) => ({ ...current, guardrails: event.target.value }))} />
          <TextField label="禁止身份声明" helperText="每行一项" multiline minRows={3} disabled={!canManage} value={draft.disallowed_identity_claims} onChange={(event) => setDraft((current) => ({ ...current, disallowed_identity_claims: event.target.value }))} />
          <TextField label="版本摘要" multiline minRows={2} disabled={!canManage} value={draft.draft_summary} onChange={(event) => setDraft((current) => ({ ...current, draft_summary: event.target.value }))} />
          {canManage ? <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: 'wrap' }}>
            <Button variant="contained" disabled={busy} startIcon={save.isPending ? <CircularProgress color="inherit" size={16} /> : <SaveRoundedIcon />} onClick={() => save.mutate(false)}>保存草稿</Button>
            <Button variant="outlined" disabled={busy} startIcon={<PublishRoundedIcon />} onClick={() => save.mutate(true)}>保存并发布</Button>
          </Stack> : null}
        </Stack>
      </Paper>

      <Stack component="aside" spacing={2} sx={{ minWidth: 0 }}>
        <Paper variant="outlined" sx={{ p: 2 }}>
          <Typography component="h2" variant="h3">运行验证</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.75 }}>验证当前市场、渠道和语言最终命中哪个已发布人格。</Typography>
          <Button fullWidth variant="outlined" sx={{ mt: 2 }} disabled={testEvidence.isPending || !draft.profile_key} onClick={() => testEvidence.mutate()}>{testEvidence.isPending ? '验证中…' : '验证实际生效'}</Button>
          {testEvidence.error ? <Box sx={{ mt: 2 }}><OperatorErrorNotice title="验证失败" error={testEvidence.error} fallback="请检查人格作用域" /></Box> : null}
          {evidence ? <OperatorTechnicalDisclosure title="命中证据" summary={String(evidence.matched_profile_key || '未命中')}><Box component="pre" sx={{ m: 0, maxHeight: 420, overflow: 'auto', whiteSpace: 'pre-wrap', fontSize: 12 }}>{JSON.stringify(evidence, null, 2)}</Box></OperatorTechnicalDisclosure> : null}
        </Paper>
        {selected ? <Paper variant="outlined" sx={{ p: 2 }}><Typography variant="subtitle2">版本状态</Typography><Divider sx={{ my: 1.5 }} /><Typography variant="body2">已发布版本：v{selected.published_version}</Typography><Typography variant="body2">最后更新：{new Date(selected.updated_at).toLocaleString()}</Typography></Paper> : null}
      </Stack>
    </Box>
  )
}

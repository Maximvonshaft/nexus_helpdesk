import DeleteOutlineRoundedIcon from '@mui/icons-material/DeleteOutlineRounded'
import PublishRoundedIcon from '@mui/icons-material/PublishRounded'
import SaveRoundedIcon from '@mui/icons-material/SaveRounded'
import SearchRoundedIcon from '@mui/icons-material/SearchRounded'
import {
  Alert,
  Box,
  Button,
  Checkbox,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  FormControlLabel,
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
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { OperatorEmptyState, OperatorErrorNotice } from '@/app/OperatorPresentation'
import { agentControlApi, type AgentConfigDraft } from '@/lib/agentControlApi'
import type { AgentConfigResource, AgentControlSnapshot } from '@/lib/types'
import { asBoolean, asNumber, lineText, lines, resourceByType } from './formUtils'

type PolicyDraft = {
  is_active: boolean
  injection_enabled: boolean
  write_enabled: boolean
  require_explicit_consent: boolean
  max_facts: string
  retention_days: string
  allowed_keys: string
  prohibited_categories: string
  draft_summary: string
}

function policyDraft(resource: AgentConfigResource | null, fallback: Record<string, unknown>): PolicyDraft {
  const content = resource?.draft_content_json || resource?.published_content_json || fallback
  return {
    is_active: resource?.is_active ?? true,
    injection_enabled: asBoolean(content.injection_enabled, true),
    write_enabled: asBoolean(content.write_enabled),
    require_explicit_consent: asBoolean(content.require_explicit_consent, true),
    max_facts: String(asNumber(content.max_facts, 12)),
    retention_days: String(asNumber(content.retention_days, 180)),
    allowed_keys: lineText(content.allowed_keys),
    prohibited_categories: lineText(content.prohibited_categories),
    draft_summary: resource?.draft_summary || '',
  }
}

export function MemoryPanel({ snapshot, canManage, tenantKey }: { snapshot: AgentControlSnapshot; canManage: boolean; tenantKey: string }) {
  const queryClient = useQueryClient()
  const resource = resourceByType(snapshot.resources, 'memory_policy')
  const resolvedPolicy = useMemo(
    () => policyDraft(resource, snapshot.memory_policy || {}),
    [resource, snapshot.memory_policy],
  )
  const [policy, setPolicy] = useState<PolicyDraft>(resolvedPolicy)
  useEffect(() => setPolicy(resolvedPolicy), [resolvedPolicy])

  const savePolicy = useMutation({
    mutationFn: async (publish: boolean) => {
      const payload: AgentConfigDraft = {
        resource_key: resource?.resource_key || 'agent.memory.default',
        config_type: 'memory_policy',
        name: resource?.name || 'Default customer memory policy',
        description: 'Governed customer long-term memory policy',
        scope_type: 'global',
        scope_value: null,
        market_id: null,
        is_active: policy.is_active,
        draft_summary: policy.draft_summary || null,
        draft_content_json: {
          schema_version: 'nexus.customer_memory_policy.v1',
          injection_enabled: policy.injection_enabled,
          write_enabled: policy.write_enabled,
          require_explicit_consent: policy.require_explicit_consent,
          max_facts: Number(policy.max_facts),
          retention_days: Number(policy.retention_days),
          allowed_keys: lines(policy.allowed_keys),
          prohibited_categories: lines(policy.prohibited_categories),
          enabled: policy.is_active,
        },
      }
      const item = resource
        ? await agentControlApi.updateConfig(resource.id, payload)
        : await agentControlApi.createConfig(payload)
      if (publish) await agentControlApi.publishConfig(item.id, 'Customer memory policy publish')
      return item
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['agentControlSnapshot'] }),
  })

  const [customerId, setCustomerId] = useState('')
  const customerMemory = useQuery({
    queryKey: ['customerMemory', tenantKey, customerId],
    queryFn: () => agentControlApi.customerMemory(Number(customerId), tenantKey, true),
    enabled: Boolean(customerId && Number(customerId) > 0),
    retry: false,
  })
  const [memoryKey, setMemoryKey] = useState('')
  const [memoryValue, setMemoryValue] = useState('')
  const [consentBasis, setConsentBasis] = useState('')
  const [sensitivity, setSensitivity] = useState('standard')
  const [forgetOpen, setForgetOpen] = useState(false)
  const upsert = useMutation({
    mutationFn: () => agentControlApi.upsertCustomerMemory(Number(customerId), {
      tenant_key: tenantKey,
      memory_key: memoryKey,
      value_text: memoryValue,
      consent_basis: consentBasis || null,
      source_type: 'operator',
      confidence: 1,
      sensitivity,
    }),
    onSuccess: async () => {
      setMemoryKey('')
      setMemoryValue('')
      setConsentBasis('')
      await customerMemory.refetch()
    },
  })
  const deactivate = useMutation({
    mutationFn: (memoryId: number) => agentControlApi.deactivateCustomerMemory(Number(customerId), memoryId, tenantKey),
    onSuccess: () => customerMemory.refetch(),
  })
  const forget = useMutation({
    mutationFn: () => agentControlApi.forgetCustomerMemory(Number(customerId), tenantKey),
    onSuccess: async () => {
      setForgetOpen(false)
      await customerMemory.refetch()
    },
  })

  return (
    <Stack spacing={2}>
      <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', xl: 'minmax(0, .85fr) minmax(0, 1.15fr)' } }}>
        <Paper component="section" variant="outlined" sx={{ p: 2 }}>
          <Typography component="h2" variant="h3">记忆策略</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>控制长期事实是否注入、是否允许写入、保留期限和字段白名单。</Typography>
          {savePolicy.error ? <Box sx={{ mt: 2 }}><OperatorErrorNotice title="策略保存失败" error={savePolicy.error} fallback="请检查字段白名单" /></Box> : null}
          <Stack spacing={1.5} sx={{ mt: 2 }}>
            <FormControlLabel control={<Switch disabled={!canManage} checked={policy.injection_enabled} onChange={(event) => setPolicy((current) => ({ ...current, injection_enabled: event.target.checked }))} />} label="向 Agent 注入有效长期事实" />
            <FormControlLabel control={<Switch disabled={!canManage} checked={policy.write_enabled} onChange={(event) => setPolicy((current) => ({ ...current, write_enabled: event.target.checked }))} />} label="允许创建或更新长期事实" />
            <FormControlLabel control={<Checkbox disabled={!canManage} checked={policy.require_explicit_consent} onChange={(event) => setPolicy((current) => ({ ...current, require_explicit_consent: event.target.checked }))} />} label="写入必须记录明确同意依据" />
            <Box sx={{ display: 'grid', gap: 1.25, gridTemplateColumns: 'repeat(2, minmax(0, 1fr))' }}>
              <TextField label="最多注入事实数" type="number" disabled={!canManage} value={policy.max_facts} onChange={(event) => setPolicy((current) => ({ ...current, max_facts: event.target.value }))} />
              <TextField label="保留天数" type="number" disabled={!canManage} value={policy.retention_days} onChange={(event) => setPolicy((current) => ({ ...current, retention_days: event.target.value }))} />
            </Box>
            <TextField label="允许的记忆字段" helperText="每行一个安全字段；未列出的字段禁止写入" multiline minRows={5} disabled={!canManage} value={policy.allowed_keys} onChange={(event) => setPolicy((current) => ({ ...current, allowed_keys: event.target.value }))} />
            <TextField label="禁止类别" helperText="每行一个；例如 credential、payment_card、health" multiline minRows={4} disabled={!canManage} value={policy.prohibited_categories} onChange={(event) => setPolicy((current) => ({ ...current, prohibited_categories: event.target.value }))} />
            <Alert severity="warning" variant="outlined">凭据、支付卡、政府证件、健康、生物特征和原始会话记录不允许进入长期记忆。</Alert>
            <FormControlLabel control={<Switch disabled={!canManage} checked={policy.is_active} onChange={(event) => setPolicy((current) => ({ ...current, is_active: event.target.checked }))} />} label="启用记忆策略" />
            <TextField label="版本摘要" multiline minRows={2} disabled={!canManage} value={policy.draft_summary} onChange={(event) => setPolicy((current) => ({ ...current, draft_summary: event.target.value }))} />
            {canManage ? <Stack direction="row" spacing={1}>
              <Button variant="contained" disabled={savePolicy.isPending} startIcon={savePolicy.isPending ? <CircularProgress color="inherit" size={16} /> : <SaveRoundedIcon />} onClick={() => savePolicy.mutate(false)}>保存草稿</Button>
              <Button variant="outlined" disabled={savePolicy.isPending} startIcon={<PublishRoundedIcon />} onClick={() => savePolicy.mutate(true)}>保存并发布</Button>
            </Stack> : null}
          </Stack>
        </Paper>

        <Paper component="section" variant="outlined" sx={{ p: 2, minWidth: 0 }}>
          <Typography component="h2" variant="h3">客户记忆治理</Typography>
          <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} sx={{ mt: 2 }}>
            <TextField fullWidth label="客户 ID" type="number" value={customerId} onChange={(event) => setCustomerId(event.target.value)} />
            <Button variant="outlined" startIcon={<SearchRoundedIcon />} disabled={!customerId} onClick={() => customerMemory.refetch()}>查询</Button>
          </Stack>
          {customerMemory.error ? <Box sx={{ mt: 2 }}><OperatorErrorNotice title="无法读取客户记忆" error={customerMemory.error} fallback="请检查客户与租户" /></Box> : null}
          {customerMemory.isFetching ? <CircularProgress size={20} sx={{ mt: 2 }} /> : null}
          {customerMemory.data ? (
            <>
              <TableContainer sx={{ mt: 2 }}>
                <Table size="small">
                  <TableHead><TableRow><TableCell>字段</TableCell><TableCell>事实</TableCell><TableCell>来源/同意</TableCell><TableCell>状态</TableCell><TableCell /></TableRow></TableHead>
                  <TableBody>
                    {customerMemory.data.facts.map((fact) => (
                      <TableRow key={fact.id}>
                        <TableCell>{fact.memory_key}</TableCell>
                        <TableCell sx={{ maxWidth: 260, whiteSpace: 'normal' }}>{fact.value_text}</TableCell>
                        <TableCell>{fact.source_type}<br />{fact.consent_basis || '未记录'}</TableCell>
                        <TableCell>{fact.is_active ? '有效' : '停用'}<br />{fact.expires_at ? new Date(fact.expires_at).toLocaleDateString() : '不过期'}</TableCell>
                        <TableCell>{canManage && fact.is_active ? <Button size="small" color="error" disabled={deactivate.isPending} onClick={() => deactivate.mutate(fact.id)}>停用</Button> : null}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
              {!customerMemory.data.facts.length ? <OperatorEmptyState title="没有长期记忆" description="该客户尚未保存长期事实" /> : null}
              {canManage ? (
                <Stack spacing={1.25} sx={{ mt: 2, pt: 2, borderTop: 1, borderColor: 'divider' }}>
                  <Typography variant="subtitle2">新增或纠正事实</Typography>
                  <TextField select label="记忆字段" value={memoryKey} onChange={(event) => setMemoryKey(event.target.value)}>
                    {lines(policy.allowed_keys).map((key) => <MenuItem key={key} value={key}>{key}</MenuItem>)}
                  </TextField>
                  <TextField label="事实内容" multiline minRows={3} value={memoryValue} onChange={(event) => setMemoryValue(event.target.value)} />
                  <TextField label="明确同意依据" value={consentBasis} onChange={(event) => setConsentBasis(event.target.value)} placeholder="例如：客户在会话中明确要求记住" />
                  <TextField select label="敏感级别" value={sensitivity} onChange={(event) => setSensitivity(event.target.value)}>
                    <MenuItem value="standard">标准（可注入）</MenuItem><MenuItem value="restricted">受限（不注入）</MenuItem>
                  </TextField>
                  {upsert.error ? <OperatorErrorNotice title="写入失败" error={upsert.error} fallback="请检查策略、同意和内容" /> : null}
                  <Stack direction="row" spacing={1}>
                    <Button variant="contained" disabled={!memoryKey || !memoryValue.trim() || upsert.isPending} onClick={() => upsert.mutate()}>保存事实</Button>
                    <Button color="error" variant="outlined" startIcon={<DeleteOutlineRoundedIcon />} onClick={() => setForgetOpen(true)}>全部忘记</Button>
                  </Stack>
                </Stack>
              ) : null}
            </>
          ) : <Alert severity="info" variant="outlined" sx={{ mt: 2 }}>输入客户 ID 查看、纠正、停用或删除长期事实。</Alert>}
        </Paper>
      </Box>

      <Dialog open={forgetOpen} onClose={() => !forget.isPending && setForgetOpen(false)}>
        <DialogTitle>删除全部客户记忆？</DialogTitle>
        <DialogContent><DialogContentText>这会物理删除该客户全部长期记忆事实，并记录治理审计。此操作不可恢复。</DialogContentText></DialogContent>
        <DialogActions><Button onClick={() => setForgetOpen(false)}>取消</Button><Button color="error" disabled={forget.isPending} onClick={() => forget.mutate()}>确认全部忘记</Button></DialogActions>
      </Dialog>
    </Stack>
  )
}

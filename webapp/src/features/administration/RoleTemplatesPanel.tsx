import AddRoundedIcon from '@mui/icons-material/AddRounded'
import PublishRoundedIcon from '@mui/icons-material/PublishRounded'
import SaveRoundedIcon from '@mui/icons-material/SaveRounded'
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  MenuItem,
  Paper,
  Stack,
  Switch,
  TextField,
  Typography,
} from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { OperatorEmptyState, OperatorErrorNotice } from '@/app/OperatorPresentation'
import { governanceApi, type RoleTemplate, type RoleTemplateDraft } from '@/lib/governanceApi'

const EMPTY_DRAFT: RoleTemplateDraft = {
  role_key: '',
  display_name: '',
  description: '',
  base_role: 'agent',
  risk_level: 'standard',
  capabilities: [],
}

export function RoleTemplatesPanel() {
  const queryClient = useQueryClient()
  const templates = useQuery({ queryKey: ['governance', 'role-templates'], queryFn: governanceApi.roleTemplates })
  const capabilities = useQuery({ queryKey: ['governance', 'capabilities'], queryFn: governanceApi.capabilities })
  const assignments = useQuery({ queryKey: ['governance', 'role-assignments'], queryFn: governanceApi.roleAssignments })
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [draft, setDraft] = useState<RoleTemplateDraft>(EMPTY_DRAFT)
  const [active, setActive] = useState(true)
  const [createOpen, setCreateOpen] = useState(false)
  const [applyUserId, setApplyUserId] = useState<number | ''>('')
  const selected = useMemo(
    () => templates.data?.find((item) => item.id === selectedId) ?? null,
    [selectedId, templates.data],
  )

  useEffect(() => {
    if (!selected && templates.data?.length) setSelectedId(templates.data[0].id)
  }, [selected, templates.data])
  useEffect(() => {
    if (!selected) return
    setDraft({
      role_key: selected.role_key,
      display_name: selected.display_name,
      description: selected.description || '',
      base_role: selected.base_role,
      risk_level: selected.risk_level,
      capabilities: selected.draft_capabilities,
    })
    setActive(selected.is_active)
  }, [selected])

  const invalidate = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['governance', 'role-templates'] }),
      queryClient.invalidateQueries({ queryKey: ['governance', 'role-assignments'] }),
      queryClient.invalidateQueries({ queryKey: ['identityRolePolicies'] }),
    ])
  }
  const create = useMutation({
    mutationFn: governanceApi.createRoleTemplate,
    onSuccess: async (row) => { setCreateOpen(false); setSelectedId(row.id); await invalidate() },
  })
  const update = useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: Partial<RoleTemplateDraft> & { is_active?: boolean } }) =>
      governanceApi.updateRoleTemplate(id, payload),
    onSuccess: invalidate,
  })
  const publish = useMutation({
    mutationFn: (id: number) => governanceApi.publishRoleTemplate(id, '运营控制面发布'),
    onSuccess: invalidate,
  })
  const apply = useMutation({
    mutationFn: ({ templateId, userId }: { templateId: number; userId: number }) =>
      governanceApi.applyRoleTemplate(templateId, userId),
    onSuccess: async () => { setApplyUserId(''); await invalidate() },
  })

  const saveSelected = () => {
    if (!selected) return
    update.mutate({
      id: selected.id,
      payload: {
        display_name: draft.display_name,
        description: draft.description || null,
        base_role: draft.base_role,
        risk_level: draft.risk_level,
        capabilities: draft.capabilities,
        is_active: active,
      },
    })
  }

  if (templates.isLoading || capabilities.isLoading || assignments.isLoading) {
    return <Stack alignItems="center" sx={{ py: 6 }}><CircularProgress /></Stack>
  }
  const error = templates.error || capabilities.error || assignments.error
  if (error) return <OperatorErrorNotice title="无法读取角色模板" error={error} fallback="请稍后重试" />

  return (
    <Stack spacing={2}>
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} justifyContent="space-between">
          <Box>
            <Typography component="h2" variant="h2">角色模板</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
              模板发布后编译到现有用户角色与能力覆盖权威；运行时不会直接读取模板表。
            </Typography>
          </Box>
          <Button startIcon={<AddRoundedIcon />} variant="contained" onClick={() => setCreateOpen(true)}>
            新建模板
          </Button>
        </Stack>
      </Paper>

      {!templates.data?.length ? (
        <OperatorEmptyState title="暂无角色模板" description="建立租户角色模板后再发布并分配给员工。" />
      ) : (
        <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', lg: 'minmax(260px, 0.8fr) minmax(0, 2fr)' }, gap: 2 }}>
          <Paper variant="outlined" sx={{ p: 1.5 }}>
            <Stack spacing={1}>
              {templates.data.map((item) => (
                <Button
                  key={item.id}
                  variant={item.id === selectedId ? 'contained' : 'text'}
                  color={item.id === selectedId ? 'primary' : 'inherit'}
                  onClick={() => setSelectedId(item.id)}
                  sx={{ justifyContent: 'space-between', textAlign: 'left' }}
                >
                  <span>{item.display_name}</span>
                  <Chip size="small" label={item.published_version ? `v${item.published_version}` : '草稿'} />
                </Button>
              ))}
            </Stack>
          </Paper>

          {selected ? (
            <Paper variant="outlined" sx={{ p: 2 }}>
              <Stack spacing={2}>
                <Stack direction={{ xs: 'column', md: 'row' }} spacing={1} alignItems={{ md: 'center' }}>
                  <Box sx={{ flex: 1 }}>
                    <Typography variant="h3">{selected.display_name}</Typography>
                    <Typography variant="caption" color="text.secondary">
                      {selected.role_key} · 已分配 {selected.assignment_count} 人 · {selected.can_manage ? '租户可维护' : '系统只读'}
                    </Typography>
                  </Box>
                  <Chip color={selected.risk_level === 'administrator' ? 'error' : selected.risk_level === 'sensitive' ? 'warning' : 'default'} label={selected.risk_level} />
                  <Chip color={selected.is_active ? 'success' : 'default'} label={selected.is_active ? '启用' : '停用'} />
                </Stack>
                {!selected.can_manage ? <Alert severity="info">系统保护模板只能查看，不能修改、发布或停用。</Alert> : null}
                <TextField label="显示名称" value={draft.display_name} disabled={!selected.can_manage} onChange={(event) => setDraft({ ...draft, display_name: event.target.value })} />
                <TextField label="说明" multiline minRows={2} value={draft.description || ''} disabled={!selected.can_manage} onChange={(event) => setDraft({ ...draft, description: event.target.value })} />
                <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
                  <TextField select fullWidth label="基础角色" value={draft.base_role} disabled={!selected.can_manage} onChange={(event) => setDraft({ ...draft, base_role: event.target.value })}>
                    {['admin', 'manager', 'lead', 'agent', 'auditor'].map((role) => <MenuItem key={role} value={role}>{role}</MenuItem>)}
                  </TextField>
                  <TextField select fullWidth label="风险等级" value={draft.risk_level} disabled={!selected.can_manage} onChange={(event) => setDraft({ ...draft, risk_level: event.target.value })}>
                    {['standard', 'sensitive', 'administrator'].map((risk) => <MenuItem key={risk} value={risk}>{risk}</MenuItem>)}
                  </TextField>
                </Stack>
                <Autocomplete
                  multiple
                  options={capabilities.data || []}
                  value={draft.capabilities}
                  disabled={!selected.can_manage}
                  onChange={(_, value) => setDraft({ ...draft, capabilities: value })}
                  renderInput={(params) => <TextField {...params} label="能力集合" helperText="保存修改草稿；发布后才可用于分配。" />}
                />
                <FormControlLabel control={<Switch checked={active} disabled={!selected.can_manage} onChange={(event) => setActive(event.target.checked)} />} label="启用模板" />
                <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
                  <Button variant="outlined" startIcon={<SaveRoundedIcon />} disabled={!selected.can_manage || update.isPending} onClick={saveSelected}>保存草稿</Button>
                  <Button variant="contained" startIcon={<PublishRoundedIcon />} disabled={!selected.can_manage || publish.isPending || !draft.capabilities.length} onClick={() => publish.mutate(selected.id)}>发布新版本</Button>
                </Stack>
                {(update.error || publish.error) ? <OperatorErrorNotice title="角色模板操作失败" error={update.error || publish.error} fallback="请检查能力集合和并发状态" /> : null}

                <Box sx={{ borderTop: 1, borderColor: 'divider', pt: 2 }}>
                  <Typography variant="h3">分配已发布模板</Typography>
                  <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} sx={{ mt: 1 }}>
                    <TextField select fullWidth label="员工" value={applyUserId} onChange={(event) => setApplyUserId(Number(event.target.value))}>
                      {(assignments.data || []).map((item) => (
                        <MenuItem key={item.user_id} value={item.user_id}>{item.display_name} · {item.username}{item.assignment ? ` · ${item.assignment.template_name}` : ''}</MenuItem>
                      ))}
                    </TextField>
                    <Button
                      variant="contained"
                      disabled={!selected.published_version || !applyUserId || apply.isPending}
                      onClick={() => apply.mutate({ templateId: selected.id, userId: Number(applyUserId) })}
                    >
                      分配并撤销旧会话
                    </Button>
                  </Stack>
                  {apply.error ? <OperatorErrorNotice title="角色分配失败" error={apply.error} fallback="请确认不会移除最后一个治理账号" /> : null}
                </Box>
              </Stack>
            </Paper>
          ) : null}
        </Box>
      )}

      <Dialog open={createOpen} onClose={() => setCreateOpen(false)} fullWidth maxWidth="md">
        <RoleTemplateEditor title="新建角色模板" capabilities={capabilities.data || []} onSubmit={(value) => create.mutate(value)} pending={create.isPending} error={create.error} onClose={() => setCreateOpen(false)} />
      </Dialog>
    </Stack>
  )
}

function RoleTemplateEditor({ title, capabilities, onSubmit, pending, error, onClose }: {
  title: string
  capabilities: string[]
  onSubmit: (draft: RoleTemplateDraft) => void
  pending: boolean
  error: unknown
  onClose: () => void
}) {
  const [draft, setDraft] = useState<RoleTemplateDraft>(EMPTY_DRAFT)
  return (
    <>
      <DialogTitle>{title}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          <TextField label="角色键" value={draft.role_key} onChange={(event) => setDraft({ ...draft, role_key: event.target.value.toLowerCase() })} helperText="仅允许小写字母、数字、点、下划线和连字符。" />
          <TextField label="显示名称" value={draft.display_name} onChange={(event) => setDraft({ ...draft, display_name: event.target.value })} />
          <TextField label="说明" multiline minRows={2} value={draft.description || ''} onChange={(event) => setDraft({ ...draft, description: event.target.value })} />
          <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
            <TextField select fullWidth label="基础角色" value={draft.base_role} onChange={(event) => setDraft({ ...draft, base_role: event.target.value })}>
              {['admin', 'manager', 'lead', 'agent', 'auditor'].map((role) => <MenuItem key={role} value={role}>{role}</MenuItem>)}
            </TextField>
            <TextField select fullWidth label="风险等级" value={draft.risk_level} onChange={(event) => setDraft({ ...draft, risk_level: event.target.value })}>
              {['standard', 'sensitive', 'administrator'].map((risk) => <MenuItem key={risk} value={risk}>{risk}</MenuItem>)}
            </TextField>
          </Stack>
          <Autocomplete multiple options={capabilities} value={draft.capabilities} onChange={(_, value) => setDraft({ ...draft, capabilities: value })} renderInput={(params) => <TextField {...params} label="能力集合" />} />
          {error ? <OperatorErrorNotice title="创建失败" error={error} fallback="请检查角色键和能力集合" /> : null}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>取消</Button>
        <Button variant="contained" disabled={pending || !draft.role_key.trim() || !draft.display_name.trim()} onClick={() => onSubmit(draft)}>创建</Button>
      </DialogActions>
    </>
  )
}

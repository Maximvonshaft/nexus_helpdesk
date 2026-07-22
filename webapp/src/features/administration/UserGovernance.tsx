import AddRoundedIcon from '@mui/icons-material/AddRounded'
import EditRoundedIcon from '@mui/icons-material/EditRounded'
import KeyRoundedIcon from '@mui/icons-material/KeyRounded'
import PersonOffRoundedIcon from '@mui/icons-material/PersonOffRounded'
import PersonRoundedIcon from '@mui/icons-material/PersonRounded'
import RestartAltRoundedIcon from '@mui/icons-material/RestartAltRounded'
import {
  Alert,
  Box,
  Button,
  Checkbox,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Divider,
  FormControlLabel,
  FormGroup,
  MenuItem,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from '@mui/material'
import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useMemo, useState } from 'react'
import {
  OperatorEmptyState,
  OperatorErrorNotice,
  OperatorLoadingState,
  OperatorTechnicalDisclosure,
} from '@/app/OperatorPresentation'
import { formatDateTime, sanitizeDisplayText } from '@/lib/format'
import { supportApi } from '@/lib/supportApi'
import type { AdminUser, IdentityTeam, RolePolicy } from '@/lib/types'

type UserDraft = {
  username: string
  password: string
  displayName: string
  email: string
  role: string
  teamId: string
  capabilities: string[]
}

const emptyDraft: UserDraft = {
  username: '',
  password: '',
  displayName: '',
  email: '',
  role: 'agent',
  teamId: '',
  capabilities: [],
}

const capabilityLabels: Record<string, string> = {
  'operator_queue.read': '查看待处理任务',
  'ticket.read': '查看案例与工单',
  'ticket.write': '更新案例与工单',
  'ticket.assign': '分配案例与工单',
  'attachment.read': '查看附件',
  'attachment.write': '上传附件',
  'customer_profile.read': '查看客户资料',
  'customer_profile.write': '更新客户资料',
  'outbound.send': '发送客户消息',
  'note.read': '查看内部备注',
  'note.write': '添加内部备注',
  'user.manage': '管理用户与权限',
  'channel_account.manage': '管理渠道账号',
  'bulletin.manage': '管理公告',
  'ai_config.read': '查看自动处理配置',
  'ai_config.manage': '管理自动处理配置',
  'runtime.manage': '管理系统运行',
  'market.manage': '管理市场配置',
  'qa.read': '查看质量数据',
  'qa.manage': '管理质量规则',
  'security.read': '查看安全记录',
  'audit.read': '查看审计记录',
  'webcall.voice.read': '查看语音会话',
  'webcall.voice.queue.view': '查看来电队列',
  'webcall.voice.accept': '接听来电',
  'webcall.voice.reject': '拒接来电',
  'webcall.voice.end': '结束通话',
  'webcall.voice.control': '管理通话',
  'webchat.read': '查看网页会话',
  'webchat.write': '回复网页会话',
}

function roleLabel(role: string) {
  if (role === 'admin') return '管理员'
  if (role === 'manager') return '运营经理'
  if (role === 'lead') return '组长'
  if (role === 'agent') return '客服专员'
  if (role === 'auditor') return '审计员'
  return sanitizeDisplayText(role)
}

function capabilityGroup(capability: string) {
  if (capability.startsWith('tool:')) return '受控业务操作'
  const prefix = capability.split('.', 1)[0]
  const labels: Record<string, string> = {
    ticket: '案例与工单',
    attachment: '附件',
    customer_profile: '客户资料',
    outbound: '客户沟通',
    ai_intake: '自动处理',
    note: '内部备注',
    user: '用户管理',
    channel_account: '渠道管理',
    bulletin: '公告管理',
    ai_config: '自动处理配置',
    runtime: '系统运行',
    market: '市场配置',
    qa: '质量管理',
    security: '安全记录',
    audit: '审计记录',
    webcall: '语音会话',
    webchat: '网页会话',
    operator_queue: '任务队列',
  }
  return labels[prefix] || '其他权限'
}

function capabilityLabel(capability: string) {
  if (capabilityLabels[capability]) return capabilityLabels[capability]
  if (capability.startsWith('tool:')) {
    const segments = capability.replace(/^tool:/, '').split(':')
    const operation = segments.at(-1) === 'write' ? '执行' : '查看'
    const subject = segments.slice(0, -1).join(' · ').replaceAll('_', ' ')
    return `${operation} ${subject}`
  }
  const action = capability.split('.').at(-1)
  if (action === 'read') return '查看'
  if (action === 'write' || action === 'manage') return '管理'
  return '使用'
}

export function UserGovernance({
  currentUserId,
  roles,
  teams,
  referencesLoading,
}: {
  currentUserId: number
  roles: RolePolicy[]
  teams: IdentityTeam[]
  referencesLoading: boolean
}) {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const [editorOpen, setEditorOpen] = useState(false)
  const [selectedUser, setSelectedUser] = useState<AdminUser | null>(null)
  const [draft, setDraft] = useState<UserDraft>(emptyDraft)
  const [resetUser, setResetUser] = useState<AdminUser | null>(null)
  const [resetPassword, setResetPassword] = useState('')
  const [resetConfirmation, setResetConfirmation] = useState('')
  const [toggleUser, setToggleUser] = useState<AdminUser | null>(null)

  const usersQuery = useInfiniteQuery({
    queryKey: ['adminUsers'],
    queryFn: ({ pageParam }) => supportApi.adminUsers({ cursor: pageParam as string | null, limit: 100, includeInactive: true }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor || undefined,
    retry: false,
  })
  const users = useMemo(() => usersQuery.data?.pages.flatMap((page) => page.items) ?? [], [usersQuery.data?.pages])
  const activeAdminCount = useMemo(
    () => users.filter((user) => user.is_active && user.role === 'admin').length,
    [users],
  )
  const selectedUserIsFinalActiveAdmin = Boolean(
    selectedUser?.is_active && selectedUser.role === 'admin' && activeAdminCount === 1,
  )
  const normalizedSearch = search.trim().toLowerCase()
  const visibleUsers = useMemo(
    () => users.filter((user) => !normalizedSearch || [user.username, user.display_name, user.email, user.role]
      .some((value) => String(value || '').toLowerCase().includes(normalizedSearch))),
    [normalizedSearch, users],
  )
  const capabilityGroups = useMemo(() => {
    const catalog = [...new Set(roles.flatMap((role) => role.default_capabilities))].sort()
    const groups = new Map<string, string[]>()
    for (const capability of catalog) {
      const group = capabilityGroup(capability)
      groups.set(group, [...(groups.get(group) ?? []), capability])
    }
    return [...groups.entries()]
  }, [roles])

  const roleDefaults = (role: string) => roles.find((item) => item.role === role)?.default_capabilities ?? []

  const invalidate = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['adminUsers'] }),
      queryClient.invalidateQueries({ queryKey: ['securityAudit'] }),
      queryClient.invalidateQueries({ queryKey: ['identityTeams'] }),
    ])
  }

  const saveUser = useMutation({
    mutationFn: async () => {
      const teamId = draft.teamId ? Number(draft.teamId) : null
      if (selectedUser) {
        const updated = await supportApi.updateAdminUser(selectedUser.id, {
          display_name: draft.displayName.trim(),
          email: draft.email.trim() || undefined,
          role: draft.role,
          team_id: teamId ?? undefined,
          capabilities: draft.capabilities,
        })
        if (teamId === null && selectedUser.team_id !== null && selectedUser.team_id !== undefined) {
          await supportApi.clearAdminUserTeam(selectedUser.id)
        }
        return updated
      }
      return supportApi.createAdminUser({
        username: draft.username.trim(),
        password: draft.password,
        display_name: draft.displayName.trim(),
        email: draft.email.trim() || null,
        role: draft.role,
        team_id: teamId,
        capabilities: draft.capabilities,
      })
    },
    onSuccess: async () => {
      const updatedCurrentUser = selectedUser?.id === currentUserId
      setEditorOpen(false)
      setSelectedUser(null)
      setDraft(emptyDraft)
      await invalidate()
      if (updatedCurrentUser) await queryClient.invalidateQueries({ queryKey: ['session'] })
    },
  })

  const toggleStatus = useMutation({
    mutationFn: (user: AdminUser) => user.is_active
      ? supportApi.deactivateAdminUser(user.id)
      : supportApi.activateAdminUser(user.id),
    onSuccess: async () => {
      setToggleUser(null)
      await invalidate()
    },
  })

  const resetCredential = useMutation({
    mutationFn: () => {
      if (!resetUser) throw new Error('未选择用户')
      return supportApi.resetAdminUserPassword(resetUser.id, resetPassword)
    },
    onSuccess: async () => {
      setResetUser(null)
      setResetPassword('')
      setResetConfirmation('')
      await invalidate()
    },
  })

  const openCreate = () => {
    saveUser.reset()
    const defaultRole = roles.find((item) => item.role === 'agent') ?? roles[0]
    setSelectedUser(null)
    setDraft({
      ...emptyDraft,
      role: defaultRole?.role ?? 'agent',
      capabilities: [...(defaultRole?.default_capabilities ?? [])],
    })
    setEditorOpen(true)
  }

  const openEdit = (user: AdminUser) => {
    saveUser.reset()
    setSelectedUser(user)
    setDraft({
      username: user.username,
      password: '',
      displayName: user.display_name,
      email: user.email || '',
      role: user.role,
      teamId: user.team_id ? String(user.team_id) : '',
      capabilities: [...user.capabilities],
    })
    setEditorOpen(true)
  }

  const changeRole = (role: string) => {
    if (selectedUserIsFinalActiveAdmin && role !== 'admin') return
    setDraft((current) => ({ ...current, role, capabilities: [...roleDefaults(role)] }))
  }

  const restoreRoleDefaults = () => {
    const defaults = [...roleDefaults(draft.role)]
    const protectedDefaults = selectedUserIsFinalActiveAdmin && !defaults.includes('user.manage')
      ? [...defaults, 'user.manage'].sort()
      : defaults
    setDraft((current) => ({ ...current, capabilities: protectedDefaults }))
  }

  const toggleCapability = (capability: string) => {
    if (
      selectedUserIsFinalActiveAdmin
      && capability === 'user.manage'
      && draft.capabilities.includes(capability)
    ) return
    setDraft((current) => ({
      ...current,
      capabilities: current.capabilities.includes(capability)
        ? current.capabilities.filter((item) => item !== capability)
        : [...current.capabilities, capability].sort(),
    }))
  }

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!draft.displayName.trim() || !draft.role || (!selectedUser && (!draft.username.trim() || !draft.password))) return
    saveUser.mutate()
  }

  const selectedRoleDefaults = roleDefaults(draft.role)
  const resetReady = resetPassword.length > 0 && resetPassword === resetConfirmation

  return (
    <Paper component="section" variant="outlined" aria-labelledby="user-governance-title" sx={{ p: 2 }}>
      <Stack direction={{ xs: 'column', md: 'row' }} spacing={2} sx={{ alignItems: { xs: 'stretch', md: 'center' }, justifyContent: 'space-between' }}>
        <Box>
          <Typography id="user-governance-title" component="h2" variant="h2">用户与权限</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            创建账号、分配角色和团队，并按需调整权限。停用账号不会删除历史记录。
          </Typography>
        </Box>
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
          <TextField size="small" label="搜索用户" value={search} onChange={(event) => setSearch(event.target.value)} />
          <Button variant="contained" startIcon={<AddRoundedIcon />} disabled={referencesLoading || !roles.length} onClick={openCreate}>创建账号</Button>
        </Stack>
      </Stack>
      <Divider sx={{ my: 2 }} />

      {usersQuery.isError ? <OperatorErrorNotice title="无法读取用户" error={usersQuery.error} fallback="请稍后重试" /> : null}
      {toggleStatus.isError ? <Box sx={{ mb: 2 }}><OperatorErrorNotice title="账号状态更新失败" error={toggleStatus.error} fallback="请检查账号保护规则" /></Box> : null}
      {usersQuery.isLoading ? <OperatorLoadingState label="正在加载用户…" minHeight={220} /> : !visibleUsers.length ? (
        <OperatorEmptyState title="没有匹配的用户" description={search ? '请调整搜索条件。' : '创建第一个运营账号。'} />
      ) : (
        <>
          <TableContainer>
            <Table size="small" aria-label="用户与权限列表">
              <TableHead>
                <TableRow>
                  <TableCell>用户</TableCell>
                  <TableCell>角色</TableCell>
                  <TableCell>团队</TableCell>
                  <TableCell align="right">权限</TableCell>
                  <TableCell>状态</TableCell>
                  <TableCell>最近更新</TableCell>
                  <TableCell align="right">操作</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {visibleUsers.map((user) => {
                  const team = teams.find((item) => item.id === user.team_id)
                  return (
                    <TableRow key={user.id} hover>
                      <TableCell>
                        <Typography variant="subtitle2">{sanitizeDisplayText(user.display_name)}</Typography>
                        <Typography variant="caption" color="text.secondary">{sanitizeDisplayText(user.username)}{user.email ? ` · ${sanitizeDisplayText(user.email)}` : ''}</Typography>
                      </TableCell>
                      <TableCell><Chip size="small" label={roleLabel(user.role)} /></TableCell>
                      <TableCell>{team ? team.name : '未分配'}</TableCell>
                      <TableCell align="right">{user.capabilities.length}</TableCell>
                      <TableCell><Chip size="small" color={user.is_active ? 'success' : 'default'} label={user.is_active ? '启用' : '停用'} /></TableCell>
                      <TableCell>{formatDateTime(user.updated_at)}</TableCell>
                      <TableCell align="right">
                        <Stack direction="row" spacing={0.5} useFlexGap sx={{ justifyContent: 'flex-end', flexWrap: 'wrap' }}>
                          <Button size="small" color="inherit" startIcon={<EditRoundedIcon />} onClick={() => openEdit(user)}>编辑</Button>
                          <Button size="small" color="inherit" startIcon={<KeyRoundedIcon />} onClick={() => { resetCredential.reset(); setResetUser(user) }}>重置密码</Button>
                          <Button
                            size="small"
                            color={user.is_active ? 'warning' : 'success'}
                            startIcon={user.is_active ? <PersonOffRoundedIcon /> : <PersonRoundedIcon />}
                            disabled={user.id === currentUserId || toggleStatus.isPending}
                            onClick={() => setToggleUser(user)}
                          >
                            {user.is_active ? '停用' : '启用'}
                          </Button>
                        </Stack>
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          </TableContainer>
          {usersQuery.hasNextPage ? (
            <Box sx={{ display: 'flex', justifyContent: 'center', mt: 2 }}>
              <Button color="inherit" disabled={usersQuery.isFetchingNextPage} onClick={() => usersQuery.fetchNextPage()}>
                {usersQuery.isFetchingNextPage ? '正在加载…' : '加载更多用户'}
              </Button>
            </Box>
          ) : null}
        </>
      )}

      <Dialog open={editorOpen} onClose={() => { if (!saveUser.isPending) setEditorOpen(false) }} fullWidth maxWidth="md">
        <Box component="form" onSubmit={submit}>
          <DialogTitle>{selectedUser ? '编辑用户' : '创建用户'}</DialogTitle>
          <DialogContent>
            <DialogContentText>
              保存后，新角色、团队和权限立即生效。修改当前账号时需要重新登录。
            </DialogContentText>
            <Stack spacing={2} sx={{ mt: 2 }}>
              {saveUser.isError ? <OperatorErrorNotice title="保存用户失败" error={saveUser.error} fallback="请检查账号、密码和管理员保护规则" /> : null}
              {selectedUser?.id === currentUserId ? <Alert severity="warning" variant="outlined">正在修改当前登录账号；保存后需要重新登录。</Alert> : null}
              {selectedUserIsFinalActiveAdmin ? (
                <Alert severity="warning" variant="outlined">
                  这是当前组织最后一个启用的管理员。管理员角色和用户管理权限不能移除。
                </Alert>
              ) : null}
              <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' } }}>
                <TextField label="账号" required disabled={Boolean(selectedUser)} value={draft.username} onChange={(event) => setDraft((current) => ({ ...current, username: event.target.value }))} />
                {!selectedUser ? <TextField label="初始密码" required type="password" autoComplete="new-password" value={draft.password} onChange={(event) => setDraft((current) => ({ ...current, password: event.target.value }))} /> : null}
                <TextField label="姓名" required value={draft.displayName} onChange={(event) => setDraft((current) => ({ ...current, displayName: event.target.value }))} />
                <TextField label="邮箱" type="email" value={draft.email} onChange={(event) => setDraft((current) => ({ ...current, email: event.target.value }))} helperText={selectedUser ? '留空时保留当前邮箱' : '可选'} />
                <TextField select label="角色" required disabled={selectedUserIsFinalActiveAdmin} value={draft.role} onChange={(event) => changeRole(event.target.value)}>
                  {roles.map((role) => <MenuItem key={role.role} value={role.role}>{roleLabel(role.role)}</MenuItem>)}
                </TextField>
                <TextField select label="团队" value={draft.teamId} onChange={(event) => setDraft((current) => ({ ...current, teamId: event.target.value }))}>
                  <MenuItem value="">未分配</MenuItem>
                  {teams.filter((team) => team.is_active || team.id === selectedUser?.team_id).map((team) => (
                    <MenuItem key={team.id} value={String(team.id)}>{team.name}{team.is_active ? '' : '（已停用）'}</MenuItem>
                  ))}
                </TextField>
              </Box>

              <Paper variant="outlined" sx={{ p: 2 }}>
                <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} sx={{ alignItems: { xs: 'stretch', sm: 'center' }, justifyContent: 'space-between' }}>
                  <Box>
                    <Typography component="h3" variant="h3">权限</Typography>
                    <Typography variant="body2" color="text.secondary">已选择 {draft.capabilities.length} 项；角色默认 {selectedRoleDefaults.length} 项。</Typography>
                  </Box>
                  <Button color="inherit" startIcon={<RestartAltRoundedIcon />} onClick={restoreRoleDefaults}>恢复角色默认</Button>
                </Stack>
                <Divider sx={{ my: 2 }} />
                <Stack spacing={2} sx={{ maxHeight: 360, overflowY: 'auto', pr: 1 }}>
                  {capabilityGroups.map(([group, capabilities]) => (
                    <Box component="section" key={group} aria-label={group}>
                      <Typography variant="subtitle2">{group}</Typography>
                      <FormGroup sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' } }}>
                        {capabilities.map((capability) => (
                          <FormControlLabel
                            key={capability}
                            control={(
                              <Checkbox
                                checked={draft.capabilities.includes(capability)}
                                disabled={selectedUserIsFinalActiveAdmin && capability === 'user.manage'}
                                onChange={() => toggleCapability(capability)}
                              />
                            )}
                            label={<Typography variant="body2">{capabilityLabel(capability)}</Typography>}
                          />
                        ))}
                      </FormGroup>
                    </Box>
                  ))}
                </Stack>
                <OperatorTechnicalDisclosure title="权限代码" summary={`${draft.capabilities.length} 项`} compact>
                  <Typography component="pre" variant="caption" sx={{ m: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                    {draft.capabilities.join('\n') || '无'}
                  </Typography>
                </OperatorTechnicalDisclosure>
              </Paper>
            </Stack>
          </DialogContent>
          <DialogActions>
            <Button color="inherit" disabled={saveUser.isPending} onClick={() => setEditorOpen(false)}>取消</Button>
            <Button type="submit" variant="contained" disabled={saveUser.isPending || !draft.displayName.trim() || !draft.role || (!selectedUser && (!draft.username.trim() || !draft.password))} startIcon={saveUser.isPending ? <CircularProgress color="inherit" size={16} /> : undefined}>
              {saveUser.isPending ? '保存中…' : '保存用户'}
            </Button>
          </DialogActions>
        </Box>
      </Dialog>

      <Dialog open={Boolean(resetUser)} onClose={() => { if (!resetCredential.isPending) setResetUser(null) }} fullWidth maxWidth="sm">
        <DialogTitle>重置用户密码</DialogTitle>
        <DialogContent>
          <DialogContentText>
            为 {resetUser?.display_name} 设置新密码。完成后，该用户需要使用新密码重新登录。
          </DialogContentText>
          <Stack spacing={2} sx={{ mt: 2 }}>
            <Alert severity="info" variant="outlined">密码至少 12 位，并同时包含小写字母、大写字母、数字和特殊字符。</Alert>
            {resetCredential.isError ? <OperatorErrorNotice title="密码重置失败" error={resetCredential.error} fallback="请检查密码要求" /> : null}
            <TextField label="新密码" type="password" required autoComplete="new-password" value={resetPassword} onChange={(event) => setResetPassword(event.target.value)} />
            <TextField label="确认新密码" type="password" required autoComplete="new-password" value={resetConfirmation} onChange={(event) => setResetConfirmation(event.target.value)} error={Boolean(resetConfirmation && resetPassword !== resetConfirmation)} helperText={resetConfirmation && resetPassword !== resetConfirmation ? '两次密码不一致' : ' '} />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button color="inherit" disabled={resetCredential.isPending} onClick={() => setResetUser(null)}>取消</Button>
          <Button variant="contained" disabled={!resetReady || resetCredential.isPending} startIcon={resetCredential.isPending ? <CircularProgress color="inherit" size={16} /> : <KeyRoundedIcon />} onClick={() => resetCredential.mutate()}>
            {resetCredential.isPending ? '正在重置…' : '重置密码'}
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog open={Boolean(toggleUser)} onClose={() => { if (!toggleStatus.isPending) setToggleUser(null) }} maxWidth="xs" fullWidth>
        <DialogTitle>{toggleUser?.is_active ? '停用账号' : '启用账号'}</DialogTitle>
        <DialogContent>
          <DialogContentText>
            {toggleUser?.is_active
              ? `停用 ${toggleUser.display_name} 后，该账号将立即无法登录并退出所有设备。历史记录不会删除。`
              : `启用 ${toggleUser?.display_name} 后，该账号可以重新登录。`}
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button color="inherit" disabled={toggleStatus.isPending} onClick={() => setToggleUser(null)}>取消</Button>
          <Button color={toggleUser?.is_active ? 'warning' : 'success'} variant="contained" disabled={!toggleUser || toggleStatus.isPending} onClick={() => { if (toggleUser) toggleStatus.mutate(toggleUser) }}>
            {toggleStatus.isPending ? '处理中…' : toggleUser?.is_active ? '确认停用' : '确认启用'}
          </Button>
        </DialogActions>
      </Dialog>
    </Paper>
  )
}

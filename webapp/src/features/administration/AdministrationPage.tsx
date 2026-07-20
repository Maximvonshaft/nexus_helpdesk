import AddRoundedIcon from '@mui/icons-material/AddRounded'
import EditRoundedIcon from '@mui/icons-material/EditRounded'
import LockResetRoundedIcon from '@mui/icons-material/LockResetRounded'
import LogoutRoundedIcon from '@mui/icons-material/LogoutRounded'
import PowerSettingsNewRoundedIcon from '@mui/icons-material/PowerSettingsNewRounded'
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
  DialogTitle,
  Divider,
  FormControlLabel,
  MenuItem,
  Paper,
  Stack,
  Tab,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tabs,
  TextField,
  Typography,
} from '@mui/material'
import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  OperatorEmptyState,
  OperatorErrorNotice,
  OperatorFactGrid,
  OperatorLoadingState,
} from '@/app/OperatorPresentation'
import { useSession } from '@/hooks/useAuth'
import { formatDateTime, sanitizeDisplayText } from '@/lib/format'
import { identityApi } from '@/lib/identityApi'
import type { RoleProfile, UserRole, UserSecurityState } from '@/lib/identityTypes'
import type { AdminUser, SecurityAudit, Team } from '@/lib/types'

const ROLE_LABELS: Record<UserRole, string> = {
  admin: '系统管理员',
  manager: '运营经理',
  lead: '团队主管',
  agent: '客服专员',
  auditor: '审计员',
}

const ROLE_ORDER: UserRole[] = ['admin', 'manager', 'lead', 'agent', 'auditor']

type UserDraft = {
  username: string
  displayName: string
  email: string
  password: string
  role: UserRole
  teamId: string
  capabilities: string[]
}

const emptyDraft: UserDraft = {
  username: '',
  displayName: '',
  email: '',
  password: '',
  role: 'agent',
  teamId: '',
  capabilities: [],
}

function strongPassword(value: string) {
  return value.length >= 12
    && /[a-z]/.test(value)
    && /[A-Z]/.test(value)
    && /\d/.test(value)
    && /[^A-Za-z0-9]/.test(value)
}

function roleLabel(role: string) {
  return ROLE_LABELS[role as UserRole] || role
}

function teamLabel(teams: Team[], teamId?: number | null) {
  return teams.find((team) => team.id === teamId)?.name || '未分配'
}

function capabilityGroups(catalog: string[]) {
  const groups = new Map<string, string[]>()
  for (const capability of catalog) {
    const separator = capability.includes(':') ? ':' : '.'
    const group = capability.split(separator, 1)[0] || 'other'
    groups.set(group, [...(groups.get(group) ?? []), capability])
  }
  return [...groups.entries()].sort(([left], [right]) => left.localeCompare(right))
}

function UserEditorDialog({
  open,
  user,
  draft,
  teams,
  roles,
  catalog,
  busy,
  error,
  onDraftChange,
  onClose,
  onSubmit,
}: {
  open: boolean
  user: AdminUser | null
  draft: UserDraft
  teams: Team[]
  roles: RoleProfile[]
  catalog: string[]
  busy: boolean
  error: unknown
  onDraftChange: (next: UserDraft) => void
  onClose: () => void
  onSubmit: () => void
}) {
  const groupedCapabilities = useMemo(() => capabilityGroups(catalog), [catalog])
  const ready = Boolean(
    draft.username.trim()
    && draft.displayName.trim()
    && (user || strongPassword(draft.password)),
  )

  const selectRole = (role: UserRole) => {
    const profile = roles.find((item) => item.role === role)
    onDraftChange({ ...draft, role, capabilities: profile?.capabilities ?? [] })
  }

  const toggleCapability = (capability: string) => {
    const next = new Set(draft.capabilities)
    if (next.has(capability)) next.delete(capability)
    else next.add(capability)
    onDraftChange({ ...draft, capabilities: [...next].sort() })
  }

  return (
    <Dialog open={open} onClose={busy ? undefined : onClose} fullWidth maxWidth="lg">
      <DialogTitle>{user ? `编辑账号 · ${user.display_name}` : '创建账号'}</DialogTitle>
      <DialogContent dividers>
        <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', md: 'minmax(0, 0.75fr) minmax(0, 1.25fr)' } }}>
          <Stack spacing={1.5}>
            <TextField
              label="登录账号"
              required
              disabled={Boolean(user)}
              value={draft.username}
              onChange={(event) => onDraftChange({ ...draft, username: event.target.value })}
            />
            <TextField
              label="显示名称"
              required
              value={draft.displayName}
              onChange={(event) => onDraftChange({ ...draft, displayName: event.target.value })}
            />
            <TextField
              label="邮箱"
              type="email"
              value={draft.email}
              onChange={(event) => onDraftChange({ ...draft, email: event.target.value })}
            />
            {!user ? (
              <TextField
                label="初始密码"
                required
                type="password"
                autoComplete="new-password"
                value={draft.password}
                error={Boolean(draft.password) && !strongPassword(draft.password)}
                helperText="至少 12 位，包含大小写字母、数字和特殊字符；首次登录必须修改"
                onChange={(event) => onDraftChange({ ...draft, password: event.target.value })}
              />
            ) : null}
            <TextField
              select
              label="角色"
              value={draft.role}
              onChange={(event) => selectRole(event.target.value as UserRole)}
            >
              {ROLE_ORDER.map((role) => <MenuItem key={role} value={role}>{roleLabel(role)}</MenuItem>)}
            </TextField>
            <TextField
              select
              label="团队"
              value={draft.teamId}
              onChange={(event) => onDraftChange({ ...draft, teamId: event.target.value })}
            >
              <MenuItem value="">未分配</MenuItem>
              {teams.map((team) => <MenuItem key={team.id} value={String(team.id)}>{team.name}</MenuItem>)}
            </TextField>
            <Alert severity="info" variant="outlined">
              角色提供标准权限模板；右侧调整会保存为该账号的显式权限覆盖。
            </Alert>
          </Stack>

          <Paper variant="outlined" sx={{ p: 1.5, maxHeight: 560, overflow: 'auto' }}>
            <Stack direction="row" spacing={1} sx={{ alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
              <Typography component="h3" variant="h3">有效权限</Typography>
              <Chip label={`${draft.capabilities.length} 项`} />
            </Stack>
            <Divider />
            {groupedCapabilities.map(([group, capabilities]) => (
              <Box key={group} sx={{ py: 1.5 }}>
                <Typography variant="overline" color="text.secondary">{group}</Typography>
                <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', lg: '1fr 1fr' } }}>
                  {capabilities.map((capability) => (
                    <FormControlLabel
                      key={capability}
                      control={<Checkbox checked={draft.capabilities.includes(capability)} onChange={() => toggleCapability(capability)} />}
                      label={<Typography variant="body2" component="code">{capability}</Typography>}
                    />
                  ))}
                </Box>
              </Box>
            ))}
          </Paper>
        </Box>
        {error ? <Box sx={{ mt: 2 }}><OperatorErrorNotice title="账号保存失败" error={error} fallback="请检查账号、邮箱和权限配置" /></Box> : null}
      </DialogContent>
      <DialogActions>
        <Button color="inherit" disabled={busy} onClick={onClose}>取消</Button>
        <Button variant="contained" disabled={!ready || busy} onClick={onSubmit}>
          {busy ? '正在保存…' : user ? '保存账号' : '创建账号'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

function PasswordResetDialog({
  user,
  password,
  busy,
  error,
  onPasswordChange,
  onClose,
  onSubmit,
}: {
  user: AdminUser | null
  password: string
  busy: boolean
  error: unknown
  onPasswordChange: (value: string) => void
  onClose: () => void
  onSubmit: () => void
}) {
  return (
    <Dialog open={Boolean(user)} onClose={busy ? undefined : onClose} fullWidth maxWidth="sm">
      <DialogTitle>重置密码 · {user?.display_name}</DialogTitle>
      <DialogContent dividers>
        <Stack spacing={2}>
          <Alert severity="warning" variant="outlined">
            重置后该账号的所有现有会话立即失效，并在下次登录时强制修改密码。
          </Alert>
          <TextField
            label="管理员签发的新密码"
            type="password"
            autoComplete="new-password"
            required
            value={password}
            error={Boolean(password) && !strongPassword(password)}
            helperText="至少 12 位，包含大小写字母、数字和特殊字符"
            onChange={(event) => onPasswordChange(event.target.value)}
          />
          {error ? <OperatorErrorNotice title="密码重置失败" error={error} fallback="请检查密码强度" /> : null}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button color="inherit" disabled={busy} onClick={onClose}>取消</Button>
        <Button color="warning" variant="contained" disabled={!strongPassword(password) || busy} onClick={onSubmit}>
          {busy ? '正在重置…' : '确认重置'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

function UsersPanel({
  users,
  teams,
  roles,
  catalog,
  states,
  currentUserId,
  loading,
  error,
  onRefresh,
}: {
  users: AdminUser[]
  teams: Team[]
  roles: RoleProfile[]
  catalog: string[]
  states: UserSecurityState[]
  currentUserId?: number
  loading: boolean
  error: unknown
  onRefresh: () => Promise<void>
}) {
  const [editorOpen, setEditorOpen] = useState(false)
  const [editingUser, setEditingUser] = useState<AdminUser | null>(null)
  const [draft, setDraft] = useState<UserDraft>(emptyDraft)
  const [resetUser, setResetUser] = useState<AdminUser | null>(null)
  const [resetPassword, setResetPassword] = useState('')
  const stateByUser = useMemo(() => new Map(states.map((state) => [state.user_id, state])), [states])

  const saveUser = useMutation({
    mutationFn: () => {
      const teamId = draft.teamId ? Number(draft.teamId) : null
      if (editingUser) {
        return identityApi.updateUser(editingUser.id, {
          display_name: draft.displayName.trim(),
          email: draft.email.trim() || null,
          role: draft.role,
          team_id: teamId,
          capabilities: draft.capabilities,
        })
      }
      return identityApi.createUser({
        username: draft.username.trim(),
        display_name: draft.displayName.trim(),
        email: draft.email.trim() || null,
        password: draft.password,
        role: draft.role,
        team_id: teamId,
        capabilities: draft.capabilities,
      })
    },
    onSuccess: async () => {
      setEditorOpen(false)
      setEditingUser(null)
      setDraft(emptyDraft)
      await onRefresh()
    },
  })

  const statusMutation = useMutation({
    mutationFn: ({ user, activate }: { user: AdminUser; activate: boolean }) => activate
      ? identityApi.activateUser(user.id)
      : identityApi.deactivateUser(user.id),
    onSuccess: onRefresh,
  })

  const resetMutation = useMutation({
    mutationFn: () => {
      if (!resetUser) throw new Error('未选择账号')
      return identityApi.resetPassword(resetUser.id, resetPassword)
    },
    onSuccess: async () => {
      setResetUser(null)
      setResetPassword('')
      await onRefresh()
    },
  })

  const sessionMutation = useMutation({
    mutationFn: ({ userId, requireChange }: { userId: number; requireChange: boolean }) => requireChange
      ? identityApi.requirePasswordChange(userId)
      : identityApi.logoutUserEverywhere(userId),
    onSuccess: onRefresh,
  })

  const openCreate = () => {
    const profile = roles.find((item) => item.role === 'agent')
    setEditingUser(null)
    setDraft({ ...emptyDraft, capabilities: profile?.capabilities ?? [] })
    saveUser.reset()
    setEditorOpen(true)
  }

  const openEdit = (user: AdminUser) => {
    setEditingUser(user)
    setDraft({
      username: user.username,
      displayName: user.display_name,
      email: user.email || '',
      password: '',
      role: user.role as UserRole,
      teamId: user.team_id ? String(user.team_id) : '',
      capabilities: [...user.capabilities],
    })
    saveUser.reset()
    setEditorOpen(true)
  }

  const actionError = statusMutation.error || sessionMutation.error

  return (
    <Stack spacing={2}>
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} sx={{ alignItems: { sm: 'center' }, justifyContent: 'space-between' }}>
        <Box>
          <Typography component="h2" variant="h2">用户与访问</Typography>
          <Typography variant="body2" color="text.secondary">创建账号、分配角色与团队、调整权限，并控制密码和会话生命周期。</Typography>
        </Box>
        <Button variant="contained" startIcon={<AddRoundedIcon />} onClick={openCreate}>创建账号</Button>
      </Stack>

      {actionError ? <OperatorErrorNotice title="账号操作失败" error={actionError} fallback="请稍后重试" /> : null}
      {error ? <OperatorErrorNotice title="无法读取账号治理数据" error={error} fallback="请稍后重试" /> : null}
      {loading ? <OperatorLoadingState label="正在读取账号…" minHeight={260} /> : users.length ? (
        <Paper variant="outlined" sx={{ overflow: 'hidden' }}>
          <TableContainer>
            <Table size="small" aria-label="用户与访问列表">
              <TableHead>
                <TableRow>
                  <TableCell>账号</TableCell>
                  <TableCell>角色 / 团队</TableCell>
                  <TableCell>权限</TableCell>
                  <TableCell>密码与会话</TableCell>
                  <TableCell>状态</TableCell>
                  <TableCell align="right">操作</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {users.map((user) => {
                  const security = stateByUser.get(user.id)
                  const isSelf = user.id === currentUserId
                  return (
                    <TableRow key={user.id} hover>
                      <TableCell>
                        <Typography variant="subtitle2">{sanitizeDisplayText(user.display_name)}</Typography>
                        <Typography variant="caption" color="text.secondary">{sanitizeDisplayText(user.username)}{user.email ? ` · ${sanitizeDisplayText(user.email)}` : ''}</Typography>
                      </TableCell>
                      <TableCell>
                        <Typography variant="body2">{roleLabel(user.role)}</Typography>
                        <Typography variant="caption" color="text.secondary">{teamLabel(teams, user.team_id)}</Typography>
                      </TableCell>
                      <TableCell>{user.capabilities.length}</TableCell>
                      <TableCell>
                        <Stack spacing={0.5} sx={{ alignItems: 'flex-start' }}>
                          <Chip size="small" color={security?.must_change_password ? 'warning' : 'success'} label={security?.must_change_password ? '必须修改密码' : '密码有效'} />
                          <Typography variant="caption" color="text.secondary">
                            上次登录：{security?.last_login_at ? formatDateTime(security.last_login_at) : '暂无'}
                          </Typography>
                        </Stack>
                      </TableCell>
                      <TableCell><Chip size="small" color={user.is_active ? 'success' : 'default'} label={user.is_active ? '启用' : '停用'} /></TableCell>
                      <TableCell align="right">
                        <Stack direction="row" spacing={0.5} useFlexGap sx={{ justifyContent: 'flex-end', flexWrap: 'wrap' }}>
                          <Button size="small" color="inherit" startIcon={<EditRoundedIcon />} onClick={() => openEdit(user)}>编辑</Button>
                          <Button size="small" color="inherit" startIcon={<LockResetRoundedIcon />} onClick={() => { setResetUser(user); setResetPassword(''); resetMutation.reset() }}>重置密码</Button>
                          <Button size="small" color="inherit" disabled={isSelf || sessionMutation.isPending} onClick={() => sessionMutation.mutate({ userId: user.id, requireChange: true })}>强制改密</Button>
                          <Button size="small" color="inherit" startIcon={<LogoutRoundedIcon />} disabled={isSelf || sessionMutation.isPending} onClick={() => sessionMutation.mutate({ userId: user.id, requireChange: false })}>强制下线</Button>
                          <Button
                            size="small"
                            color={user.is_active ? 'warning' : 'success'}
                            startIcon={<PowerSettingsNewRoundedIcon />}
                            disabled={isSelf || statusMutation.isPending}
                            onClick={() => statusMutation.mutate({ user, activate: !user.is_active })}
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
        </Paper>
      ) : <OperatorEmptyState title="暂无账号" description="创建第一个运营账号" />}

      <UserEditorDialog
        open={editorOpen}
        user={editingUser}
        draft={draft}
        teams={teams}
        roles={roles}
        catalog={catalog}
        busy={saveUser.isPending}
        error={saveUser.error}
        onDraftChange={setDraft}
        onClose={() => { setEditorOpen(false); setEditingUser(null); saveUser.reset() }}
        onSubmit={() => saveUser.mutate()}
      />
      <PasswordResetDialog
        user={resetUser}
        password={resetPassword}
        busy={resetMutation.isPending}
        error={resetMutation.error}
        onPasswordChange={(value) => { setResetPassword(value); resetMutation.reset() }}
        onClose={() => { setResetUser(null); setResetPassword(''); resetMutation.reset() }}
        onSubmit={() => resetMutation.mutate()}
      />
    </Stack>
  )
}

function SecurityPanel({ audit, loading, error, readOnly }: { audit?: SecurityAudit; loading: boolean; error: unknown; readOnly: boolean }) {
  const riskyUsers = audit?.users.filter((user) => user.high_risk_count > 0 || user.override_count > 0) ?? []
  return (
    <Stack spacing={2}>
      <Box>
        <Typography component="h2" variant="h2">安全与审计</Typography>
        <Typography variant="body2" color="text.secondary">查看高风险权限覆盖和管理员操作记录。所有密码、令牌与凭证字段在服务端脱敏。</Typography>
      </Box>
      {readOnly ? <Alert severity="info" variant="outlined">当前账号为只读审计访问，不能修改用户或权限。</Alert> : null}
      {error ? <OperatorErrorNotice title="无法读取安全审计" error={error} fallback="请稍后重试" /> : null}
      {loading ? <OperatorLoadingState label="正在读取安全审计…" minHeight={260} /> : audit ? (
        <>
          <Paper variant="outlined" sx={{ p: 2 }}>
            <OperatorFactGrid columns={4} facts={[
              ['账号总数', audit.summary.total_users],
              ['活动账号', audit.summary.active_users],
              ['停用账号', audit.summary.inactive_users],
              ['管理员', audit.summary.admin_users],
              ['审计员', audit.summary.auditor_users],
              ['高风险覆盖', audit.summary.high_risk_overrides],
              ['24 小时管理操作', audit.summary.recent_audit_24h],
              ['权限目录', audit.summary.catalog_size],
            ]} />
          </Paper>

          <Paper component="section" variant="outlined" sx={{ p: 2 }}>
            <Typography component="h3" variant="h3">权限复核</Typography>
            <Divider sx={{ my: 2 }} />
            {riskyUsers.length ? (
              <TableContainer>
                <Table size="small" aria-label="权限复核列表">
                  <TableHead><TableRow><TableCell>账号</TableCell><TableCell>角色</TableCell><TableCell>显式覆盖</TableCell><TableCell>高风险权限</TableCell><TableCell>状态</TableCell></TableRow></TableHead>
                  <TableBody>{riskyUsers.map((user) => (
                    <TableRow key={user.user_id} hover>
                      <TableCell>{sanitizeDisplayText(user.display_name || user.username)}</TableCell>
                      <TableCell>{roleLabel(user.role)}</TableCell>
                      <TableCell>{user.override_count}</TableCell>
                      <TableCell>{user.high_risk_count}</TableCell>
                      <TableCell>{user.is_active ? '启用' : '停用'}</TableCell>
                    </TableRow>
                  ))}</TableBody>
                </Table>
              </TableContainer>
            ) : <OperatorEmptyState title="没有需要复核的权限覆盖" description="当前权限配置符合角色模板" />}
          </Paper>

          <Paper component="section" variant="outlined" sx={{ p: 2 }}>
            <Typography component="h3" variant="h3">最近管理操作</Typography>
            <Divider sx={{ my: 2 }} />
            {audit.recent_audit.length ? (
              <TableContainer>
                <Table size="small" aria-label="最近管理操作">
                  <TableHead><TableRow><TableCell>时间</TableCell><TableCell>操作人</TableCell><TableCell>动作</TableCell><TableCell>对象</TableCell></TableRow></TableHead>
                  <TableBody>{audit.recent_audit.map((item) => (
                    <TableRow key={item.id} hover>
                      <TableCell>{formatDateTime(item.created_at)}</TableCell>
                      <TableCell>{sanitizeDisplayText(item.actor_display_name || item.actor_username || '系统')}</TableCell>
                      <TableCell><Box component="code">{sanitizeDisplayText(item.action)}</Box></TableCell>
                      <TableCell>{sanitizeDisplayText(item.target_type)}{item.target_id ? ` #${item.target_id}` : ''}</TableCell>
                    </TableRow>
                  ))}</TableBody>
                </Table>
              </TableContainer>
            ) : <OperatorEmptyState title="暂无管理操作" description="暂无审计记录" />}
          </Paper>
        </>
      ) : null}
    </Stack>
  )
}

export function AdministrationPage() {
  const queryClient = useQueryClient()
  const session = useSession()
  const [tab, setTab] = useState(0)
  const capabilities = useMemo(() => new Set(session.data?.capabilities ?? []), [session.data?.capabilities])
  const canManageUsers = capabilities.has('user.manage')
  const canReadAudit = canManageUsers || capabilities.has('security.read') || capabilities.has('audit.read')

  const users = useQuery({ queryKey: ['identityUsers'], queryFn: identityApi.users, enabled: canManageUsers, retry: false })
  const teams = useQuery({ queryKey: ['identityTeams'], queryFn: identityApi.teams, enabled: canManageUsers, retry: false })
  const roles = useQuery({ queryKey: ['identityRoles'], queryFn: identityApi.roles, enabled: canManageUsers, retry: false })
  const catalog = useQuery({ queryKey: ['identityCapabilityCatalog'], queryFn: identityApi.capabilityCatalog, enabled: canManageUsers, retry: false })
  const states = useQuery({ queryKey: ['identitySecurityStates'], queryFn: identityApi.securityStates, enabled: canManageUsers, retry: false })
  const audit = useQuery({ queryKey: ['identitySecurityAudit'], queryFn: identityApi.securityAudit, enabled: canReadAudit, retry: false })

  const refreshIdentity = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['identityUsers'] }),
      queryClient.invalidateQueries({ queryKey: ['identitySecurityStates'] }),
      queryClient.invalidateQueries({ queryKey: ['identitySecurityAudit'] }),
      queryClient.invalidateQueries({ queryKey: ['session'] }),
    ])
  }

  const userError = users.error || teams.error || roles.error || catalog.error || states.error
  const userLoading = users.isLoading || teams.isLoading || roles.isLoading || catalog.isLoading || states.isLoading

  return (
    <Box component="main" sx={{ p: { xs: 1.5, md: 2.5 } }}>
      <Stack spacing={2.5}>
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} sx={{ alignItems: { sm: 'flex-start' }, justifyContent: 'space-between' }}>
          <Box>
            <Typography component="h1" variant="h1">管理控制台</Typography>
            <Typography variant="body2" color="text.secondary">唯一的账号、权限和安全审计控制面。</Typography>
          </Box>
          {(users.isFetching || states.isFetching || audit.isFetching) ? <CircularProgress size={22} aria-label="正在刷新" /> : null}
        </Stack>

        <Paper variant="outlined" sx={{ px: 1 }}>
          <Tabs value={tab} onChange={(_, value: number) => setTab(value)} aria-label="管理控制台模块">
            {canManageUsers ? <Tab label="用户与访问" /> : null}
            {canReadAudit ? <Tab label="安全与审计" /> : null}
          </Tabs>
        </Paper>

        {!canManageUsers && !canReadAudit ? <Alert severity="warning">当前账号无权访问管理控制台。</Alert> : null}
        {canManageUsers && tab === 0 ? (
          <UsersPanel
            users={users.data ?? []}
            teams={teams.data ?? []}
            roles={roles.data ?? []}
            catalog={catalog.data ?? []}
            states={states.data ?? []}
            currentUserId={session.data?.id}
            loading={userLoading}
            error={userError}
            onRefresh={refreshIdentity}
          />
        ) : null}
        {canReadAudit && ((!canManageUsers && tab === 0) || (canManageUsers && tab === 1)) ? (
          <SecurityPanel audit={audit.data} loading={audit.isLoading} error={audit.error} readOnly={!canManageUsers} />
        ) : null}
      </Stack>
    </Box>
  )
}

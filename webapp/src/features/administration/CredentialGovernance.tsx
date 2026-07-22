import KeyRoundedIcon from '@mui/icons-material/KeyRounded'
import LogoutRoundedIcon from '@mui/icons-material/LogoutRounded'
import SecurityRoundedIcon from '@mui/icons-material/SecurityRounded'
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Divider,
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
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import {
  OperatorEmptyState,
  OperatorErrorNotice,
  OperatorLoadingState,
} from '@/app/OperatorPresentation'
import { formatDateTime, sanitizeDisplayText } from '@/lib/format'
import { supportApi } from '@/lib/supportApi'
import type { CredentialPolicy } from '@/lib/types'

type CredentialAction = {
  kind: 'require-password-change' | 'revoke-sessions' | 'reset-mfa'
  policy: CredentialPolicy
}

function roleLabel(role: string) {
  if (role === 'admin') return '管理员'
  if (role === 'manager') return '运营经理'
  if (role === 'lead') return '组长'
  if (role === 'agent') return '客服专员'
  if (role === 'auditor') return '审计员'
  return sanitizeDisplayText(role)
}

function actionTitle(action: CredentialAction | null) {
  if (action?.kind === 'require-password-change') return '要求修改密码'
  if (action?.kind === 'revoke-sessions') return '退出所有设备'
  if (action?.kind === 'reset-mfa') return '重置两步验证'
  return '账号安全操作'
}

function actionButtonLabel(action: CredentialAction | null) {
  if (action?.kind === 'require-password-change') return '确认要求修改'
  if (action?.kind === 'revoke-sessions') return '确认退出所有设备'
  if (action?.kind === 'reset-mfa') return '确认重置'
  return '确认'
}

export function CredentialGovernance({ currentUserId }: { currentUserId: number }) {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const [action, setAction] = useState<CredentialAction | null>(null)

  const policies = useQuery({
    queryKey: ['credentialPolicies'],
    queryFn: supportApi.credentialPolicies,
    retry: false,
  })

  const normalizedSearch = search.trim().toLowerCase()
  const visiblePolicies = useMemo(
    () => (policies.data ?? []).filter((policy) => !normalizedSearch || [
      policy.username,
      policy.display_name,
      policy.role,
    ].some((value) => String(value || '').toLowerCase().includes(normalizedSearch))),
    [normalizedSearch, policies.data],
  )

  const mutatePolicy = useMutation({
    mutationFn: async (selected: CredentialAction) => {
      if (selected.kind === 'require-password-change') {
        return supportApi.requireAdminUserPasswordChange(selected.policy.user_id)
      }
      if (selected.kind === 'reset-mfa') {
        return supportApi.resetAdminUserMfa(selected.policy.user_id)
      }
      return supportApi.revokeAdminUserSessions(selected.policy.user_id)
    },
    onSuccess: async () => {
      setAction(null)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['credentialPolicies'] }),
        queryClient.invalidateQueries({ queryKey: ['securityAudit'] }),
        queryClient.invalidateQueries({ queryKey: ['adminUsers'] }),
      ])
    },
  })

  const forcedCount = (policies.data ?? []).filter((policy) => policy.must_change_password).length
  const twoStepCount = (policies.data ?? []).filter((policy) => policy.mfa_enabled).length
  const neverLoggedInCount = (policies.data ?? []).filter((policy) => !policy.last_login_at).length

  return (
    <Paper component="section" variant="outlined" aria-labelledby="credential-governance-title" sx={{ p: 2 }}>
      <Stack direction={{ xs: 'column', md: 'row' }} spacing={2} sx={{ alignItems: { xs: 'stretch', md: 'center' }, justifyContent: 'space-between' }}>
        <Box>
          <Typography id="credential-governance-title" component="h2" variant="h2">密码与登录</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            查看密码状态、两步验证和最近登录，并处理账号安全问题。
          </Typography>
        </Box>
        <TextField
          size="small"
          label="搜索账号"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
      </Stack>

      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} sx={{ mt: 2 }}>
        <Chip label={`待修改密码 ${forcedCount}`} color={forcedCount ? 'warning' : 'default'} />
        <Chip label={`已启用两步验证 ${twoStepCount}`} color={twoStepCount ? 'success' : 'default'} />
        <Chip label={`从未登录 ${neverLoggedInCount}`} />
        <Chip label={`账号总数 ${policies.data?.length ?? 0}`} />
      </Stack>
      <Divider sx={{ my: 2 }} />

      {policies.isError ? <OperatorErrorNotice title="无法读取账号安全状态" error={policies.error} fallback="请稍后重试" /> : null}
      {mutatePolicy.isError ? (
        <Box sx={{ mb: 2 }}>
          <OperatorErrorNotice title="操作失败" error={mutatePolicy.error} fallback="请检查账号状态后重试" />
        </Box>
      ) : null}

      {policies.isLoading ? <OperatorLoadingState label="正在加载账号安全状态…" minHeight={220} /> : !visiblePolicies.length ? (
        <OperatorEmptyState title="没有匹配的账号" description={search ? '请调整搜索条件。' : '尚无账号记录。'} />
      ) : (
        <TableContainer>
          <Table size="small" aria-label="密码与登录列表">
            <TableHead>
              <TableRow>
                <TableCell>账号</TableCell>
                <TableCell>角色与状态</TableCell>
                <TableCell>密码</TableCell>
                <TableCell>两步验证</TableCell>
                <TableCell>上次登录</TableCell>
                <TableCell>密码更新</TableCell>
                <TableCell align="right">操作</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {visiblePolicies.map((policy) => {
                const isSelf = policy.user_id === currentUserId
                return (
                  <TableRow key={policy.user_id} hover>
                    <TableCell>
                      <Typography variant="subtitle2">{sanitizeDisplayText(policy.display_name)}</Typography>
                      <Typography variant="caption" color="text.secondary">{sanitizeDisplayText(policy.username)}</Typography>
                    </TableCell>
                    <TableCell>
                      <Stack direction="row" spacing={0.75} useFlexGap sx={{ flexWrap: 'wrap' }}>
                        <Chip size="small" label={roleLabel(policy.role)} />
                        <Chip size="small" color={policy.is_active ? 'success' : 'default'} label={policy.is_active ? '启用' : '停用'} />
                      </Stack>
                    </TableCell>
                    <TableCell>
                      <Chip
                        size="small"
                        color={policy.must_change_password ? 'warning' : 'success'}
                        label={policy.must_change_password ? '等待修改' : '正常'}
                      />
                    </TableCell>
                    <TableCell>
                      <Stack spacing={0.5} sx={{ alignItems: 'flex-start' }}>
                        <Chip size="small" color={policy.mfa_enabled ? 'success' : 'default'} label={policy.mfa_enabled ? '已启用' : '未启用'} />
                        {policy.mfa_enabled ? <Typography variant="caption" color="text.secondary">剩余恢复码 {policy.mfa_recovery_codes_remaining}</Typography> : null}
                      </Stack>
                    </TableCell>
                    <TableCell>{policy.last_login_at ? formatDateTime(policy.last_login_at) : '从未登录'}</TableCell>
                    <TableCell>{policy.password_changed_at ? formatDateTime(policy.password_changed_at) : '暂无'}</TableCell>
                    <TableCell align="right">
                      <Stack direction="row" spacing={0.5} useFlexGap sx={{ justifyContent: 'flex-end', flexWrap: 'wrap' }}>
                        <Button
                          size="small"
                          color="warning"
                          startIcon={<KeyRoundedIcon />}
                          disabled={isSelf || mutatePolicy.isPending}
                          onClick={() => setAction({ kind: 'require-password-change', policy })}
                        >
                          要求改密
                        </Button>
                        <Button
                          size="small"
                          color="inherit"
                          startIcon={<LogoutRoundedIcon />}
                          disabled={isSelf || mutatePolicy.isPending}
                          onClick={() => setAction({ kind: 'revoke-sessions', policy })}
                        >
                          退出设备
                        </Button>
                        {policy.mfa_enabled ? (
                          <Button
                            size="small"
                            color="error"
                            startIcon={<SecurityRoundedIcon />}
                            disabled={isSelf || mutatePolicy.isPending}
                            onClick={() => setAction({ kind: 'reset-mfa', policy })}
                          >
                            重置两步验证
                          </Button>
                        ) : null}
                      </Stack>
                      {isSelf ? <Typography variant="caption" color="text.secondary">当前账号请在账户设置中操作</Typography> : null}
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      <Dialog open={Boolean(action)} onClose={() => { if (!mutatePolicy.isPending) setAction(null) }} maxWidth="sm" fullWidth>
        <DialogTitle>{actionTitle(action)}</DialogTitle>
        <DialogContent>
          <DialogContentText>
            {action?.kind === 'require-password-change'
              ? `${action.policy.display_name} 下次登录后必须先修改密码，当前登录状态将全部失效。`
              : action?.kind === 'reset-mfa'
                ? `${action.policy.display_name} 现有的两步验证和恢复码将被删除，当前登录状态也会全部失效。`
                : `${action?.policy.display_name ?? '该账号'} 将从所有设备退出，密码不会改变。`}
          </DialogContentText>
          {action?.kind === 'require-password-change' && action.policy.must_change_password ? (
            <Alert severity="info" variant="outlined" sx={{ mt: 2 }}>该账号已经处于等待修改密码状态。</Alert>
          ) : null}
          {action?.kind === 'reset-mfa' ? (
            <Alert severity="warning" variant="outlined" sx={{ mt: 2 }}>重置后无法恢复旧的验证器配置和恢复码。</Alert>
          ) : null}
        </DialogContent>
        <DialogActions>
          <Button color="inherit" disabled={mutatePolicy.isPending} onClick={() => setAction(null)}>取消</Button>
          <Button
            color={action?.kind === 'require-password-change' ? 'warning' : 'error'}
            variant="contained"
            disabled={!action || mutatePolicy.isPending}
            startIcon={mutatePolicy.isPending ? <CircularProgress color="inherit" size={16} /> : undefined}
            onClick={() => { if (action) mutatePolicy.mutate(action) }}
          >
            {mutatePolicy.isPending ? '处理中…' : actionButtonLabel(action)}
          </Button>
        </DialogActions>
      </Dialog>
    </Paper>
  )
}

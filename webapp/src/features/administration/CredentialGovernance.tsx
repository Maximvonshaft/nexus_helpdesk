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

function actionTitle(action: CredentialAction | null) {
  if (action?.kind === 'require-password-change') return '要求修改密码'
  if (action?.kind === 'revoke-sessions') return '撤销全部会话'
  if (action?.kind === 'reset-mfa') return '重置两步验证'
  return '凭据操作'
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
  const mfaCount = (policies.data ?? []).filter((policy) => policy.mfa_enabled).length
  const neverLoggedInCount = (policies.data ?? []).filter((policy) => !policy.last_login_at).length

  return (
    <Paper component="section" variant="outlined" aria-labelledby="credential-governance-title" sx={{ p: 2 }}>
      <Stack direction={{ xs: 'column', md: 'row' }} spacing={2} sx={{ alignItems: { xs: 'stretch', md: 'center' }, justifyContent: 'space-between' }}>
        <Box>
          <Typography id="credential-governance-title" component="h2" variant="h2">凭据与会话</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            查看密码轮换、两步验证与最近登录，并通过同一身份版本撤销 HTTP 和实时工作会话。
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
        <Chip label={`已启用 MFA ${mfaCount}`} color={mfaCount ? 'success' : 'default'} />
        <Chip label={`从未登录 ${neverLoggedInCount}`} />
        <Chip label={`账号总数 ${policies.data?.length ?? 0}`} />
      </Stack>
      <Divider sx={{ my: 2 }} />

      {policies.isError ? <OperatorErrorNotice title="无法读取凭据策略" error={policies.error} fallback="请稍后重试" /> : null}
      {mutatePolicy.isError ? (
        <Box sx={{ mb: 2 }}>
          <OperatorErrorNotice title="凭据操作失败" error={mutatePolicy.error} fallback="请检查账号状态后重试" />
        </Box>
      ) : null}

      {policies.isLoading ? <OperatorLoadingState label="正在加载凭据策略…" minHeight={220} /> : !visiblePolicies.length ? (
        <OperatorEmptyState title="没有匹配的账号" description={search ? '请调整搜索条件。' : '尚无账号凭据记录。'} />
      ) : (
        <TableContainer>
          <Table size="small" aria-label="凭据与会话治理列表">
            <TableHead>
              <TableRow>
                <TableCell>账号</TableCell>
                <TableCell>身份</TableCell>
                <TableCell>密码状态</TableCell>
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
                        <Chip size="small" label={policy.role} />
                        <Chip size="small" color={policy.is_active ? 'success' : 'default'} label={policy.is_active ? '启用' : '停用'} />
                      </Stack>
                    </TableCell>
                    <TableCell>
                      <Chip
                        size="small"
                        color={policy.must_change_password ? 'warning' : 'success'}
                        label={policy.must_change_password ? '必须修改' : '正常'}
                      />
                    </TableCell>
                    <TableCell>
                      <Stack spacing={0.5} sx={{ alignItems: 'flex-start' }}>
                        <Chip size="small" color={policy.mfa_enabled ? 'success' : 'default'} label={policy.mfa_enabled ? '已启用' : '未启用'} />
                        {policy.mfa_enabled ? <Typography variant="caption" color="text.secondary">恢复码 {policy.mfa_recovery_codes_remaining}</Typography> : null}
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
                          强制改密
                        </Button>
                        <Button
                          size="small"
                          color="inherit"
                          startIcon={<LogoutRoundedIcon />}
                          disabled={isSelf || mutatePolicy.isPending}
                          onClick={() => setAction({ kind: 'revoke-sessions', policy })}
                        >
                          撤销会话
                        </Button>
                        {policy.mfa_enabled ? (
                          <Button
                            size="small"
                            color="error"
                            startIcon={<SecurityRoundedIcon />}
                            disabled={isSelf || mutatePolicy.isPending}
                            onClick={() => setAction({ kind: 'reset-mfa', policy })}
                          >
                            重置 MFA
                          </Button>
                        ) : null}
                      </Stack>
                      {isSelf ? <Typography variant="caption" color="text.secondary">当前账号请在账户设置操作</Typography> : null}
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
              ? `${action.policy.display_name} 的现有会话将立即失效；下次登录后只能进入账户设置，完成密码修改后才能恢复业务访问。`
              : action?.kind === 'reset-mfa'
                ? `${action.policy.display_name} 的验证器密钥和全部恢复码将被删除，现有会话也会立即失效。用户下次登录只需密码，可在账户设置重新启用 MFA。`
                : `${action?.policy.display_name ?? '该账号'} 在所有设备和实时工作连接中的现有访问将立即失效；密码不会改变。`}
          </DialogContentText>
          {action?.kind === 'require-password-change' && action.policy.must_change_password ? (
            <Alert severity="info" variant="outlined" sx={{ mt: 2 }}>该账号当前已经处于必须修改密码状态；确认后仍会再次撤销现有会话。</Alert>
          ) : null}
          {action?.kind === 'reset-mfa' ? (
            <Alert severity="warning" variant="outlined" sx={{ mt: 2 }}>MFA 重置不可撤销，且不会显示或恢复旧密钥与恢复码。</Alert>
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
            {mutatePolicy.isPending ? '处理中…' : '确认执行'}
          </Button>
        </DialogActions>
      </Dialog>
    </Paper>
  )
}

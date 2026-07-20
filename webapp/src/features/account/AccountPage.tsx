import LockResetRoundedIcon from '@mui/icons-material/LockResetRounded'
import LogoutRoundedIcon from '@mui/icons-material/LogoutRounded'
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Divider,
  Paper,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import {
  OperatorErrorNotice,
  OperatorFactGrid,
  OperatorLoadingState,
} from '@/app/OperatorPresentation'
import { useLogout, useSession } from '@/hooks/useAuth'
import { formatDateTime } from '@/lib/format'
import { identityApi } from '@/lib/identityApi'

function hasStrongPasswordShape(value: string) {
  return value.length >= 12
    && /[a-z]/.test(value)
    && /[A-Z]/.test(value)
    && /\d/.test(value)
    && /[^A-Za-z0-9]/.test(value)
}

export function AccountPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const logout = useLogout()
  const session = useSession()
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')

  const security = useQuery({
    queryKey: ['accountSecurity'],
    queryFn: identityApi.accountSecurity,
    enabled: Boolean(session.data),
    retry: false,
  })

  const changePassword = useMutation({
    mutationFn: () => identityApi.changePassword(currentPassword, newPassword),
    onSuccess: async (response) => {
      setCurrentPassword('')
      setNewPassword('')
      setConfirmPassword('')
      queryClient.setQueryData(['session'], response.user)
      await queryClient.invalidateQueries({ queryKey: ['accountSecurity'] })
    },
  })

  const logoutEverywhere = useMutation({
    mutationFn: identityApi.logoutAll,
    onSuccess: () => {
      logout()
      navigate({ to: '/login', replace: true })
    },
  })

  const passwordsMatch = Boolean(newPassword) && newPassword === confirmPassword
  const passwordReady = Boolean(currentPassword) && hasStrongPasswordShape(newPassword) && passwordsMatch

  return (
    <Box component="main" sx={{ p: { xs: 1.5, md: 2.5 } }}>
      <Stack spacing={2.5}>
        <Typography component="h1" variant="h1">账号与安全</Typography>

        {session.data?.must_change_password ? (
          <Alert severity="warning" variant="outlined">
            该账号使用的是管理员签发或重置的密码。完成密码修改后才能继续安全使用。
          </Alert>
        ) : null}

        <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', xl: 'minmax(0, 0.8fr) minmax(0, 1.2fr)' } }}>
          <Paper component="section" variant="outlined" sx={{ p: 2, alignSelf: 'start' }}>
            <Typography component="h2" variant="h3">当前账号</Typography>
            <Divider sx={{ my: 2 }} />
            {session.isLoading ? <OperatorLoadingState label="正在读取账号…" minHeight={160} /> : session.data ? (
              <Stack spacing={2}>
                <OperatorFactGrid facts={[
                  ['姓名', session.data.display_name || '未设置'],
                  ['账号', session.data.username],
                  ['邮箱', session.data.email || '未设置'],
                  ['角色', session.data.role],
                  ['上次登录', security.data?.last_login_at ? formatDateTime(security.data.last_login_at) : '暂无'],
                  ['密码更新', security.data?.password_changed_at ? formatDateTime(security.data.password_changed_at) : '暂无'],
                ]} />
                {security.isError ? <OperatorErrorNotice title="无法读取安全状态" error={security.error} fallback="请稍后重试" /> : null}
              </Stack>
            ) : null}
          </Paper>

          <Paper component="section" variant="outlined" sx={{ p: 2 }}>
            <Typography component="h2" variant="h3">修改密码</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.75 }}>
              密码至少 12 位，并同时包含大小写字母、数字和特殊字符。修改后所有旧会话立即失效。
            </Typography>
            <Divider sx={{ my: 2 }} />
            <Stack spacing={1.5} component="form" onSubmit={(event) => { event.preventDefault(); if (passwordReady) changePassword.mutate() }}>
              <TextField
                label="当前密码"
                type="password"
                autoComplete="current-password"
                required
                value={currentPassword}
                onChange={(event) => { setCurrentPassword(event.target.value); changePassword.reset() }}
              />
              <TextField
                label="新密码"
                type="password"
                autoComplete="new-password"
                required
                value={newPassword}
                error={Boolean(newPassword) && !hasStrongPasswordShape(newPassword)}
                helperText={newPassword && !hasStrongPasswordShape(newPassword) ? '密码强度不符合要求' : '不要重复使用其他系统的密码'}
                onChange={(event) => { setNewPassword(event.target.value); changePassword.reset() }}
              />
              <TextField
                label="确认新密码"
                type="password"
                autoComplete="new-password"
                required
                value={confirmPassword}
                error={Boolean(confirmPassword) && !passwordsMatch}
                helperText={confirmPassword && !passwordsMatch ? '两次输入不一致' : ' '}
                onChange={(event) => { setConfirmPassword(event.target.value); changePassword.reset() }}
              />
              {changePassword.isError ? <OperatorErrorNotice title="密码修改失败" error={changePassword.error} fallback="请检查当前密码和新密码强度" /> : null}
              {changePassword.isSuccess ? <Alert severity="success" variant="outlined">密码已修改，旧会话已撤销。</Alert> : null}
              <Button
                type="submit"
                variant="contained"
                startIcon={changePassword.isPending ? <CircularProgress color="inherit" size={16} /> : <LockResetRoundedIcon />}
                disabled={!passwordReady || changePassword.isPending}
              >
                {changePassword.isPending ? '正在更新…' : '更新密码'}
              </Button>
            </Stack>
          </Paper>
        </Box>

        <Paper component="section" variant="outlined" sx={{ p: 2 }}>
          <Typography component="h2" variant="h3">会话控制</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.75 }}>
            撤销该账号在所有设备上的访问令牌。操作完成后当前设备也会退出。
          </Typography>
          <Divider sx={{ my: 2 }} />
          {logoutEverywhere.isError ? <OperatorErrorNotice title="无法撤销会话" error={logoutEverywhere.error} fallback="请稍后重试" /> : null}
          <Button
            color="error"
            variant="outlined"
            startIcon={logoutEverywhere.isPending ? <CircularProgress color="inherit" size={16} /> : <LogoutRoundedIcon />}
            disabled={logoutEverywhere.isPending}
            onClick={() => logoutEverywhere.mutate()}
          >
            退出所有设备
          </Button>
        </Paper>
      </Stack>
    </Box>
  )
}

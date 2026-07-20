import LockResetRoundedIcon from '@mui/icons-material/LockResetRounded'
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
import { useMutation } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { type FormEvent, useEffect, useState } from 'react'
import {
  OperatorErrorNotice,
  OperatorFactGrid,
  OperatorLoadingState,
} from '@/app/OperatorPresentation'
import { useLogout, useSession } from '@/hooks/useAuth'
import { supportApi } from '@/lib/supportApi'

export function AccountPage() {
  const navigate = useNavigate()
  const logout = useLogout()
  const session = useSession()
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [validationError, setValidationError] = useState('')

  useEffect(() => { document.title = '账户设置 · Nexus OSR' }, [])

  const changePassword = useMutation({
    mutationFn: () => supportApi.changePassword(currentPassword, newPassword),
    onSuccess: () => {
      logout()
      navigate({ to: '/login', replace: true })
    },
  })

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setValidationError('')
    if (newPassword !== confirmPassword) {
      setValidationError('两次输入的新密码不一致。')
      return
    }
    if (currentPassword === newPassword) {
      setValidationError('新密码必须与当前密码不同。')
      return
    }
    changePassword.mutate()
  }

  if (session.isLoading || !session.data) {
    return (
      <Box component="main" sx={{ p: { xs: 1.5, md: 2.5 } }}>
        {session.isError
          ? <OperatorErrorNotice title="无法读取账户" error={session.error} fallback="请重新登录" />
          : <OperatorLoadingState label="正在加载账户…" minHeight={240} />}
      </Box>
    )
  }

  return (
    <Box component="main" sx={{ p: { xs: 1.5, md: 2.5 } }}>
      <Typography component="h1" variant="h1">账户设置</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mt: 0.75 }}>
        管理当前登录身份和凭据。修改密码后，所有旧会话会立即失效。
      </Typography>

      <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', lg: 'minmax(280px, 0.8fr) minmax(0, 1.2fr)' }, mt: 2.5 }}>
        <Paper component="section" variant="outlined" aria-labelledby="account-identity-title" sx={{ p: 2, alignSelf: 'start' }}>
          <Typography id="account-identity-title" component="h2" variant="h3">当前身份</Typography>
          <Divider sx={{ my: 2 }} />
          <OperatorFactGrid facts={[
            ['姓名', session.data.display_name || '未设置'],
            ['账号', session.data.username],
            ['邮箱', session.data.email || '未设置'],
            ['角色', session.data.role],
            ['团队编号', session.data.team_id ?? '未分配'],
          ]} />
        </Paper>

        <Paper component="section" variant="outlined" aria-labelledby="account-password-title" sx={{ p: 2 }}>
          <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
            <LockResetRoundedIcon color="primary" aria-hidden="true" />
            <Typography id="account-password-title" component="h2" variant="h3">修改密码</Typography>
          </Stack>
          <Divider sx={{ my: 2 }} />
          <Box component="form" onSubmit={submit} noValidate>
            <Stack spacing={2}>
              <Alert severity="info" variant="outlined">
                密码至少 12 位，并同时包含小写字母、大写字母、数字和特殊字符；不能使用常见弱密码或连续字符。
              </Alert>
              {validationError ? <Alert severity="warning" variant="outlined">{validationError}</Alert> : null}
              {changePassword.isError ? (
                <OperatorErrorNotice title="密码修改失败" error={changePassword.error} fallback="请检查当前密码和新密码规则" />
              ) : null}
              <TextField
                label="当前密码"
                type="password"
                required
                autoComplete="current-password"
                value={currentPassword}
                onChange={(event) => setCurrentPassword(event.target.value)}
              />
              <TextField
                label="新密码"
                type="password"
                required
                autoComplete="new-password"
                value={newPassword}
                onChange={(event) => setNewPassword(event.target.value)}
              />
              <TextField
                label="确认新密码"
                type="password"
                required
                autoComplete="new-password"
                value={confirmPassword}
                onChange={(event) => setConfirmPassword(event.target.value)}
              />
              <Button
                type="submit"
                variant="contained"
                disabled={!currentPassword || !newPassword || !confirmPassword || changePassword.isPending}
                startIcon={changePassword.isPending ? <CircularProgress color="inherit" size={16} /> : <LockResetRoundedIcon />}
              >
                {changePassword.isPending ? '正在更新…' : '更新密码并重新登录'}
              </Button>
            </Stack>
          </Box>
        </Paper>
      </Box>
    </Box>
  )
}

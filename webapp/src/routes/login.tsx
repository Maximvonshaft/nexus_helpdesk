import ArrowBackRoundedIcon from '@mui/icons-material/ArrowBackRounded'
import SecurityRoundedIcon from '@mui/icons-material/SecurityRounded'
import VisibilityOffRoundedIcon from '@mui/icons-material/VisibilityOffRounded'
import VisibilityRoundedIcon from '@mui/icons-material/VisibilityRounded'
import {
  Alert,
  Box,
  Button,
  Container,
  IconButton,
  InputAdornment,
  Paper,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { createRoute, useNavigate } from '@tanstack/react-router'
import { Route as RootRoute } from './root'
import {
  isMfaLoginChallenge,
  useLogin,
  useMfaLoginVerification,
  useSession,
} from '@/hooks/useAuth'
import type { MfaLoginChallenge } from '@/lib/types'

function LoginPage() {
  const navigate = useNavigate()
  const session = useSession()
  const login = useLogin()
  const mfaVerification = useMfaLoginVerification()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [mfaChallenge, setMfaChallenge] = useState<MfaLoginChallenge | null>(null)
  const [mfaCredential, setMfaCredential] = useState('')
  const errorRef = useRef<HTMLDivElement>(null)

  useEffect(() => { document.title = '登录 · Nexus OSR' }, [])

  useEffect(() => {
    if (session.data && !login.isPending && !login.isSuccess && !mfaVerification.isPending) {
      navigate({ to: '/', replace: true })
    }
  }, [login.isPending, login.isSuccess, mfaVerification.isPending, navigate, session.data])

  useEffect(() => {
    if (login.error || mfaVerification.error) errorRef.current?.focus()
  }, [login.error, mfaVerification.error])

  const clearErrors = () => {
    if (login.error) login.reset()
    if (mfaVerification.error) mfaVerification.reset()
  }

  const handlePasswordSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const normalizedUsername = username.trim()
    if (login.isPending || !normalizedUsername || !password) return

    try {
      const result = await login.mutateAsync({ username: normalizedUsername, password })
      if (isMfaLoginChallenge(result)) {
        setMfaChallenge(result)
        setMfaCredential('')
        setPassword('')
        return
      }
      navigate({ to: '/', replace: true })
    } catch {
      // React Query retains failure state; the bounded alert below receives focus.
    }
  }

  const handleMfaSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!mfaChallenge || mfaVerification.isPending || !mfaCredential.trim()) return
    try {
      await mfaVerification.mutateAsync({
        challengeToken: mfaChallenge.challenge_token,
        credential: mfaCredential.trim(),
      })
      navigate({ to: '/', replace: true })
    } catch {
      // React Query retains failure state; the bounded alert below receives focus.
    }
  }

  const restartPasswordLogin = () => {
    setMfaChallenge(null)
    setMfaCredential('')
    setPassword('')
    clearErrors()
  }

  return (
    <Box
      component="main"
      sx={{
        alignItems: 'center',
        bgcolor: 'background.default',
        display: 'flex',
        minHeight: '100dvh',
        py: { xs: 4, md: 8 },
      }}
    >
      <Container maxWidth="sm">
        <Stack spacing={3}>
          <Stack spacing={1} sx={{ alignItems: 'center', textAlign: 'center' }}>
            <Box
              aria-hidden="true"
              sx={{
                alignItems: 'center',
                bgcolor: 'primary.main',
                borderRadius: 2,
                color: 'primary.contrastText',
                display: 'flex',
                fontSize: 20,
                fontWeight: 800,
                height: 52,
                justifyContent: 'center',
                width: 52,
              }}
            >
              N
            </Box>
            <Typography translate="no" variant="h2">Nexus OSR</Typography>
          </Stack>

          {mfaChallenge ? (
            <Paper component="form" onSubmit={handleMfaSubmit} variant="outlined" sx={{ borderRadius: 2, p: { xs: 2.5, sm: 4 } }}>
              <Stack spacing={2.5}>
                <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
                  <SecurityRoundedIcon color="primary" />
                  <Typography component="h1" variant="h2">两步验证</Typography>
                </Stack>
                <Alert severity="info" variant="outlined">
                  {mfaChallenge.display_name} 的密码已验证。请输入验证器生成的 6 位验证码，或输入一个未使用的恢复码。
                </Alert>
                <TextField
                  label="验证码或恢复码"
                  name="mfa-credential"
                  value={mfaCredential}
                  onChange={(event) => {
                    setMfaCredential(event.target.value)
                    clearErrors()
                  }}
                  autoComplete="one-time-code"
                  inputMode="text"
                  required
                  autoFocus
                />
                {mfaVerification.error ? (
                  <Alert ref={errorRef} severity="error" role="alert" tabIndex={-1}>
                    验证码、恢复码或登录挑战无效。请重试或重新输入密码。
                  </Alert>
                ) : null}
                <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
                  <Button color="inherit" startIcon={<ArrowBackRoundedIcon />} disabled={mfaVerification.isPending} onClick={restartPasswordLogin}>
                    重新输入密码
                  </Button>
                  <Button
                    variant="contained"
                    size="large"
                    type="submit"
                    disabled={mfaVerification.isPending || !mfaCredential.trim()}
                    sx={{ flex: 1 }}
                  >
                    {mfaVerification.isPending ? '正在验证…' : '验证并登录'}
                  </Button>
                </Stack>
              </Stack>
            </Paper>
          ) : (
            <Paper component="form" onSubmit={handlePasswordSubmit} variant="outlined" sx={{ borderRadius: 2, p: { xs: 2.5, sm: 4 } }}>
              <Stack spacing={2.5}>
                <Typography component="h1" variant="h2">登录</Typography>

                <TextField
                  label="账号"
                  name="username"
                  value={username}
                  onChange={(event) => {
                    setUsername(event.target.value)
                    clearErrors()
                  }}
                  autoComplete="username"
                  spellCheck={false}
                  required
                  autoFocus
                />

                <TextField
                  id="login-password"
                  label="密码"
                  name="password"
                  value={password}
                  onChange={(event) => {
                    setPassword(event.target.value)
                    clearErrors()
                  }}
                  type={showPassword ? 'text' : 'password'}
                  autoComplete="current-password"
                  required
                  slotProps={{
                    input: {
                      endAdornment: (
                        <InputAdornment position="end">
                          <IconButton
                            aria-label={showPassword ? '隐藏密码' : '显示密码'}
                            aria-controls="login-password"
                            aria-pressed={showPassword}
                            edge="end"
                            onClick={() => setShowPassword((current) => !current)}
                          >
                            {showPassword ? <VisibilityOffRoundedIcon /> : <VisibilityRoundedIcon />}
                          </IconButton>
                        </InputAdornment>
                      ),
                    },
                  }}
                />

                {login.error ? (
                  <Alert ref={errorRef} severity="error" role="alert" tabIndex={-1}>
                    账号或密码错误。
                  </Alert>
                ) : null}

                <Typography variant="body2" color="text.secondary">
                  请勿在共享设备保存密码。
                </Typography>

                <Button
                  variant="contained"
                  size="large"
                  type="submit"
                  disabled={login.isPending || !username.trim() || !password}
                >
                  {login.isPending ? '正在登录…' : '登录'}
                </Button>
              </Stack>
            </Paper>
          )}
        </Stack>
      </Container>
    </Box>
  )
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/login',
  component: LoginPage,
})

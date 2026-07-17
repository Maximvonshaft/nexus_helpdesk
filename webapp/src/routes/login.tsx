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
import { useLogin, useSession } from '@/hooks/useAuth'

function LoginPage() {
  const navigate = useNavigate()
  const session = useSession()
  const login = useLogin()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const errorRef = useRef<HTMLDivElement>(null)

  useEffect(() => { document.title = '登录 · Nexus OSR' }, [])

  useEffect(() => {
    if (session.data && !login.isPending && !login.isSuccess) {
      navigate({ to: '/', replace: true })
    }
  }, [login.isPending, login.isSuccess, navigate, session.data])

  useEffect(() => {
    if (login.error) errorRef.current?.focus()
  }, [login.error])

  const clearLoginError = () => {
    if (login.error) login.reset()
  }

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const normalizedUsername = username.trim()
    if (login.isPending || !normalizedUsername || !password) return

    try {
      await login.mutateAsync({ username: normalizedUsername, password })
      navigate({ to: '/', replace: true })
    } catch {
      // React Query retains failure state; the bounded alert below receives focus.
    }
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
          <Stack spacing={1} alignItems="center" textAlign="center">
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

          <Paper component="form" onSubmit={handleSubmit} variant="outlined" sx={{ borderRadius: 2, p: { xs: 2.5, sm: 4 } }}>
            <Stack spacing={2.5}>
              <Typography component="h1" variant="h2">登录</Typography>

              <TextField
                label="账号"
                name="username"
                value={username}
                onChange={(event) => {
                  setUsername(event.target.value)
                  clearLoginError()
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
                  clearLoginError()
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

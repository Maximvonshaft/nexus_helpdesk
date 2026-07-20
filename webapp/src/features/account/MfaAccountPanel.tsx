import ContentCopyRoundedIcon from '@mui/icons-material/ContentCopyRounded'
import KeyRoundedIcon from '@mui/icons-material/KeyRounded'
import SecurityRoundedIcon from '@mui/icons-material/SecurityRounded'
import ShieldOutlinedIcon from '@mui/icons-material/ShieldOutlined'
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
  TextField,
  Typography,
} from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { type FormEvent, useState } from 'react'
import { OperatorErrorNotice, OperatorFactGrid, OperatorLoadingState } from '@/app/OperatorPresentation'
import { useLogout } from '@/hooks/useAuth'
import { formatDateTime } from '@/lib/format'
import { supportApi } from '@/lib/supportApi'
import type { MfaRecoveryCodes, MfaSetupBegin } from '@/lib/types'

type SensitiveAction = 'regenerate' | 'disable' | null
type SensitiveResult = MfaRecoveryCodes | { ok: boolean; reauthenticate: boolean }

export function MfaAccountPanel() {
  const navigate = useNavigate()
  const logout = useLogout()
  const queryClient = useQueryClient()
  const [currentPassword, setCurrentPassword] = useState('')
  const [setup, setSetup] = useState<MfaSetupBegin | null>(null)
  const [setupCode, setSetupCode] = useState('')
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([])
  const [sensitiveAction, setSensitiveAction] = useState<SensitiveAction>(null)
  const [sensitivePassword, setSensitivePassword] = useState('')
  const [sensitiveCredential, setSensitiveCredential] = useState('')

  const status = useQuery({
    queryKey: ['mfaStatus'],
    queryFn: supportApi.mfaStatus,
    retry: false,
  })

  const restartLogin = () => {
    setRecoveryCodes([])
    logout()
    navigate({ to: '/login', replace: true })
  }

  const beginSetup = useMutation({
    mutationFn: () => supportApi.beginMfaSetup(currentPassword),
    onSuccess: (result) => {
      setSetup(result)
      setSetupCode('')
      setCurrentPassword('')
    },
  })

  const cancelSetup = useMutation({
    mutationFn: supportApi.cancelMfaSetup,
    onSuccess: async () => {
      setSetup(null)
      setSetupCode('')
      await queryClient.invalidateQueries({ queryKey: ['mfaStatus'] })
    },
  })

  const confirmSetup = useMutation({
    mutationFn: () => supportApi.confirmMfaSetup(setupCode.trim()),
    onSuccess: (result) => {
      setSetup(null)
      setSetupCode('')
      setRecoveryCodes(result.recovery_codes)
    },
  })

  const sensitiveMutation = useMutation<SensitiveResult, Error, void>({
    mutationFn: async () => {
      if (!sensitiveAction) throw new Error('未选择 MFA 操作')
      if (sensitiveAction === 'regenerate') {
        return supportApi.regenerateMfaRecoveryCodes(sensitivePassword, sensitiveCredential.trim())
      }
      return supportApi.disableMfa(sensitivePassword, sensitiveCredential.trim())
    },
    onSuccess: (result) => {
      const wasRegenerate = sensitiveAction === 'regenerate'
      setSensitiveAction(null)
      setSensitivePassword('')
      setSensitiveCredential('')
      if (wasRegenerate && 'recovery_codes' in result) {
        setRecoveryCodes(result.recovery_codes)
      } else {
        restartLogin()
      }
    },
  })

  const copyRecoveryCodes = async () => {
    if (!navigator.clipboard || !recoveryCodes.length) return
    await navigator.clipboard.writeText(recoveryCodes.join('\n'))
  }

  if (status.isLoading) return <OperatorLoadingState label="正在读取两步验证状态…" minHeight={180} />
  if (status.isError) return <OperatorErrorNotice title="无法读取两步验证状态" error={status.error} fallback="请稍后重试" />
  if (!status.data) return null

  return (
    <Paper component="section" variant="outlined" aria-labelledby="account-mfa-title" sx={{ p: 2, mt: 2 }}>
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} sx={{ alignItems: { xs: 'stretch', sm: 'center' }, justifyContent: 'space-between' }}>
        <Box>
          <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
            <SecurityRoundedIcon color="primary" />
            <Typography id="account-mfa-title" component="h2" variant="h3">两步验证</Typography>
          </Stack>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.75 }}>
            使用兼容 TOTP 的验证器应用。登录时可使用 6 位验证码或一个未使用的恢复码。
          </Typography>
        </Box>
        <Chip color={status.data.enabled ? 'success' : 'default'} label={status.data.enabled ? '已启用' : '未启用'} />
      </Stack>
      <Divider sx={{ my: 2 }} />

      <OperatorFactGrid columns={3} facts={[
        ['状态', status.data.enabled ? '已启用' : status.data.setup_pending ? '配置未完成' : '未启用'],
        ['最近验证', status.data.last_verified_at ? formatDateTime(status.data.last_verified_at) : '暂无'],
        ['剩余恢复码', status.data.enabled ? status.data.recovery_codes_remaining : 0],
      ]} />

      {!status.data.enabled ? (
        <Stack spacing={2} sx={{ mt: 2 }}>
          <Alert severity="info" variant="outlined">
            启用后会撤销当前账号的所有旧会话。验证器密钥和恢复码不会在以后再次显示。
          </Alert>
          {beginSetup.isError ? <OperatorErrorNotice title="无法开始两步验证配置" error={beginSetup.error} fallback="请检查当前密码" /> : null}
          {!setup ? (
            <Box component="form" onSubmit={(event: FormEvent<HTMLFormElement>) => { event.preventDefault(); if (currentPassword) beginSetup.mutate() }}>
              <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
                <TextField
                  label="当前密码"
                  type="password"
                  autoComplete="current-password"
                  required
                  value={currentPassword}
                  onChange={(event) => setCurrentPassword(event.target.value)}
                  sx={{ flex: 1 }}
                />
                <Button
                  type="submit"
                  variant="contained"
                  startIcon={beginSetup.isPending ? <CircularProgress color="inherit" size={16} /> : <ShieldOutlinedIcon />}
                  disabled={!currentPassword || beginSetup.isPending}
                >
                  {status.data.setup_pending ? '重新生成配置' : '开始启用'}
                </Button>
              </Stack>
            </Box>
          ) : (
            <Paper variant="outlined" sx={{ p: 2 }}>
              <Stack spacing={2}>
                <Alert severity="warning" variant="outlined">
                  将下方密钥添加到验证器。不要发送给任何人，也不要保存到工单、聊天或审计备注。
                </Alert>
                <Box>
                  <Typography variant="caption" color="text.secondary">手动输入密钥</Typography>
                  <Typography component="code" sx={{ display: 'block', mt: 0.5, overflowWrap: 'anywhere', fontWeight: 700 }}>{setup.secret}</Typography>
                </Box>
                <Box>
                  <Typography variant="caption" color="text.secondary">验证器链接</Typography>
                  <Typography component="code" variant="caption" sx={{ display: 'block', mt: 0.5, overflowWrap: 'anywhere' }}>{setup.otpauth_uri}</Typography>
                </Box>
                {confirmSetup.isError ? <OperatorErrorNotice title="验证码确认失败" error={confirmSetup.error} fallback="请使用验证器当前显示的 6 位验证码" /> : null}
                <TextField
                  label="6 位验证码"
                  value={setupCode}
                  onChange={(event) => setSetupCode(event.target.value)}
                  autoComplete="one-time-code"
                  inputMode="numeric"
                  required
                />
                <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
                  <Button color="inherit" disabled={cancelSetup.isPending || confirmSetup.isPending} onClick={() => cancelSetup.mutate()}>取消配置</Button>
                  <Button
                    variant="contained"
                    disabled={!setupCode.trim() || confirmSetup.isPending}
                    startIcon={confirmSetup.isPending ? <CircularProgress color="inherit" size={16} /> : <SecurityRoundedIcon />}
                    onClick={() => confirmSetup.mutate()}
                  >
                    确认并启用
                  </Button>
                </Stack>
              </Stack>
            </Paper>
          )}
        </Stack>
      ) : (
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} sx={{ mt: 2 }}>
          <Button variant="outlined" startIcon={<KeyRoundedIcon />} onClick={() => { sensitiveMutation.reset(); setSensitiveAction('regenerate') }}>
            重新生成恢复码
          </Button>
          <Button color="error" variant="outlined" onClick={() => { sensitiveMutation.reset(); setSensitiveAction('disable') }}>
            停用两步验证
          </Button>
        </Stack>
      )}

      <Dialog open={Boolean(recoveryCodes.length)} maxWidth="sm" fullWidth>
        <DialogTitle>保存恢复码</DialogTitle>
        <DialogContent>
          <DialogContentText>
            每个恢复码只能使用一次。请立即保存到安全的密码管理器；关闭后系统不会再次显示这些明文恢复码。
          </DialogContentText>
          <Alert severity="warning" variant="outlined" sx={{ mt: 2 }}>
            保存完成后需要重新登录。不要截图到共享相册，也不要发送到客服或工作群。
          </Alert>
          <Box component="pre" sx={{ mt: 2, p: 2, bgcolor: 'action.hover', borderRadius: 1, fontSize: 16, lineHeight: 1.8, whiteSpace: 'pre-wrap' }}>
            {recoveryCodes.join('\n')}
          </Box>
        </DialogContent>
        <DialogActions>
          <Button startIcon={<ContentCopyRoundedIcon />} onClick={copyRecoveryCodes}>复制恢复码</Button>
          <Button variant="contained" onClick={restartLogin}>已安全保存，重新登录</Button>
        </DialogActions>
      </Dialog>

      <Dialog open={Boolean(sensitiveAction)} onClose={() => { if (!sensitiveMutation.isPending) setSensitiveAction(null) }} maxWidth="sm" fullWidth>
        <DialogTitle>{sensitiveAction === 'regenerate' ? '重新生成恢复码' : '停用两步验证'}</DialogTitle>
        <DialogContent>
          <DialogContentText>
            请输入当前密码和验证器验证码，或一个未使用的恢复码。成功后现有会话会全部失效。
          </DialogContentText>
          <Stack spacing={2} sx={{ mt: 2 }}>
            {sensitiveMutation.isError ? <OperatorErrorNotice title="两步验证操作失败" error={sensitiveMutation.error} fallback="请检查密码和验证码或恢复码" /> : null}
            <TextField
              label="当前密码"
              type="password"
              autoComplete="current-password"
              required
              value={sensitivePassword}
              onChange={(event) => setSensitivePassword(event.target.value)}
            />
            <TextField
              label="验证码或恢复码"
              autoComplete="one-time-code"
              required
              value={sensitiveCredential}
              onChange={(event) => setSensitiveCredential(event.target.value)}
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button color="inherit" disabled={sensitiveMutation.isPending} onClick={() => setSensitiveAction(null)}>取消</Button>
          <Button
            color={sensitiveAction === 'disable' ? 'error' : 'primary'}
            variant="contained"
            disabled={!sensitivePassword || !sensitiveCredential.trim() || sensitiveMutation.isPending}
            startIcon={sensitiveMutation.isPending ? <CircularProgress color="inherit" size={16} /> : undefined}
            onClick={() => sensitiveMutation.mutate()}
          >
            {sensitiveMutation.isPending ? '处理中…' : sensitiveAction === 'regenerate' ? '生成新恢复码' : '确认停用'}
          </Button>
        </DialogActions>
      </Dialog>
    </Paper>
  )
}

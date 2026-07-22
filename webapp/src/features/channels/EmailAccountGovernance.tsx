import AddRoundedIcon from '@mui/icons-material/AddRounded'
import EditRoundedIcon from '@mui/icons-material/EditRounded'
import SendRoundedIcon from '@mui/icons-material/SendRounded'
import ToggleOffRoundedIcon from '@mui/icons-material/ToggleOffRounded'
import ToggleOnRoundedIcon from '@mui/icons-material/ToggleOnRounded'
import {
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
  FormControlLabel,
  MenuItem,
  Paper,
  Stack,
  Switch,
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
import { type FormEvent, useMemo, useState } from 'react'
import {
  OperatorEmptyState,
  OperatorErrorNotice,
  OperatorLoadingState,
  OperatorTechnicalDisclosure,
  operatorToneColor,
} from '@/app/OperatorPresentation'
import { formatDateTime, sanitizeDisplayText } from '@/lib/format'
import { supportApi } from '@/lib/supportApi'
import { healthPresentation } from '@/lib/supportStatus'
import type {
  OutboundEmailAccount,
  OutboundEmailAccountCreate,
  OutboundEmailAccountUpdate,
  OutboundEmailSecurityMode,
} from '@/lib/types'

type EmailDraft = {
  displayName: string
  host: string
  port: string
  username: string
  password: string
  fromAddress: string
  replyTo: string
  securityMode: OutboundEmailSecurityMode
  inboundEnabled: boolean
  imapHost: string
  imapPort: string
  imapUsername: string
  imapPassword: string
  imapSecurityMode: OutboundEmailSecurityMode
  imapMailbox: string
  marketId: string
  priority: string
  isActive: boolean
}

const emptyDraft: EmailDraft = {
  displayName: '',
  host: '',
  port: '587',
  username: '',
  password: '',
  fromAddress: '',
  replyTo: '',
  securityMode: 'starttls',
  inboundEnabled: false,
  imapHost: '',
  imapPort: '993',
  imapUsername: '',
  imapPassword: '',
  imapSecurityMode: 'ssl',
  imapMailbox: 'INBOX',
  marketId: '',
  priority: '100',
  isActive: true,
}

function accountDraft(account: OutboundEmailAccount): EmailDraft {
  return {
    displayName: account.display_name || '',
    host: account.host,
    port: String(account.port),
    username: account.username,
    password: '',
    fromAddress: account.from_address,
    replyTo: account.reply_to || '',
    securityMode: account.security_mode as OutboundEmailSecurityMode,
    inboundEnabled: account.inbound_enabled,
    imapHost: account.imap_host || '',
    imapPort: account.imap_port ? String(account.imap_port) : '993',
    imapUsername: account.imap_username || '',
    imapPassword: '',
    imapSecurityMode: (account.imap_security_mode || 'ssl') as OutboundEmailSecurityMode,
    imapMailbox: account.imap_mailbox || 'INBOX',
    marketId: account.market_id ? String(account.market_id) : '',
    priority: String(account.priority),
    isActive: account.is_active,
  }
}

export function EmailAccountGovernance() {
  const queryClient = useQueryClient()
  const [editorOpen, setEditorOpen] = useState(false)
  const [selected, setSelected] = useState<OutboundEmailAccount | null>(null)
  const [draft, setDraft] = useState<EmailDraft>(emptyDraft)
  const [toggleAccount, setToggleAccount] = useState<OutboundEmailAccount | null>(null)
  const [testAccount, setTestAccount] = useState<OutboundEmailAccount | null>(null)
  const [testAddress, setTestAddress] = useState('')

  const accounts = useQuery({
    queryKey: ['outboundEmailAccounts'],
    queryFn: supportApi.outboundEmailAccounts,
    refetchInterval: 30_000,
    retry: false,
  })
  const markets = useQuery({
    queryKey: ['identityMarkets'],
    queryFn: supportApi.identityMarkets,
    retry: false,
  })

  const sortedAccounts = useMemo(
    () => [...(accounts.data ?? [])].sort((left, right) => Number(right.is_active) - Number(left.is_active) || left.priority - right.priority || left.id - right.id),
    [accounts.data],
  )

  const invalidate = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['outboundEmailAccounts'] }),
      queryClient.invalidateQueries({ queryKey: ['canonicalChannelAccounts'] }),
      queryClient.invalidateQueries({ queryKey: ['securityAudit'] }),
    ])
  }

  const saveAccount = useMutation({
    mutationFn: async () => {
      const common = {
        display_name: draft.displayName.trim() || null,
        host: draft.host.trim(),
        port: Number(draft.port),
        username: draft.username.trim(),
        from_address: draft.fromAddress.trim(),
        reply_to: draft.replyTo.trim() || null,
        security_mode: draft.securityMode,
        inbound_enabled: draft.inboundEnabled,
        imap_host: draft.inboundEnabled ? draft.imapHost.trim() || null : null,
        imap_port: draft.inboundEnabled && draft.imapPort ? Number(draft.imapPort) : null,
        imap_username: draft.inboundEnabled ? draft.imapUsername.trim() || null : null,
        imap_security_mode: draft.inboundEnabled ? draft.imapSecurityMode : null,
        imap_mailbox: draft.inboundEnabled ? draft.imapMailbox.trim() || 'INBOX' : null,
        market_id: draft.marketId ? Number(draft.marketId) : null,
        priority: Number(draft.priority),
        is_active: draft.isActive,
      }
      if (selected) {
        const payload: OutboundEmailAccountUpdate = {
          ...common,
          ...(draft.password ? { password: draft.password } : {}),
          ...(draft.inboundEnabled && draft.imapPassword ? { imap_password: draft.imapPassword } : {}),
        }
        return supportApi.updateOutboundEmailAccount(selected.id, payload)
      }
      const payload: OutboundEmailAccountCreate = {
        ...common,
        password: draft.password,
        ...(draft.inboundEnabled ? { imap_password: draft.imapPassword } : {}),
      }
      return supportApi.createOutboundEmailAccount(payload)
    },
    onSuccess: async () => {
      setEditorOpen(false)
      setSelected(null)
      setDraft(emptyDraft)
      await invalidate()
    },
  })

  const toggleActive = useMutation({
    mutationFn: (account: OutboundEmailAccount) => account.is_active
      ? supportApi.disableOutboundEmailAccount(account.id)
      : supportApi.enableOutboundEmailAccount(account.id),
    onSuccess: async () => {
      setToggleAccount(null)
      await invalidate()
    },
  })

  const testSend = useMutation({
    mutationFn: () => {
      if (!testAccount) throw new Error('未选择邮件账号')
      return supportApi.testOutboundEmailAccount(testAccount.id, {
        to_address: testAddress.trim(),
        subject: 'Nexus OSR 邮件渠道测试',
        body: '此邮件用于验证 Nexus OSR 邮件发送配置。',
      })
    },
    onSuccess: async () => {
      setTestAccount(null)
      setTestAddress('')
      await invalidate()
    },
  })

  const openCreate = () => {
    saveAccount.reset()
    setSelected(null)
    setDraft(emptyDraft)
    setEditorOpen(true)
  }

  const openEdit = (account: OutboundEmailAccount) => {
    saveAccount.reset()
    setSelected(account)
    setDraft(accountDraft(account))
    setEditorOpen(true)
  }

  const formReady = Boolean(
    draft.host.trim()
    && Number(draft.port) > 0
    && draft.username.trim()
    && draft.fromAddress.trim()
    && Number(draft.priority) > 0
    && (selected || draft.password)
    && (!draft.inboundEnabled || (
      draft.imapHost.trim()
      && Number(draft.imapPort) > 0
      && draft.imapUsername.trim()
      && (selected?.imap_password_configured || draft.imapPassword)
    )),
  )

  return (
    <Paper component="section" variant="outlined" aria-labelledby="email-accounts-title" sx={{ p: 2, mt: 2 }}>
      <Stack direction={{ xs: 'column', md: 'row' }} spacing={2} sx={{ alignItems: { xs: 'stretch', md: 'center' }, justifyContent: 'space-between' }}>
        <Box>
          <Typography id="email-accounts-title" component="h2" variant="h3">邮件账号</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            管理邮件发送和收件账号。密码仅在新增或更换时填写，保存后不再显示。
          </Typography>
        </Box>
        <Button variant="contained" startIcon={<AddRoundedIcon />} onClick={openCreate}>新增邮件账号</Button>
      </Stack>
      <Divider sx={{ my: 2 }} />

      {accounts.isError ? <OperatorErrorNotice title="无法读取邮件账号" error={accounts.error} fallback="请稍后重试" /> : null}
      {markets.isError ? <OperatorErrorNotice title="无法读取市场" error={markets.error} fallback="请稍后重试" /> : null}
      {toggleActive.isError ? <OperatorErrorNotice title="账号状态更新失败" error={toggleActive.error} fallback="请稍后重试" /> : null}
      {accounts.isLoading ? <OperatorLoadingState label="正在加载邮件账号…" minHeight={220} /> : !sortedAccounts.length ? (
        <OperatorEmptyState title="暂无邮件账号" description="新增账号并完成测试发送后，即可启用邮件渠道。" />
      ) : (
        <TableContainer>
          <Table size="small" aria-label="邮件账号列表">
            <TableHead>
              <TableRow>
                <TableCell>账号</TableCell>
                <TableCell>市场</TableCell>
                <TableCell>发送</TableCell>
                <TableCell>收件</TableCell>
                <TableCell>测试</TableCell>
                <TableCell>状态</TableCell>
                <TableCell align="right">操作</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {sortedAccounts.map((account) => {
                const health = healthPresentation(account.health_status)
                const market = markets.data?.find((item) => item.id === account.market_id)
                return (
                  <TableRow key={account.id} hover>
                    <TableCell>
                      <Typography variant="subtitle2">{sanitizeDisplayText(account.display_name || account.from_address)}</Typography>
                      <Typography variant="caption" color="text.secondary">{sanitizeDisplayText(account.from_address)}</Typography>
                    </TableCell>
                    <TableCell>{market?.name || (account.market_id ? `市场 #${account.market_id}` : '全部市场')}</TableCell>
                    <TableCell>{account.password_configured ? '已配置' : '缺少密码'}</TableCell>
                    <TableCell>{account.inbound_enabled ? (account.imap_password_configured ? '已启用' : '配置不完整') : '未启用'}</TableCell>
                    <TableCell>
                      <Typography variant="body2">{account.last_test_status || '未测试'}</Typography>
                      <Typography variant="caption" color="text.secondary">{account.last_test_at ? formatDateTime(account.last_test_at) : '暂无时间'}</Typography>
                    </TableCell>
                    <TableCell>
                      <Stack spacing={0.5} sx={{ alignItems: 'flex-start' }}>
                        <Chip size="small" color={operatorToneColor(health.tone)} label={health.label} />
                        <Chip size="small" color={account.is_active ? 'success' : 'default'} label={account.is_active ? '启用' : '停用'} />
                      </Stack>
                    </TableCell>
                    <TableCell align="right">
                      <Stack direction="row" spacing={0.5} useFlexGap sx={{ justifyContent: 'flex-end', flexWrap: 'wrap' }}>
                        <Button size="small" color="inherit" startIcon={<EditRoundedIcon />} onClick={() => openEdit(account)}>编辑</Button>
                        <Button size="small" color="inherit" startIcon={<SendRoundedIcon />} onClick={() => { testSend.reset(); setTestAccount(account) }}>测试发送</Button>
                        <Button
                          size="small"
                          color={account.is_active ? 'warning' : 'success'}
                          startIcon={account.is_active ? <ToggleOffRoundedIcon /> : <ToggleOnRoundedIcon />}
                          onClick={() => setToggleAccount(account)}
                        >
                          {account.is_active ? '停用' : '启用'}
                        </Button>
                      </Stack>
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      <Dialog open={editorOpen} onClose={() => { if (!saveAccount.isPending) setEditorOpen(false) }} fullWidth maxWidth="md">
        <Box component="form" onSubmit={(event: FormEvent<HTMLFormElement>) => { event.preventDefault(); if (formReady) saveAccount.mutate() }}>
          <DialogTitle>{selected ? '编辑邮件账号' : '新增邮件账号'}</DialogTitle>
          <DialogContent>
            <DialogContentText>
              编辑已有账号时，密码留空会保留当前密码。
            </DialogContentText>
            <Stack spacing={2} sx={{ mt: 2 }}>
              {saveAccount.isError ? <OperatorErrorNotice title="保存邮件账号失败" error={saveAccount.error} fallback="请检查地址、端口、市场和收件配置" /> : null}
              <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' } }}>
                <TextField label="账号名称" value={draft.displayName} onChange={(event) => setDraft((current) => ({ ...current, displayName: event.target.value }))} />
                <TextField select label="市场" value={draft.marketId} onChange={(event) => setDraft((current) => ({ ...current, marketId: event.target.value }))}>
                  <MenuItem value="">全部市场</MenuItem>
                  {(markets.data ?? []).map((market) => <MenuItem key={market.id} value={String(market.id)}>{market.name}</MenuItem>)}
                </TextField>
                <TextField label="发送服务器" required value={draft.host} onChange={(event) => setDraft((current) => ({ ...current, host: event.target.value }))} />
                <TextField label="发送端口" required type="number" value={draft.port} onChange={(event) => setDraft((current) => ({ ...current, port: event.target.value }))} />
                <TextField label="登录账号" required value={draft.username} onChange={(event) => setDraft((current) => ({ ...current, username: event.target.value }))} />
                <TextField label={selected ? '更换发送密码' : '发送密码'} required={!selected} type="password" autoComplete="new-password" value={draft.password} onChange={(event) => setDraft((current) => ({ ...current, password: event.target.value }))} helperText={selected ? '留空保留当前密码' : '必填'} />
                <TextField label="发件地址" required type="email" value={draft.fromAddress} onChange={(event) => setDraft((current) => ({ ...current, fromAddress: event.target.value }))} />
                <TextField label="回复地址" type="email" value={draft.replyTo} onChange={(event) => setDraft((current) => ({ ...current, replyTo: event.target.value }))} />
                <TextField label="优先级" type="number" required value={draft.priority} onChange={(event) => setDraft((current) => ({ ...current, priority: event.target.value }))} />
              </Box>

              <FormControlLabel control={<Switch checked={draft.isActive} onChange={(event) => setDraft((current) => ({ ...current, isActive: event.target.checked }))} />} label="保存后启用发送" />
              <FormControlLabel control={<Switch checked={draft.inboundEnabled} onChange={(event) => setDraft((current) => ({ ...current, inboundEnabled: event.target.checked }))} />} label="启用收件" />

              {draft.inboundEnabled ? (
                <Paper variant="outlined" sx={{ p: 2 }}>
                  <Typography component="h3" variant="h3">收件配置</Typography>
                  <Divider sx={{ my: 2 }} />
                  <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' } }}>
                    <TextField label="收件服务器" required value={draft.imapHost} onChange={(event) => setDraft((current) => ({ ...current, imapHost: event.target.value }))} />
                    <TextField label="收件端口" required type="number" value={draft.imapPort} onChange={(event) => setDraft((current) => ({ ...current, imapPort: event.target.value }))} />
                    <TextField label="收件登录账号" required value={draft.imapUsername} onChange={(event) => setDraft((current) => ({ ...current, imapUsername: event.target.value }))} />
                    <TextField label={selected ? '更换收件密码' : '收件密码'} required={!selected?.imap_password_configured} type="password" autoComplete="new-password" value={draft.imapPassword} onChange={(event) => setDraft((current) => ({ ...current, imapPassword: event.target.value }))} helperText={selected ? '留空保留当前密码' : '启用收件时必填'} />
                    <TextField label="邮箱目录" value={draft.imapMailbox} onChange={(event) => setDraft((current) => ({ ...current, imapMailbox: event.target.value }))} />
                  </Box>
                </Paper>
              ) : null}

              <OperatorTechnicalDisclosure title="高级连接设置" summary="加密方式与协议">
                <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' } }}>
                  <TextField select label="发送加密方式" value={draft.securityMode} onChange={(event) => setDraft((current) => ({ ...current, securityMode: event.target.value as OutboundEmailSecurityMode }))}>
                    <MenuItem value="starttls">STARTTLS</MenuItem>
                    <MenuItem value="ssl">SSL/TLS</MenuItem>
                    <MenuItem value="plain">不加密</MenuItem>
                  </TextField>
                  {draft.inboundEnabled ? (
                    <TextField select label="收件加密方式" value={draft.imapSecurityMode} onChange={(event) => setDraft((current) => ({ ...current, imapSecurityMode: event.target.value as OutboundEmailSecurityMode }))}>
                      <MenuItem value="ssl">SSL/TLS</MenuItem>
                      <MenuItem value="starttls">STARTTLS</MenuItem>
                      <MenuItem value="plain">不加密</MenuItem>
                    </TextField>
                  ) : null}
                </Box>
              </OperatorTechnicalDisclosure>
            </Stack>
          </DialogContent>
          <DialogActions>
            <Button color="inherit" disabled={saveAccount.isPending} onClick={() => setEditorOpen(false)}>取消</Button>
            <Button type="submit" variant="contained" disabled={!formReady || saveAccount.isPending} startIcon={saveAccount.isPending ? <CircularProgress color="inherit" size={16} /> : undefined}>
              {saveAccount.isPending ? '保存中…' : '保存账号'}
            </Button>
          </DialogActions>
        </Box>
      </Dialog>

      <Dialog open={Boolean(testAccount)} onClose={() => { if (!testSend.isPending) setTestAccount(null) }} maxWidth="sm" fullWidth>
        <DialogTitle>测试邮件发送</DialogTitle>
        <DialogContent>
          <DialogContentText>
            使用 {testAccount?.display_name || testAccount?.from_address} 向指定地址发送一封真实测试邮件。
          </DialogContentText>
          <Stack spacing={2} sx={{ mt: 2 }}>
            {testSend.isError ? <OperatorErrorNotice title="测试发送失败" error={testSend.error} fallback="请检查账号配置和收件地址" /> : null}
            <TextField label="测试收件地址" type="email" required value={testAddress} onChange={(event) => setTestAddress(event.target.value)} />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button color="inherit" disabled={testSend.isPending} onClick={() => setTestAccount(null)}>取消</Button>
          <Button variant="contained" disabled={!testAddress.trim() || testSend.isPending} startIcon={testSend.isPending ? <CircularProgress color="inherit" size={16} /> : <SendRoundedIcon />} onClick={() => testSend.mutate()}>
            {testSend.isPending ? '正在发送…' : '发送测试邮件'}
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog open={Boolean(toggleAccount)} onClose={() => { if (!toggleActive.isPending) setToggleAccount(null) }} maxWidth="xs" fullWidth>
        <DialogTitle>{toggleAccount?.is_active ? '停用邮件账号' : '启用邮件账号'}</DialogTitle>
        <DialogContent>
          <DialogContentText>
            {toggleAccount?.is_active
              ? '停用后，该账号不会用于新的邮件发送。历史记录会保留。'
              : '启用后仍应先完成测试发送，确认账号可以正常使用。'}
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button color="inherit" disabled={toggleActive.isPending} onClick={() => setToggleAccount(null)}>取消</Button>
          <Button color={toggleAccount?.is_active ? 'warning' : 'success'} variant="contained" disabled={!toggleAccount || toggleActive.isPending} onClick={() => { if (toggleAccount) toggleActive.mutate(toggleAccount) }}>
            {toggleActive.isPending ? '处理中…' : toggleAccount?.is_active ? '确认停用' : '确认启用'}
          </Button>
        </DialogActions>
      </Dialog>
    </Paper>
  )
}

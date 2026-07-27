import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  MenuItem,
  Paper,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  OperatorEmptyState,
  OperatorErrorNotice,
  OperatorFactGrid,
  OperatorTechnicalDisclosure,
  operatorToneColor,
} from '@/app/OperatorPresentation'
import { formatDateTime, sanitizeDisplayText } from '@/lib/format'
import { whatsappApi } from '@/lib/whatsappApi'
import type {
  WhatsAppConnection,
  WhatsAppConnectionCreate,
  WhatsAppTransport,
} from '@/lib/whatsappTypes'

type CreateDraft = {
  transport: WhatsAppTransport
  displayName: string
  accountId: string
  sidecarSessionKey: string
  businessAccountId: string
  wabaId: string
  phoneNumberId: string
  graphApiVersion: string
  accessToken: string
  appSecret: string
  verifyToken: string
}

const emptyDraft: CreateDraft = {
  transport: 'baileys_sidecar',
  displayName: '',
  accountId: '',
  sidecarSessionKey: '',
  businessAccountId: '',
  wabaId: '',
  phoneNumberId: '',
  graphApiVersion: '',
  accessToken: '',
  appSecret: '',
  verifyToken: '',
}

function presentation(connection: WhatsAppConnection) {
  if (connection.verification_state === 'verified' && connection.observed_state === 'connected') {
    return { label: connection.channel_active ? '生产启用' : '验证完成', tone: 'success' as const }
  }
  if (connection.observed_state === 'error' || connection.authentication_state === 'error') {
    return { label: '需要修复', tone: 'danger' as const }
  }
  if (connection.desired_state === 'binding' || ['qr_pending', 'auth_persisting', 'connecting'].includes(connection.observed_state)) {
    return { label: '绑定中', tone: 'warning' as const }
  }
  if (connection.authentication_state === 'revoked' || connection.observed_state === 'logged_out') {
    return { label: '已注销', tone: 'default' as const }
  }
  return { label: '待配置', tone: 'warning' as const }
}

function verificationLabel(value: string) {
  return {
    pending: '尚未验证',
    inbound_verified: '入站已验证',
    outbound_verified: '出站已验证',
    verified: '双向已验证',
    failed: '验证失败',
  }[value] || value
}

function transportLabel(value: WhatsAppTransport) {
  return value === 'meta_cloud_api' ? 'Meta Cloud API' : 'Baileys 关联设备'
}

function clean(value: string) {
  return value.trim() || null
}

export function WhatsAppConfigurationPanel() {
  const queryClient = useQueryClient()
  const [createOpen, setCreateOpen] = useState(false)
  const [draft, setDraft] = useState<CreateDraft>(emptyDraft)

  const connections = useQuery({
    queryKey: ['whatsappConnections'],
    queryFn: whatsappApi.connections,
    refetchInterval: 15_000,
    retry: false,
  })

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ['whatsappConnections'] })
  }

  const create = useMutation({
    mutationFn: () => {
      const payload: WhatsAppConnectionCreate = {
        transport: draft.transport,
        display_name: draft.displayName.trim(),
        account_id: draft.accountId.trim(),
        priority: 100,
      }
      if (draft.transport === 'baileys_sidecar') {
        payload.sidecar_session_key = clean(draft.sidecarSessionKey) || draft.accountId.trim()
      } else {
        payload.business_account_id = clean(draft.businessAccountId)
        payload.waba_id = clean(draft.wabaId)
        payload.phone_number_id = clean(draft.phoneNumberId)
        payload.graph_api_version = clean(draft.graphApiVersion)
        payload.access_token = clean(draft.accessToken)
        payload.app_secret = clean(draft.appSecret)
        payload.verify_token = clean(draft.verifyToken)
      }
      return whatsappApi.createConnection(payload)
    },
    onSuccess: async () => {
      setDraft(emptyDraft)
      setCreateOpen(false)
      await refresh()
    },
  })

  const createReady = Boolean(
    draft.displayName.trim()
      && draft.accountId.trim()
      && (
        draft.transport === 'baileys_sidecar'
          || (
            draft.wabaId.trim()
            && draft.phoneNumberId.trim()
            && draft.graphApiVersion.trim()
            && draft.accessToken.trim()
            && draft.appSecret.trim()
            && draft.verifyToken.trim()
          )
      ),
  )

  return (
    <Paper component="section" variant="outlined" aria-labelledby="whatsapp-configuration-title" sx={{ mt: 2, p: 2, minWidth: 0 }}>
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1.5} sx={{ alignItems: { xs: 'stretch', sm: 'center' }, justifyContent: 'space-between' }}>
        <Box>
          <Typography id="whatsapp-configuration-title" component="h2" variant="h3">WhatsApp 连接</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            Meta Cloud API 与 Baileys 使用同一账号、收件箱、出站队列和验证标准。双向验证完成前不会进入生产路由。
          </Typography>
        </Box>
        <Button variant="contained" onClick={() => setCreateOpen(true)}>新建 WhatsApp 连接</Button>
      </Stack>
      <Divider sx={{ my: 2 }} />

      {connections.isError ? (
        <OperatorErrorNotice title="无法读取 WhatsApp 连接" error={connections.error} fallback="请检查后端和租户权限" />
      ) : connections.isLoading ? (
        <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}><CircularProgress size={20} /><Typography variant="body2">正在读取连接…</Typography></Stack>
      ) : !(connections.data?.length) ? (
        <OperatorEmptyState title="尚未配置 WhatsApp" description="创建 Meta Cloud API 或 Baileys 连接后执行绑定和真实双向验证。" />
      ) : (
        <Stack spacing={2}>
          {connections.data.map((connection) => (
            <WhatsAppConnectionCard key={connection.id} connection={connection} onChanged={refresh} />
          ))}
        </Stack>
      )}

      <Dialog open={createOpen} onClose={() => { if (!create.isPending) setCreateOpen(false) }} fullWidth maxWidth="sm" aria-labelledby="create-whatsapp-title">
        <DialogTitle id="create-whatsapp-title">新建 WhatsApp 连接</DialogTitle>
        <DialogContent>
          <Stack spacing={1.5} sx={{ mt: 1 }}>
            {create.error ? <OperatorErrorNotice title="创建失败" error={create.error} fallback="请检查账号标识和连接参数" /> : null}
            <TextField select required label="传输方式" value={draft.transport} onChange={(event) => setDraft((current) => ({ ...current, transport: event.target.value as WhatsAppTransport }))}>
              <MenuItem value="meta_cloud_api">Meta Cloud API</MenuItem>
              <MenuItem value="baileys_sidecar">Baileys 关联设备</MenuItem>
            </TextField>
            <TextField required label="账号名称" value={draft.displayName} onChange={(event) => setDraft((current) => ({ ...current, displayName: event.target.value }))} />
            <TextField required label="Nexus 账号标识" helperText="稳定的内部标识，例如 wa-ch-primary；创建后不可更换传输方式。" value={draft.accountId} onChange={(event) => setDraft((current) => ({ ...current, accountId: event.target.value }))} />
            {draft.transport === 'baileys_sidecar' ? (
              <TextField label="Sidecar 会话标识" helperText="留空时使用 Nexus 账号标识。" value={draft.sidecarSessionKey} onChange={(event) => setDraft((current) => ({ ...current, sidecarSessionKey: event.target.value }))} />
            ) : (
              <>
                <Alert severity="info" variant="outlined">请粘贴 Meta Embedded Signup 或 Business Manager 返回的正式资产与系统用户凭证。凭证仅加密写入，不会回显。</Alert>
                <TextField label="Business Account ID" value={draft.businessAccountId} onChange={(event) => setDraft((current) => ({ ...current, businessAccountId: event.target.value }))} />
                <TextField required label="WABA ID" value={draft.wabaId} onChange={(event) => setDraft((current) => ({ ...current, wabaId: event.target.value }))} />
                <TextField required label="Phone Number ID" value={draft.phoneNumberId} onChange={(event) => setDraft((current) => ({ ...current, phoneNumberId: event.target.value }))} />
                <TextField required label="Graph API 版本" placeholder="例如当前已批准的 vXX.X" helperText="必须显式填写并按 Meta 发布周期复核，系统不会静默升级。" value={draft.graphApiVersion} onChange={(event) => setDraft((current) => ({ ...current, graphApiVersion: event.target.value }))} />
                <TextField required type="password" autoComplete="new-password" label="System User Access Token" value={draft.accessToken} onChange={(event) => setDraft((current) => ({ ...current, accessToken: event.target.value }))} />
                <TextField required type="password" autoComplete="new-password" label="Meta App Secret" value={draft.appSecret} onChange={(event) => setDraft((current) => ({ ...current, appSecret: event.target.value }))} />
                <TextField required type="password" autoComplete="new-password" label="Webhook Verify Token" helperText="至少 16 个字符。" value={draft.verifyToken} onChange={(event) => setDraft((current) => ({ ...current, verifyToken: event.target.value }))} />
              </>
            )}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button color="inherit" disabled={create.isPending} onClick={() => setCreateOpen(false)}>取消</Button>
          <Button variant="contained" disabled={!createReady || create.isPending} onClick={() => create.mutate()}>
            {create.isPending ? '创建中…' : '创建并进入绑定'}
          </Button>
        </DialogActions>
      </Dialog>
    </Paper>
  )
}

function WhatsAppConnectionCard({ connection, onChanged }: { connection: WhatsAppConnection; onChanged: () => Promise<void> }) {
  const [pairingPhone, setPairingPhone] = useState('')
  const [pairingCode, setPairingCode] = useState<string | null>(null)
  const [inboundMessageId, setInboundMessageId] = useState('')
  const [outboundTarget, setOutboundTarget] = useState('')
  const [outboundBody, setOutboundBody] = useState('Nexus WhatsApp connection verification')
  const state = presentation(connection)

  const qr = useQuery({
    queryKey: ['whatsappBindingQr', connection.id],
    queryFn: () => whatsappApi.bindingQr(connection.id),
    enabled: connection.transport === 'baileys_sidecar' && connection.desired_state === 'binding',
    refetchInterval: 5_000,
    retry: false,
  })

  const mutation = useMutation({
    mutationFn: async (action: string) => {
      if (action === 'bind') return whatsappApi.startBinding(connection.id)
      if (action === 'pair') return whatsappApi.requestPairingCode(connection.id, pairingPhone)
      if (action === 'probe') return whatsappApi.probe(connection.id)
      if (action === 'restart') return whatsappApi.restart(connection.id)
      if (action === 'logout') return whatsappApi.logout(connection.id)
      if (action === 'activate') return whatsappApi.setDesiredState(connection.id, 'active')
      if (action === 'disable') return whatsappApi.setDesiredState(connection.id, 'disabled')
      if (action === 'inbound') return whatsappApi.testInbound(connection.id, inboundMessageId)
      if (action === 'outbound') return whatsappApi.testOutbound(connection.id, outboundTarget, outboundBody)
      throw new Error('不支持的操作')
    },
    onSuccess: async (result, action) => {
      if (action === 'pair' && 'pairing_code' in result) setPairingCode(result.pairing_code)
      await onChanged()
    },
  })

  const facts = useMemo(() => [
    ['传输', transportLabel(connection.transport)],
    ['运行状态', sanitizeDisplayText(connection.observed_state)],
    ['认证状态', sanitizeDisplayText(connection.authentication_state)],
    ['监听状态', sanitizeDisplayText(connection.listener_state)],
    ['验证状态', verificationLabel(connection.verification_state)],
    ['绑定号码', connection.phone_number_mask || '尚未返回'],
    ['最近连接', connection.last_connected_at ? formatDateTime(connection.last_connected_at) : '暂无'],
    ['配置版本', `${connection.observed_generation} / ${connection.desired_generation}`],
  ], [connection])

  const operationError = mutation.error || qr.error
  const canActivate = connection.verification_state === 'verified'
    && connection.observed_state === 'connected'
    && connection.authentication_state === 'linked'
    && connection.listener_state === 'active'
    && connection.observed_generation === connection.desired_generation

  return (
    <Paper component="article" variant="outlined" sx={{ p: 2, minWidth: 0 }}>
      <Stack direction={{ xs: 'column', md: 'row' }} spacing={2} sx={{ justifyContent: 'space-between' }}>
        <Box sx={{ minWidth: 0, flex: 1 }}>
          <Stack direction="row" spacing={1} useFlexGap sx={{ alignItems: 'center', flexWrap: 'wrap' }}>
            <Typography component="h3" variant="h4">{sanitizeDisplayText(connection.display_name || connection.account_id)}</Typography>
            <Chip color={operatorToneColor(state.tone)} label={state.label} />
            <Chip variant="outlined" label={transportLabel(connection.transport)} />
          </Stack>
          <Typography variant="caption" color="text.secondary">账号标识：{sanitizeDisplayText(connection.account_id)}</Typography>
        </Box>
        <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: 'wrap', alignItems: 'flex-start' }}>
          <Button size="small" variant="contained" disabled={mutation.isPending} onClick={() => mutation.mutate('bind')}>{connection.transport === 'meta_cloud_api' ? '订阅并验证' : '开始绑定'}</Button>
          <Button size="small" variant="outlined" disabled={mutation.isPending} onClick={() => mutation.mutate('probe')}>运行探针</Button>
          <Button size="small" variant="outlined" disabled={mutation.isPending} onClick={() => mutation.mutate('restart')}>重启连接</Button>
          <Button size="small" color="error" disabled={mutation.isPending} onClick={() => mutation.mutate('logout')}>注销</Button>
        </Stack>
      </Stack>

      {operationError ? <Box sx={{ mt: 2 }}><OperatorErrorNotice title="WhatsApp 操作失败" error={operationError} fallback="请查看错误编号并执行探针" /></Box> : null}
      {connection.last_error_message ? <Alert severity="error" variant="outlined" sx={{ mt: 2 }}>{sanitizeDisplayText(connection.last_error_message)}</Alert> : null}

      <Box sx={{ mt: 2 }}><OperatorFactGrid facts={facts} /></Box>

      {connection.transport === 'baileys_sidecar' && connection.desired_state === 'binding' ? (
        <Paper variant="outlined" sx={{ mt: 2, p: 2 }}>
          <Typography component="h4" variant="subtitle1">关联设备认证</Typography>
          <Stack spacing={1.5} sx={{ mt: 1.5 }}>
            {qr.data?.qr_data_url ? (
              <Box component="img" src={qr.data.qr_data_url} alt="WhatsApp 关联设备二维码" sx={{ width: 240, maxWidth: '100%', height: 'auto', alignSelf: 'center' }} />
            ) : <Alert severity="info" variant="outlined">二维码正在生成或已经过期。系统每 5 秒读取最新版本，不缓存二维码。</Alert>}
            {qr.data?.qr_expires_at ? <Typography variant="caption" color="text.secondary">二维码有效期至 {formatDateTime(qr.data.qr_expires_at)}</Typography> : null}
            <Divider />
            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
              <TextField fullWidth label="配对电话号码" placeholder="含国家码，例如 +41…" value={pairingPhone} onChange={(event) => setPairingPhone(event.target.value)} />
              <Button variant="outlined" disabled={!pairingPhone.trim() || mutation.isPending} onClick={() => mutation.mutate('pair')}>生成配对码</Button>
            </Stack>
            {pairingCode ? <Alert severity="success" variant="outlined">配对码：<Box component="strong" sx={{ fontVariantNumeric: 'tabular-nums' }}>{pairingCode}</Box></Alert> : null}
          </Stack>
        </Paper>
      ) : null}

      <Paper variant="outlined" sx={{ mt: 2, p: 2 }}>
        <Typography component="h4" variant="subtitle1">真实双向验证</Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
          入站必须填写实际收到并已存储的 Provider Message ID；出站必须由 Provider 返回真实消息 ID。两个方向都通过后才能激活。
        </Typography>
        <Stack spacing={1.5} sx={{ mt: 1.5 }}>
          <Stack direction={{ xs: 'column', md: 'row' }} spacing={1}>
            <TextField fullWidth label="真实入站 Provider Message ID" value={inboundMessageId} onChange={(event) => setInboundMessageId(event.target.value)} />
            <Button variant="outlined" disabled={!inboundMessageId.trim() || mutation.isPending} onClick={() => mutation.mutate('inbound')}>验证入站</Button>
          </Stack>
          <Stack direction={{ xs: 'column', md: 'row' }} spacing={1}>
            <TextField label="测试收件号码" placeholder="E.164 国家码号码" value={outboundTarget} onChange={(event) => setOutboundTarget(event.target.value)} sx={{ minWidth: { md: 240 } }} />
            <TextField fullWidth label="测试消息" value={outboundBody} onChange={(event) => setOutboundBody(event.target.value)} />
            <Button variant="outlined" disabled={!outboundTarget.trim() || !outboundBody.trim() || mutation.isPending} onClick={() => mutation.mutate('outbound')}>验证出站</Button>
          </Stack>
        </Stack>
      </Paper>

      <Stack direction="row" spacing={1} useFlexGap sx={{ mt: 2, flexWrap: 'wrap' }}>
        {connection.channel_active ? (
          <Button color="warning" variant="outlined" disabled={mutation.isPending} onClick={() => mutation.mutate('disable')}>停用生产路由</Button>
        ) : (
          <Button variant="contained" disabled={!canActivate || mutation.isPending} onClick={() => mutation.mutate('activate')}>激活生产路由</Button>
        )}
        {!canActivate && !connection.channel_active ? <Typography variant="caption" color="text.secondary" sx={{ alignSelf: 'center' }}>需连接正常、双向验证完成且配置版本一致。</Typography> : null}
      </Stack>

      <OperatorTechnicalDisclosure title="技术与审计信息">
        <OperatorFactGrid facts={[
          ['Connection ID', connection.id],
          ['Channel Account ID', connection.channel_account_id],
          ['WABA ID', connection.waba_id || '不适用'],
          ['Phone Number ID', connection.phone_number_id || '不适用'],
          ['Sidecar Session', connection.sidecar_session_key || '不适用'],
          ['密钥配置', connection.transport === 'meta_cloud_api' ? `Token ${connection.access_token_configured ? '已配置' : '缺失'} / App Secret ${connection.app_secret_configured ? '已配置' : '缺失'} / Verify Token ${connection.verify_token_configured ? '已配置' : '缺失'}` : '由 Sidecar 加密会话卷持有'],
          ['重连次数', connection.reconnect_count],
          ['最后探针', connection.last_probe_at ? formatDateTime(connection.last_probe_at) : '暂无'],
          ['错误编号', connection.last_error_code || '无'],
        ]} />
      </OperatorTechnicalDisclosure>
    </Paper>
  )
}

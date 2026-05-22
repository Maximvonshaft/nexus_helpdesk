import { useState } from 'react'
import { createRoute, redirect, useNavigate } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Route as RootRoute } from './root'
import { AppShell } from '@/layouts/AppShell'
import { api, getToken } from '@/lib/api'
import { formatDateTime, sanitizeDisplayText } from '@/lib/format'
import { useSession } from '@/hooks/useAuth'
import { canViewOps } from '@/lib/access'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { Field, Input } from '@/components/ui/Field'
import { PageHeader } from '@/components/ui/PageHeader'
import { Toast } from '@/components/ui/Toast'
import type { CodexDeviceStart, CodexSessionStatus, ProviderCredentialStatus } from '@/lib/types'

function scopeList(raw: string) {
  return raw.split(/[\s,]+/).map((item) => item.trim()).filter(Boolean)
}

function credentialTone(status: string, revokedAt?: string | null) {
  if (revokedAt || status === 'revoked') return 'danger' as const
  if (status === 'active') return 'success' as const
  if (status === 'pending') return 'warning' as const
  return 'default' as const
}

function CredentialCard({ credential, onRefresh, onRevoke, onDisconnect, pending }: {
  credential: ProviderCredentialStatus
  onRefresh: (id: string) => void
  onRevoke: (id: string) => void
  onDisconnect: (id: string) => void
  pending: boolean
}) {
  return <div className="list-item">
    <div className="badges">
      <Badge tone={credentialTone(credential.status, credential.revoked_at)}>{credential.status}</Badge>
      <Badge>{sanitizeDisplayText(credential.provider_runtime)}</Badge>
      {credential.scope ? <Badge>{sanitizeDisplayText(credential.scope)}</Badge> : <Badge tone="warning">scope 未声明</Badge>}
    </div>
    <div><strong>{sanitizeDisplayText(credential.email || credential.account_id || credential.profile_id)}</strong></div>
    <div className="section-subtitle">Credential: {credential.id} · Fingerprint: {credential.token_fingerprint_prefix || '—'}</div>
    <div className="section-subtitle">Expires: {formatDateTime(credential.expires_at)} · Last refresh: {formatDateTime(credential.last_refresh_at)} · Error: {sanitizeDisplayText(credential.last_error_code || '—')}</div>
    <div className="button-row" style={{ marginTop: 8 }}>
      <Button variant="secondary" disabled={pending || credential.status !== 'active'} onClick={() => onRefresh(credential.id)}>刷新 Token</Button>
      <Button variant="secondary" disabled={pending || credential.status === 'revoked'} onClick={() => onDisconnect(credential.id)}>本地断开</Button>
      <Button disabled={pending || credential.status === 'revoked'} onClick={() => onRevoke(credential.id)}>上游撤销</Button>
    </div>
  </div>
}

function ProviderCredentialsPage() {
  const session = useSession()
  const navigate = useNavigate()
  const client = useQueryClient()
  const permitted = canViewOps(session.data)
  const [scopes, setScopes] = useState('')
  const [device, setDevice] = useState<CodexDeviceStart | null>(null)
  const [sessionStatus, setSessionStatus] = useState<CodexSessionStatus | null>(null)
  const [toast, setToast] = useState<{ message: string; tone?: 'default' | 'danger' | 'success' } | null>(null)

  const status = useQuery({ queryKey: ['codex-credential-status'], queryFn: api.codexCredentialStatus, enabled: permitted })

  const refreshStatus = async () => {
    await client.invalidateQueries({ queryKey: ['codex-credential-status'] })
  }

  const authorize = useMutation({
    mutationFn: () => api.startCodexAuthorization(scopeList(scopes)),
    onSuccess: (data) => {
      setToast({ message: '已生成 Code X 授权 URL，正在打开授权页面。', tone: 'success' })
      window.location.assign(data.authorization_url)
    },
    onError: (err: Error) => setToast({ message: err.message || '生成授权 URL 失败', tone: 'danger' }),
  })

  const startDevice = useMutation({
    mutationFn: () => api.startCodexDeviceFlow(scopeList(scopes)),
    onSuccess: (data) => {
      setDevice(data)
      setSessionStatus({ status: 'pending', session_id: data.session_id, user_code: data.user_code, verification_url: data.verification_url, expires_at: data.expires_at, scope: data.scope })
      setToast({ message: '已启动 Device 授权，请使用验证码完成 Code X 授权。', tone: 'success' })
    },
    onError: (err: Error) => setToast({ message: err.message || '启动 Device 授权失败', tone: 'danger' }),
  })

  const pollDevice = useMutation({
    mutationFn: async () => {
      if (!device?.session_id) throw new Error('没有待轮询的授权会话')
      return api.pollCodexDeviceFlow(device.session_id)
    },
    onSuccess: async (data) => {
      setSessionStatus(data)
      if (data.status === 'authorized') {
        setToast({ message: 'Code X 授权成功，Token 已加密保存。', tone: 'success' })
        await refreshStatus()
      } else if (data.status === 'failed' || data.status === 'expired') {
        setToast({ message: `授权失败：${data.error_code || data.status}`, tone: 'danger' })
      } else {
        setToast({ message: `授权状态：${data.status}` })
      }
    },
    onError: (err: Error) => setToast({ message: err.message || '轮询授权失败', tone: 'danger' }),
  })

  const action = useMutation({
    mutationFn: async ({ id, op }: { id: string; op: 'refresh' | 'revoke' | 'disconnect' }) => {
      if (op === 'refresh') return api.refreshCodexCredential(id)
      if (op === 'revoke') return api.revokeCodexCredential(id)
      return api.disconnectCodexCredential(id)
    },
    onSuccess: async (data) => {
      setToast({ message: `操作完成：${data.status}${data.error_code ? ` / ${data.error_code}` : ''}`, tone: data.ok ? 'success' : 'danger' })
      await refreshStatus()
    },
    onError: (err: Error) => setToast({ message: err.message || 'Credential 操作失败', tone: 'danger' }),
  })

  if (session.data && !permitted) navigate({ to: '/' })

  const credentials = status.data?.credentials ?? []
  const pending = authorize.isPending || startDevice.isPending || pollDevice.isPending || action.isPending

  return <AppShell>
    <PageHeader
      eyebrow="Provider Credentials"
      title="Code X / Codex 云端授权"
      description="由 Nexus 后端生成授权会话、校验 state/PKCE、完成 token exchange，并加密保存 access/refresh token。前端只显示 masked 状态。"
      actions={<div className="button-row"><Button variant="secondary" onClick={() => refreshStatus()}>刷新状态</Button><Button onClick={() => authorize.mutate()} disabled={pending}>浏览器授权</Button><Button variant="secondary" onClick={() => startDevice.mutate()} disabled={pending}>Device 授权</Button></div>}
    />
    <div className="page-grid split-grid">
      <Card className="soft">
        <CardHeader title="授权范围" subtitle="留空则使用后端 CODEX_OAUTH_DEFAULT_SCOPES；若后端配置 CODEX_OAUTH_ALLOWED_SCOPES，未列入 allowlist 的 scope 会被拒绝。" />
        <CardBody>
          <Field label="Scopes" hint="空格或逗号分隔；例如 read:profile reply:write。不要在前端配置 client_secret。">
            <Input value={scopes} onChange={(e) => setScopes(e.target.value)} placeholder="后端默认 scope" />
          </Field>
          {device ? <div className="message" style={{ marginTop: 12 }}>
            <div><strong>Device Code:</strong> {sanitizeDisplayText(device.user_code)}</div>
            <div><strong>Verification:</strong> <a href={device.verification_url} target="_blank" rel="noreferrer">打开 Code X 授权页</a></div>
            <div><strong>Expires:</strong> {formatDateTime(device.expires_at)}</div>
            <div className="button-row" style={{ marginTop: 8 }}><Button onClick={() => pollDevice.mutate()} disabled={pending}>轮询授权结果</Button></div>
          </div> : null}
          {sessionStatus ? <div className="section-subtitle" style={{ marginTop: 8 }}>当前授权会话：{sessionStatus.session_id || device?.session_id || '—'} · {sessionStatus.status} · {sessionStatus.error_code || 'no_error'}</div> : null}
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="连接状态" subtitle={`Active credentials: ${status.data?.active_count ?? 0}；不会展示任何 token 明文。`} />
        <CardBody>
          <div className="list">
            {credentials.map((credential) => <CredentialCard key={credential.id} credential={credential} pending={pending} onRefresh={(id) => action.mutate({ id, op: 'refresh' })} onRevoke={(id) => action.mutate({ id, op: 'revoke' })} onDisconnect={(id) => action.mutate({ id, op: 'disconnect' })} />)}
            {!credentials.length ? <div className="message">还没有 Code X credential。请先发起授权。</div> : null}
          </div>
        </CardBody>
      </Card>
    </div>
    {toast ? <Toast message={toast.message} tone={toast.tone} onClose={() => setToast(null)} /> : null}
  </AppShell>
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/provider-credentials',
  beforeLoad: () => { if (!getToken()) throw redirect({ to: '/login' }) },
  component: ProviderCredentialsPage,
})

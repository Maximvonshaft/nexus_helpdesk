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
import { EmptyState } from '@/components/ui/EmptyState'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { TechnicalDetails } from '@/components/ui/TechnicalDetails'
import { GuidedWorkflow } from '@/components/ui/GuidedWorkflow'
import type { CodexDeviceStart, CodexManualAuthorizationStart, CodexSessionStatus, ProviderCredentialStatus } from '@/lib/types'
import { credentialTermLabels } from '@/lib/uxCopy'

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
      <Badge tone={credentialTone(credential.status, credential.revoked_at)}>{credentialTermLabels[credential.status] ?? credential.status}</Badge>
      <Badge>{sanitizeDisplayText(credential.email ? '已绑定邮箱' : '服务账号')}</Badge>
      {credential.chatgpt_plan_type ? <Badge>{sanitizeDisplayText(credential.chatgpt_plan_type)}</Badge> : null}
    </div>
    <div><strong>{sanitizeDisplayText(credential.email || credential.account_id || credential.profile_id)}</strong></div>
    <div className="section-subtitle">可用于云端授权调用；Token 明文不会在前端展示。</div>
    <div className="section-subtitle">有效期：{formatDateTime(credential.expires_at)} · 最近刷新：{formatDateTime(credential.last_refresh_at)} · 最近错误：{sanitizeDisplayText(credential.last_error_code || '无')}</div>
    <div className="button-row" style={{ marginTop: 8 }}>
      <Button variant="secondary" disabled={pending || credential.status !== 'active'} onClick={() => onRefresh(credential.id)}>刷新 Token</Button>
      <Button variant="secondary" disabled={pending || credential.status === 'revoked'} onClick={() => onDisconnect(credential.id)}>本地断开</Button>
      <Button disabled={pending || credential.status === 'revoked'} onClick={() => onRevoke(credential.id)}>上游撤销</Button>
    </div>
    <TechnicalDetails title="凭证技术详情" summary="排查 OAuth、scope、fingerprint 时查看">
      <div className="kv-grid">
        <div className="kv"><label>Credential ID</label><div>{sanitizeDisplayText(credential.id)}</div></div>
        <div className="kv"><label>Provider Runtime</label><div>{sanitizeDisplayText(credential.provider_runtime)}</div></div>
        <div className="kv"><label>Scope</label><div>{sanitizeDisplayText(credential.scope || '未声明')}</div></div>
        <div className="kv"><label>Fingerprint</label><div>{sanitizeDisplayText(credential.token_fingerprint_prefix || '—')}</div></div>
      </div>
    </TechnicalDetails>
  </div>
}

function ProviderCredentialsPage() {
  const session = useSession()
  const navigate = useNavigate()
  const client = useQueryClient()
  const permitted = canViewOps(session.data)
  const [scopes, setScopes] = useState('')
  const [device, setDevice] = useState<CodexDeviceStart | null>(null)
  const [manual, setManual] = useState<CodexManualAuthorizationStart | null>(null)
  const [manualResponse, setManualResponse] = useState('')
  const [sessionStatus, setSessionStatus] = useState<CodexSessionStatus | null>(null)
  const [toast, setToast] = useState<{ message: string; tone?: 'default' | 'danger' | 'success' } | null>(null)
  const [confirmAction, setConfirmAction] = useState<{ id: string; op: 'revoke' | 'disconnect' } | null>(null)

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

  const startManual = useMutation({
    mutationFn: api.startCodexManualAuthorization,
    onSuccess: (data) => {
      setManual(data)
      setManualResponse('')
      setSessionStatus({ status: 'pending', session_id: data.session_id, expires_at: data.expires_at, scope: data.scope })
      setToast({ message: '已生成 Codex 授权链接，请在浏览器完成登录后粘贴 redirect URL 或 code。', tone: 'success' })
      window.open(data.authorization_url, '_blank', 'noopener,noreferrer')
    },
    onError: (err: Error) => setToast({ message: err.message || '生成 Codex 授权链接失败', tone: 'danger' }),
  })

  const completeManual = useMutation({
    mutationFn: async () => {
      if (!manual?.session_id) throw new Error('没有待完成的 Codex 授权会话')
      return api.completeCodexManualAuthorization(manual.session_id, manualResponse)
    },
    onSuccess: async (data) => {
      setSessionStatus({ status: data.status, session_id: manual?.session_id, credential_id: data.credential_id })
      setToast({ message: 'Codex ChatGPT 授权成功，Token 已加密保存。', tone: 'success' })
      setManualResponse('')
      await refreshStatus()
    },
    onError: (err: Error) => setToast({ message: err.message || '完成 Codex 授权失败', tone: 'danger' }),
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
  const pending = authorize.isPending || startDevice.isPending || startManual.isPending || completeManual.isPending || pollDevice.isPending || action.isPending

  return <AppShell>
    <PageHeader
      eyebrow="Provider Credentials"
      title="Code X / Codex 云端授权"
      description="连接一个可用的 Code X 授权账号，让后台可以安全刷新授权并支持相关自动化。OAuth 细节默认收起。"
      actions={<div className="button-row"><Button variant="secondary" onClick={() => refreshStatus()}>刷新状态</Button><Button onClick={() => authorize.mutate()} disabled={pending}>浏览器授权</Button><Button variant="secondary" onClick={() => startDevice.mutate()} disabled={pending}>Device 授权</Button></div>}
    />
    <Card className="soft">
      <CardHeader title="推荐处理步骤" subtitle="正常授权只需要按步骤走；排障时再展开高级技术详情。" />
      <CardBody>
        <GuidedWorkflow steps={[
          { title: '确认连接状态', description: '先看是否已有可用授权。', status: credentials.length ? 'done' : 'active' },
          { title: '选择授权方式', description: '优先浏览器授权，远程环境用手动授权。', status: 'active' },
          { title: '完成登录', description: '按页面提示完成 Code X 登录。', status: sessionStatus?.status === 'authorized' ? 'done' : 'todo' },
          { title: '刷新状态', description: '授权后回到本页确认已连接。', status: 'todo' },
          { title: '排查高级信息', description: '仅在授权失败或 scope 异常时查看。', status: 'todo' },
        ]} />
      </CardBody>
    </Card>
    <div className="page-grid split-grid">
      <Card className="soft">
        <CardHeader title="开始 Code X 授权" subtitle="多数情况下直接使用后端默认授权范围，不需要手动填写 scope。" />
        <CardBody>
          <TechnicalDetails title="高级授权范围" summary="只有排查 scope 拒绝或最小权限时填写">
            <Field label="Scopes" hint="空格或逗号分隔；例如 read:profile reply:write。不要在前端配置 client_secret。">
              <Input value={scopes} onChange={(e) => setScopes(e.target.value)} placeholder="后端默认 scope" />
            </Field>
          </TechnicalDetails>
          {device ? <div className="message" style={{ marginTop: 12 }}>
            <div><strong>验证码：</strong> {sanitizeDisplayText(device.user_code)}</div>
            <div><strong>授权页面：</strong> <a href={device.verification_url} target="_blank" rel="noreferrer">打开 Code X 授权页</a></div>
            <div><strong>过期时间：</strong> {formatDateTime(device.expires_at)}</div>
            <div className="button-row" style={{ marginTop: 8 }}><Button onClick={() => pollDevice.mutate()} disabled={pending}>轮询授权结果</Button></div>
          </div> : null}
          {sessionStatus ? <TechnicalDetails title="当前授权会话详情" summary={credentialTermLabels[sessionStatus.status] ?? sessionStatus.status}><div className="section-subtitle">Session：{sessionStatus.session_id || device?.session_id || '—'} · {sessionStatus.status} · {sessionStatus.error_code || 'no_error'}</div></TechnicalDetails> : null}
        </CardBody>
      </Card>
      <Card className="soft">
        <CardHeader title="Codex ChatGPT 订阅授权" subtitle="适用于云端、远程或 localhost callback 无法自动返回 Nexus 的场景。" />
        <CardBody>
          <div className="message">
            如果浏览器跳转到 localhost 页面失败，请复制地址栏里的完整 URL 粘贴回来。
          </div>
          <div className="button-row" style={{ marginTop: 12 }}>
            <Button onClick={() => startManual.mutate()} disabled={pending}>生成 Codex 授权链接</Button>
            {manual?.authorization_url ? <Button variant="secondary" onClick={() => window.open(manual.authorization_url, '_blank', 'noopener,noreferrer')} disabled={pending}>打开授权链接</Button> : null}
          </div>
          {manual ? <TechnicalDetails title="手动授权技术详情" summary="复制回调 URL 或 code 时查看"><div className="message" style={{ marginTop: 12 }}>
            <div><strong>Redirect URI:</strong> {sanitizeDisplayText(manual.redirect_uri)}</div>
            <div><strong>Scope:</strong> {sanitizeDisplayText(manual.scope || '—')}</div>
            <div><strong>Expires:</strong> {formatDateTime(manual.expires_at)}</div>
            <div style={{ marginTop: 8, wordBreak: 'break-all' }}><a href={manual.authorization_url} target="_blank" rel="noreferrer">{manual.authorization_url}</a></div>
          </div></TechnicalDetails> : null}
          <Field label="授权返回内容" hint="支持完整 localhost redirect URL、code=...&state=... query string，或裸 code。">
            <textarea
              value={manualResponse}
              onChange={(e) => setManualResponse(e.target.value)}
              placeholder="http://localhost:1455/auth/callback?code=...&state=..."
              rows={5}
              style={{ width: '100%', resize: 'vertical' }}
            />
          </Field>
          <div className="button-row" style={{ marginTop: 8 }}>
            <Button onClick={() => completeManual.mutate()} disabled={pending || !manual?.session_id || !manualResponse.trim()}>完成授权</Button>
          </div>
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="连接状态" subtitle={`当前可用授权 ${status.data?.active_count ?? 0} 个；不会展示任何 token 明文。`} />
        <CardBody>
          <div className="list">
            {credentials.map((credential) => <CredentialCard key={credential.id} credential={credential} pending={pending} onRefresh={(id) => action.mutate({ id, op: 'refresh' })} onRevoke={(id) => setConfirmAction({ id, op: 'revoke' })} onDisconnect={(id) => setConfirmAction({ id, op: 'disconnect' })} />)}
            {!credentials.length ? <EmptyState title="还没有连接 Code X 授权" description="完成授权后，这里会显示连接状态、身份和有效期。" reason="请先使用浏览器授权；远程环境无法自动回调时再使用手动授权。" action={<Button onClick={() => authorize.mutate()} disabled={pending}>浏览器授权</Button>} /> : null}
          </div>
        </CardBody>
      </Card>
    </div>
    {toast ? <Toast message={toast.message} tone={toast.tone} onClose={() => setToast(null)} /> : null}
    <ConfirmDialog
      open={confirmAction !== null}
      title={confirmAction?.op === 'revoke' ? '撤销上游授权？' : '断开本地授权？'}
      description={confirmAction?.op === 'revoke' ? '撤销后，上游授权也会失效，相关自动化将无法继续使用该账号。' : '断开后，本系统将停止使用该凭证；上游授权可能仍存在。'}
      consequence="该操作会影响生产授权能力。确认前请确保已有替代授权或业务允许暂停。"
      confirmLabel={confirmAction?.op === 'revoke' ? '确认撤销' : '确认断开'}
      tone="danger"
      pending={action.isPending}
      onCancel={() => setConfirmAction(null)}
      onConfirm={() => {
        const next = confirmAction
        setConfirmAction(null)
        if (next) action.mutate(next)
      }}
    />
  </AppShell>
}

export const Route = createRoute({
  getParentRoute: () => RootRoute,
  path: '/provider-credentials',
  beforeLoad: () => { if (!getToken()) throw redirect({ to: '/login' }) },
  component: ProviderCredentialsPage,
})

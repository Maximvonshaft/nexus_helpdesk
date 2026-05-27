import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { CaseDetail, OutboundChannelCapability } from '@/lib/types'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { EmptyState } from '@/components/ui/EmptyState'
import { Field, Input, Select, Textarea } from '@/components/ui/Field'
import { Skeleton } from '@/components/ui/Skeleton'
import { labelize, sanitizeDisplayText } from '@/lib/format'
import { SpeedafActionsPanel } from '@/components/operator/SpeedafActionsPanel'

function capabilityTone(capability?: OutboundChannelCapability) {
  if (!capability) return 'default'
  if (capability.supports_send) return capability.external_send ? 'warning' : 'success'
  return 'danger'
}

function defaultReply(activeCase: CaseDetail) {
  return [
    activeCase.customer_update,
    activeCase.required_action ? `Next step: ${activeCase.required_action}` : null,
    activeCase.missing_fields ? `Missing information: ${activeCase.missing_fields}` : null,
  ].filter(Boolean).join('\n\n')
}

function defaultEmailSubject(activeCase: CaseDetail) {
  return activeCase.title?.trim() || `工单 ${activeCase.id} 客户回复`
}

function emailRecipient(activeCase: CaseDetail) {
  return activeCase.preferred_reply_contact || activeCase.customer?.email || ''
}

function deliveryLabel(capability?: OutboundChannelCapability) {
  if (!capability) return '请选择回复渠道'
  if (capability.channel === 'email') return 'SMTP 外部邮件发送：会进入真实 Email outbox，并由后台 worker 连接已配置 SMTP 账号发送。'
  if (capability.external_send) return '外部渠道发送：会进入真实外部发送链路或外部队列。'
  if (capability.dispatch_type === 'local') return '本地 WebChat 发送：不会触发外部供应商。'
  return capability.operator_note || '当前渠道不具备客户发送能力。'
}

function replyTarget(activeCase: CaseDetail, channel: string) {
  if (channel === 'email') return emailRecipient(activeCase)
  return activeCase.preferred_reply_contact || activeCase.openclaw_conversation?.recipient || activeCase.customer?.phone || activeCase.customer?.email || ''
}

export function CustomerReplyPanel({ activeCase, onToast }: { activeCase: CaseDetail; onToast: (toast: { message: string; tone?: 'default' | 'danger' | 'success' }) => void }) {
  const client = useQueryClient()
  const [channel, setChannel] = useState(activeCase.preferred_reply_channel || activeCase.openclaw_conversation?.channel || 'web_chat')
  const [subject, setSubject] = useState(defaultEmailSubject(activeCase))
  const [body, setBody] = useState(defaultReply(activeCase))
  const [confirmExternal, setConfirmExternal] = useState(false)

  const capabilities = useQuery({
    queryKey: ['ticketOutboundChannelCapabilities', activeCase.id],
    queryFn: () => api.ticketOutboundChannelCapabilities(activeCase.id),
    enabled: !!activeCase.id,
  })

  useEffect(() => {
    setChannel(activeCase.preferred_reply_channel || activeCase.openclaw_conversation?.channel || 'web_chat')
    setSubject(defaultEmailSubject(activeCase))
    setBody(defaultReply(activeCase))
    setConfirmExternal(false)
    // Reset only when the ticket changes; live refetches must not wipe an operator draft.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeCase.id])

  const sendableChannels = useMemo(
    () => (capabilities.data?.channels ?? []).filter((item) => item.customer_sendable),
    [capabilities.data?.channels],
  )
  const selectedCapability = useMemo(
    () => (capabilities.data?.channels ?? []).find((item) => item.channel === channel),
    [capabilities.data?.channels, channel],
  )
  const selectedIsEmail = channel === 'email'
  const resolvedTarget = replyTarget(activeCase, channel)
  const canSend = Boolean(selectedCapability?.supports_send && body.trim() && (!selectedIsEmail || subject.trim()) && (!selectedCapability.external_send || confirmExternal))

  const sendMutation = useMutation({
    mutationFn: () => api.sendOutboundMessage(activeCase.id, selectedIsEmail ? { channel, subject: subject.trim(), body: body.trim() } : { channel, body: body.trim() }),
    onSuccess: async (result) => {
      const semantics = String(result.delivery_semantics || '')
      onToast({
        message: semantics === 'external_provider_send' ? '客户回复已进入外部发送队列' : '客户回复已发送/记录',
        tone: 'success',
      })
      setBody('')
      setConfirmExternal(false)
      await client.invalidateQueries({ queryKey: ['caseDetail', activeCase.id] })
      await client.invalidateQueries({ queryKey: ['ticketTimeline', activeCase.id] })
      await client.invalidateQueries({ queryKey: ['cases'] })
    },
    onError: (err: Error) => onToast({ message: err.message || '发送客户回复失败', tone: 'danger' }),
  })

  return (
    <>
      <Card className="soft">
        <CardHeader title="发送给客户" subtitle="从工单工作台直接闭环客户回复，发送前必须看清渠道、目标和发送语义。" />
        <CardBody>
          {capabilities.isLoading ? <Skeleton lines={4} /> : null}
          {capabilities.isError ? <EmptyState text="无法加载当前工单的回复渠道状态。" /> : null}
          {!capabilities.isLoading && !capabilities.isError ? (
            <div className="stack" data-testid="workspace-customer-reply-panel">
              <Field label="回复渠道">
                <Select value={channel} onChange={(event) => {
                  const nextChannel = event.target.value
                  setChannel(nextChannel)
                  if (nextChannel === 'email' && !subject.trim()) setSubject(defaultEmailSubject(activeCase))
                  setConfirmExternal(false)
                }}>
                  {sendableChannels.map((item) => (
                    <option key={item.channel} value={item.channel}>
                      {item.label} · {item.supports_send ? '可发送' : '未就绪'}
                    </option>
                  ))}
                </Select>
              </Field>

              <div className="kv-grid">
                <div className="kv"><label>{selectedIsEmail ? 'Email 收件人' : '目标联系对象'}</label><div>{sanitizeDisplayText(resolvedTarget || '未配置')}</div></div>
                <div className="kv"><label>发送语义</label><div>{sanitizeDisplayText(deliveryLabel(selectedCapability))}</div></div>
              </div>

              <div className="badges">
                <Badge tone={capabilityTone(selectedCapability)}>{selectedCapability?.supports_send ? '当前可发送' : '当前不可发送'}</Badge>
                {selectedCapability?.external_send ? <Badge tone="warning">外部发送</Badge> : <Badge tone="success">本地/非外部</Badge>}
                {selectedCapability?.status ? <Badge>{labelize(selectedCapability.status)}</Badge> : null}
              </div>

              {selectedCapability && selectedCapability.missing.length ? (
                <div className="message" data-role="user">
                  <strong>发送阻断项：</strong> {selectedCapability.missing.map(labelize).join('、')}
                </div>
              ) : null}

              {selectedIsEmail ? (
                <>
                  <Field label="Email 主题" required hint="主题会随发送请求提交；请不要依赖隐藏默认值。">
                    <Input value={subject} onChange={(event) => setSubject(event.target.value)} placeholder="请输入邮件主题" />
                  </Field>
                  <div className="message" data-role="agent">
                    本次会通过后端 Email capability 选择市场 SMTP 账号；没有市场账号时使用全局 fallback。发送结果会先进入外部 provider outbox。
                  </div>
                </>
              ) : null}

              <Field label="回复正文" hint="建议先核对客户最新消息、公告和证据，再点击发送。">
                <Textarea value={body} onChange={(event) => setBody(event.target.value)} rows={7} placeholder="输入要发送给客户的回复…" />
              </Field>

              {selectedCapability?.external_send ? (
                <label className="checkbox-row">
                  <input type="checkbox" checked={confirmExternal} onChange={(event) => setConfirmExternal(event.target.checked)} />
                  <span>{selectedIsEmail ? '我确认这是 SMTP 外部邮件发送，收件人、主题和正文已核对。' : '我确认这是外部客户渠道发送，内容和目标已核对。'}</span>
                </label>
              ) : null}

              <Button variant="primary" onClick={() => sendMutation.mutate()} disabled={!canSend || sendMutation.isPending}>
                {sendMutation.isPending ? '发送中…' : '发送客户回复'}
              </Button>
            </div>
          ) : null}
        </CardBody>
      </Card>
      <SpeedafActionsPanel activeCase={activeCase} onToast={onToast} />
    </>
  )
}

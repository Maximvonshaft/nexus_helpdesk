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

function deliveryLabel(capability?: OutboundChannelCapability) {
  if (!capability) return '请选择回复渠道'
  if (capability.external_send) return '外部渠道发送：会进入真实外部发送链路或外部队列。'
  if (capability.dispatch_type === 'local') return '本地 WebChat 发送：不会触发外部供应商。'
  return capability.operator_note || '当前渠道不具备客户发送能力。'
}

export function CustomerReplyPanel({ activeCase, onToast }: { activeCase: CaseDetail; onToast: (toast: { message: string; tone?: 'default' | 'danger' | 'success' }) => void }) {
  const client = useQueryClient()
  const [channel, setChannel] = useState(activeCase.preferred_reply_channel || activeCase.openclaw_conversation?.channel || 'web_chat')
  const [body, setBody] = useState(defaultReply(activeCase))
  const [emailSubject, setEmailSubject] = useState(`Re: ${activeCase.title}`)
  const [emailTo, setEmailTo] = useState(activeCase.customer?.email || activeCase.preferred_reply_contact || '')
  const [emailCc, setEmailCc] = useState('')
  const [emailBcc, setEmailBcc] = useState('')
  const [confirmExternal, setConfirmExternal] = useState(false)

  const capabilities = useQuery({
    queryKey: ['ticketOutboundChannelCapabilities', activeCase.id],
    queryFn: () => api.ticketOutboundChannelCapabilities(activeCase.id),
    enabled: !!activeCase.id,
  })

  useEffect(() => {
    setChannel(activeCase.preferred_reply_channel || activeCase.openclaw_conversation?.channel || 'web_chat')
    setBody(defaultReply(activeCase))
    setEmailSubject(`Re: ${activeCase.title}`)
    setEmailTo(activeCase.customer?.email || activeCase.preferred_reply_contact || '')
    setEmailCc('')
    setEmailBcc('')
    setConfirmExternal(false)
  }, [activeCase.id])

  const sendableChannels = useMemo(
    () => (capabilities.data?.channels ?? []).filter((item) => item.customer_sendable),
    [capabilities.data?.channels],
  )
  const selectedCapability = useMemo(
    () => (capabilities.data?.channels ?? []).find((item) => item.channel === channel),
    [capabilities.data?.channels, channel],
  )
  const canSend = Boolean(selectedCapability?.supports_send && body.trim() && (channel !== 'email' || (emailTo.trim() && emailSubject.trim())) && (!selectedCapability.external_send || confirmExternal))

  const sendCurrentMessage = () => {
    if (channel !== 'email') {
      return api.sendOutboundMessage(activeCase.id, { channel, body: body.trim() })
    }
    return api.sendOutboundMessage(activeCase.id, {
      channel: 'email',
      body: body.trim(),
      email_to: emailTo.trim(),
      email_subject: emailSubject.trim(),
      email_cc: emailCc.split(',').map((item) => item.trim()).filter(Boolean),
      email_bcc: emailBcc.split(',').map((item) => item.trim()).filter(Boolean),
    })
  }

  const sendMutation = useMutation({
    mutationFn: sendCurrentMessage,
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
                <Select value={channel} onChange={(event) => { setChannel(event.target.value); setConfirmExternal(false); }}>
                  {sendableChannels.map((item) => (
                    <option key={item.channel} value={item.channel}>
                      {item.label} · {item.supports_send ? '可发送' : '未就绪'}
                    </option>
                  ))}
                </Select>
              </Field>

              <div className="kv-grid">
                <div className="kv"><label>目标联系对象</label><div>{sanitizeDisplayText(activeCase.preferred_reply_contact || activeCase.openclaw_conversation?.recipient || activeCase.customer?.phone || activeCase.customer?.email || '未配置')}</div></div>
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

              {channel === 'email' ? (
                <div className="form-grid">
                  <Field label="To" required><Input value={emailTo} onChange={(event) => setEmailTo(event.target.value)} /></Field>
                  <Field label="Subject" required><Input value={emailSubject} onChange={(event) => setEmailSubject(event.target.value)} /></Field>
                  <Field label="CC"><Input value={emailCc} onChange={(event) => setEmailCc(event.target.value)} /></Field>
                  <Field label="BCC"><Input value={emailBcc} onChange={(event) => setEmailBcc(event.target.value)} /></Field>
                </div>
              ) : null}

              <Field label="回复正文" hint="建议先核对客户最新消息、公告和证据，再点击发送。">
                <Textarea value={body} onChange={(event) => setBody(event.target.value)} rows={7} placeholder="输入要发送给客户的回复…" />
              </Field>

              {selectedCapability?.external_send ? (
                <label className="checkbox-row">
                  <input type="checkbox" checked={confirmExternal} onChange={(event) => setConfirmExternal(event.target.checked)} />
                  <span>我确认这是外部客户渠道发送，内容和目标已核对。</span>
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

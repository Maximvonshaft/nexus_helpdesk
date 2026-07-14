import { useEffect, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/EmptyState'
import { ErrorSummary } from '@/components/ui/ErrorSummary'
import { Field, Input, Select, Textarea } from '@/components/ui/Field'
import { sanitizeDisplayText } from '@/lib/format'
import { operatorWorkspaceApi } from '@/lib/operatorWorkspaceApi'
import { outcomePresentation } from '@/lib/operatorWorkspacePresentation'
import { supportApi } from '@/lib/supportApi'
import type { UnifiedOperatorQueueItem } from '@/lib/operatorWorkspaceTypes'
import type { WebchatThread } from '@/lib/types'
import { errorCopy, hasCapability, safeRecord, textValue } from '../workspaceUtils'

type ServiceAction = 'none' | 'waybill_lookup' | 'work_order' | 'address_update' | 'cancel'
type ActionResultEnvelope = { kind: ServiceAction; result: Record<string, unknown> }
type CancelPreview = {
  cancelAllowed: boolean
  confirmToken?: string | null
  currentStatusLabel?: string | null
  reasonLabel?: string | null
}

const cancelReasons = [
  { value: 'CC01', label: '派送太慢' },
  { value: 'CC02', label: '快递员服务问题' },
  { value: 'CC03', label: '不支持验货' },
  { value: 'CC04', label: '不支持部分签收' },
  { value: 'CC05', label: '其他原因' },
]

function disabledReason({
  action,
  item,
  capabilities,
  waybill,
  caller,
  description,
  contactPhone,
}: {
  action: ServiceAction
  item: UnifiedOperatorQueueItem
  capabilities: Set<string>
  waybill: string
  caller: string
  description: string
  contactPhone: string
}) {
  if (action === 'none') return '请先选择与客户诉求相符的处理动作'
  if (!item.ticket_id) return '当前案例没有可执行动作的工单'
  if (action === 'waybill_lookup') return caller.trim() ? '' : '缺少客户电话'
  if (!waybill.trim()) return '缺少运单号'
  if (!caller.trim()) return '缺少客户电话'
  if (action === 'work_order' && !hasCapability(capabilities, 'tool:speedaf.work_order.create:write')) return '当前账号不能创建催派工单'
  if (action === 'address_update' && !hasCapability(capabilities, 'tool:speedaf.order.update_address:write')) return '当前账号不能更新联系号码'
  if (action === 'cancel' && !hasCapability(capabilities, 'tool:speedaf.order.cancel:write')) return '当前账号不能请求取消'
  if (action === 'work_order' && !description.trim()) return '请说明客户诉求和需要运营处理的内容'
  if (action === 'address_update' && !contactPhone.trim()) return '缺少客户确认后的联系号码'
  return ''
}

export function ServiceActionsPanel({
  item,
  thread,
  capabilities,
  selectionUnavailable,
  onRefresh,
}: {
  item: UnifiedOperatorQueueItem
  thread: WebchatThread | null
  capabilities: Set<string>
  selectionUnavailable: boolean
  onRefresh: () => Promise<void>
}) {
  const [action, setAction] = useState<ServiceAction>('none')
  const [waybill, setWaybill] = useState('')
  const [caller, setCaller] = useState('')
  const [countryCode, setCountryCode] = useState(item.country_code || 'CH')
  const [description, setDescription] = useState('')
  const [contactPhone, setContactPhone] = useState('')
  const [reasonCode, setReasonCode] = useState('CC01')
  const [cancelPreview, setCancelPreview] = useState<CancelPreview | null>(null)

  useEffect(() => {
    setAction('none')
    setWaybill('')
    setCaller(thread?.visitor?.phone || '')
    setContactPhone(thread?.visitor?.phone || '')
    setCountryCode(item.country_code || 'CH')
    setDescription('')
    setCancelPreview(null)
  }, [item.country_code, item.queue_id, thread?.visitor?.phone])

  const handoffMutation = useMutation({
    mutationFn: async (kind: 'accept' | 'force' | 'release' | 'resume' | 'decline') => {
      const handoff = thread?.handoff
      if (kind === 'accept' && handoff?.id) return supportApi.webchatAcceptHandoff(handoff.id, 'Accepted from customer-service workspace')
      if (kind === 'force' && item.ticket_id) return supportApi.webchatForceTakeover(item.ticket_id, { reason_code: 'operator_takeover', note: 'Customer-service takeover' })
      if (kind === 'release' && handoff?.id) return supportApi.webchatReleaseHandoff(handoff.id, 'Released from customer-service workspace')
      if (kind === 'resume' && handoff?.id) return supportApi.webchatResumeAi(handoff.id, 'Returned to automatic reception')
      if (kind === 'decline' && handoff?.id) return operatorWorkspaceApi.declineHandoff(handoff.id, 'operator_capacity', 'Declined from customer-service workspace')
      throw new Error('当前接管动作不可执行')
    },
    onSuccess: onRefresh,
  })

  const actionMutation = useMutation({
    mutationFn: async (): Promise<ActionResultEnvelope> => {
      if (!item.ticket_id) throw new Error('当前案例没有可执行动作的工单')
      if (action === 'waybill_lookup') {
        const result = await supportApi.querySpeedafWaybills(item.ticket_id, {
          callerID: caller.trim(),
          countryCode: countryCode.trim().toUpperCase(),
        })
        return { kind: action, result: result as unknown as Record<string, unknown> }
      }
      if (action === 'work_order') {
        const result = await supportApi.createSpeedafWorkOrder(item.ticket_id, {
          waybillCode: waybill.trim().toUpperCase(),
          callerID: caller.trim(),
          workOrderType: 'WT0103-05',
          description: description.trim(),
        })
        return { kind: action, result: result as unknown as Record<string, unknown> }
      }
      if (action === 'address_update') {
        const result = await supportApi.submitSpeedafAddressUpdate(item.ticket_id, {
          waybillCode: waybill.trim().toUpperCase(),
          callerID: caller.trim(),
          whatsAppPhone: contactPhone.trim(),
        })
        return { kind: action, result: result as unknown as Record<string, unknown> }
      }
      throw new Error('请选择可执行动作')
    },
    onSuccess: onRefresh,
  })

  const cancelPreviewMutation = useMutation({
    mutationFn: async () => {
      if (!item.ticket_id) throw new Error('当前案例没有可执行动作的工单')
      return supportApi.previewSpeedafCancel(item.ticket_id, {
        waybillCode: waybill.trim().toUpperCase(),
        callerID: caller.trim(),
        reasonCode,
      })
    },
    onSuccess: (result) => setCancelPreview(result),
  })

  const cancelConfirmMutation = useMutation({
    mutationFn: async (): Promise<ActionResultEnvelope> => {
      if (!item.ticket_id) throw new Error('当前案例没有可执行动作的工单')
      const result = await supportApi.confirmSpeedafCancel(item.ticket_id, {
        waybillCode: waybill.trim().toUpperCase(),
        callerID: caller.trim(),
        reasonCode,
        confirmToken: cancelPreview?.confirmToken || '',
      })
      return { kind: 'cancel', result: result as unknown as Record<string, unknown> }
    },
    onSuccess: onRefresh,
  })

  if (selectionUnavailable) {
    return <EmptyState title="当前案例操作已暂停" description="该案例已离开你的待办范围，请选择仍可处理的案例。" />
  }

  const reason = disabledReason({ action, item, capabilities, waybill, caller, description, contactPhone })
  const busy = handoffMutation.isPending || actionMutation.isPending || cancelPreviewMutation.isPending || cancelConfirmMutation.isPending
  const actionError = handoffMutation.error || actionMutation.error || cancelPreviewMutation.error || cancelConfirmMutation.error
  const envelope = actionMutation.data || cancelConfirmMutation.data
  const resultRecord = envelope?.result ?? {}
  const resultPresentation = envelope ? outcomePresentation(resultRecord.status, resultRecord.message) : null
  const candidates = Array.isArray(resultRecord.candidates) ? resultRecord.candidates.map(safeRecord) : []
  const handoff = thread?.handoff
  const canManageConversation = hasCapability(
    capabilities,
    'webchat.handoff.accept',
    'webchat.handoff.force_takeover',
    'webchat.handoff.release',
    'webchat.handoff.resume_ai',
  )
  const takeoverAvailable = Boolean(handoff?.can_accept || handoff?.can_force_takeover || (!handoff && item.source_type === 'handoff'))

  return (
    <section className="service-actions" aria-labelledby="service-actions-title">
      <div className="workspace-section-heading">
        <div>
          <h2 id="service-actions-title">处理动作</h2>
          <p>先确认客户诉求和事实，再执行一个主要动作。</p>
        </div>
        <Badge tone="warning">执行前再次校验权限</Badge>
      </div>

      <div className="service-action-group">
        <h3>案例责任</h3>
        {!thread ? <p className="action-disabled-copy">当前案例没有客户会话，仍可处理工单和运营动作。</p> : null}
        {!canManageConversation ? <p className="action-disabled-copy">当前账号没有接管或释放会话的权限。</p> : null}
        <div className="service-action-buttons">
          {takeoverAvailable ? (
            <Button variant="primary" loading={handoffMutation.isPending} disabled={!thread || !canManageConversation} onClick={() => handoffMutation.mutate(handoff?.can_accept ? 'accept' : 'force')}>
              接手案例
            </Button>
          ) : null}
          {handoff?.can_decline ? <Button variant="secondary" onClick={() => handoffMutation.mutate('decline')}>暂不接手</Button> : null}
          {handoff?.can_release ? <Button variant="ghost" onClick={() => handoffMutation.mutate('release')}>释放案例</Button> : null}
          {handoff?.can_resume_ai ? <Button variant="ghost" onClick={() => handoffMutation.mutate('resume')}>交回自动接待</Button> : null}
        </div>
        {handoff?.reason_text || handoff?.recommended_agent_action ? (
          <div className="action-context">
            {handoff.reason_text ? <p><strong>转人工原因：</strong>{sanitizeDisplayText(handoff.reason_text)}</p> : null}
            {handoff.recommended_agent_action ? <p><strong>建议下一步：</strong>{sanitizeDisplayText(handoff.recommended_agent_action)}</p> : null}
          </div>
        ) : null}
      </div>

      <div className="service-action-group">
        <h3>运单与运营处理</h3>
        <Field label="选择动作" description="查询不会修改业务数据；写入动作需要明确确认。">
          <Select
            value={action}
            onChange={(event) => {
              setAction(event.target.value as ServiceAction)
              setCancelPreview(null)
              actionMutation.reset()
              cancelConfirmMutation.reset()
            }}
          >
            <option value="none">请选择动作</option>
            <option value="waybill_lookup">根据电话查询运单</option>
            <option value="work_order">创建催派工单</option>
            <option value="address_update">更新联系号码</option>
            <option value="cancel">取消预检与确认</option>
          </Select>
        </Field>

        {action !== 'none' ? (
          <>
            <div className="service-action-grid">
              {action !== 'waybill_lookup' ? (
                <Field label="运单号" required>
                  <Input value={waybill} onChange={(event) => setWaybill(event.target.value.toUpperCase())} autoComplete="off" placeholder="输入完整运单号" />
                </Field>
              ) : null}
              <Field label="客户电话" required>
                <Input type="tel" inputMode="tel" value={caller} onChange={(event) => setCaller(event.target.value)} autoComplete="off" placeholder="输入客户电话" />
              </Field>
            </div>
            {action === 'waybill_lookup' ? (
              <Field label="国家代码" required>
                <Input value={countryCode} onChange={(event) => setCountryCode(event.target.value.toUpperCase())} autoComplete="off" />
              </Field>
            ) : null}
            {action === 'work_order' ? (
              <Field label="催派说明" required description="说明客户诉求、已核实事实和期望的运营处理。">
                <Textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={3} />
              </Field>
            ) : null}
            {action === 'address_update' ? (
              <Field label="确认后的联系号码" required>
                <Input type="tel" inputMode="tel" value={contactPhone} onChange={(event) => setContactPhone(event.target.value)} />
              </Field>
            ) : null}
            {action === 'cancel' ? (
              <Field label="取消原因" required>
                <Select value={reasonCode} onChange={(event) => { setReasonCode(event.target.value); setCancelPreview(null) }}>
                  {cancelReasons.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                </Select>
              </Field>
            ) : null}
          </>
        ) : null}

        {reason && action !== 'none' ? <p className="action-disabled-copy"><strong>当前不能执行：</strong>{reason}</p> : null}
        {actionError ? <ErrorSummary title="处理动作失败" errors={[errorCopy(actionError, '请检查信息后重试')]} /> : null}

        {resultPresentation ? (
          <div className={`action-result is-${resultPresentation.tone}`} role="status">
            <strong>{resultPresentation.label}</strong>
            {resultPresentation.detail ? <p>{sanitizeDisplayText(resultPresentation.detail)}</p> : null}
          </div>
        ) : null}

        {candidates.length ? (
          <div className="waybill-candidates">
            <strong>找到 {candidates.length} 个候选运单</strong>
            {candidates.slice(0, 5).map((candidate) => {
              const code = textValue(candidate.waybillCode)
              return (
                <div key={code}>
                  <span>{sanitizeDisplayText(code)}</span>
                  <Button variant="secondary" onClick={() => { setWaybill(code); setAction('work_order') }}>用于催派</Button>
                </div>
              )
            })}
          </div>
        ) : null}

        {cancelPreview ? (
          <div className={`action-result ${cancelPreview.cancelAllowed ? 'is-default' : 'is-warning'}`} role="status">
            <strong>{cancelPreview.cancelAllowed ? '当前状态允许取消' : '当前状态不允许取消'}</strong>
            <p>{sanitizeDisplayText(cancelPreview.currentStatusLabel || cancelPreview.reasonLabel || '')}</p>
          </div>
        ) : null}

        <div className="service-action-buttons">
          {action === 'cancel' ? (
            <>
              <Button variant="secondary" disabled={busy || Boolean(reason)} onClick={() => cancelPreviewMutation.mutate()}>
                {cancelPreviewMutation.isPending ? '检查中…' : '检查是否可取消'}
              </Button>
              <Button variant="danger" disabled={busy || !cancelPreview?.cancelAllowed || !cancelPreview.confirmToken} onClick={() => cancelConfirmMutation.mutate()}>
                {cancelConfirmMutation.isPending ? '提交中…' : '确认取消'}
              </Button>
            </>
          ) : action === 'waybill_lookup' ? (
            <Button variant="secondary" disabled={busy || Boolean(reason)} onClick={() => actionMutation.mutate()}>
              {actionMutation.isPending ? '查询中…' : '查询运单'}
            </Button>
          ) : action !== 'none' ? (
            <Button variant="primary" disabled={busy || Boolean(reason)} onClick={() => actionMutation.mutate()}>
              {actionMutation.isPending ? '提交中…' : action === 'work_order' ? '创建催派工单' : '提交联系号码更新'}
            </Button>
          ) : null}
        </div>
      </div>
    </section>
  )
}

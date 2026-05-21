import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import type { CaseDetail } from '@/lib/types'
import { speedafApi } from '@/lib/speedafApi'
import type { SpeedafActionResponse, SpeedafCancelPreviewResponse } from '@/lib/speedafTypes'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { Field, Input, Select, Textarea } from '@/components/ui/Field'
import { sanitizeDisplayText } from '@/lib/format'

function normalizePhone(value?: string | null) {
  return String(value || '').replace(/[^0-9+]/g, '').slice(0, 80)
}

function defaultCallerId(activeCase: CaseDetail) {
  return normalizePhone(activeCase.customer?.phone || activeCase.preferred_reply_contact)
}

function defaultWaybill(activeCase: CaseDetail) {
  return String(activeCase.tracking_number || '').trim().toUpperCase().slice(0, 80)
}

type ToastFn = (toast: { message: string; tone?: 'default' | 'danger' | 'success' }) => void

function ActionResult({ result }: { result: SpeedafActionResponse | null }) {
  if (!result) return null
  return (
    <div className="message" data-role="agent">
      <strong>{sanitizeDisplayText(result.status)}</strong> · {sanitizeDisplayText(result.message)}
      {result.jobId ? <div className="section-subtitle">Job #{result.jobId}</div> : null}
      {result.dedupeKey ? <div className="section-subtitle">Dedupe: {sanitizeDisplayText(result.dedupeKey)}</div> : null}
    </div>
  )
}

export function SpeedafActionsPanel({ activeCase, onToast }: { activeCase: CaseDetail; onToast: ToastFn }) {
  const client = useQueryClient()
  const [workOrderDescription, setWorkOrderDescription] = useState('Please follow up delivery with Speedaf last-mile operations.')
  const [workOrderResult, setWorkOrderResult] = useState<SpeedafActionResponse | null>(null)
  const [addressPhone, setAddressPhone] = useState(defaultCallerId(activeCase))
  const [addressResult, setAddressResult] = useState<SpeedafActionResponse | null>(null)
  const [reasonCode, setReasonCode] = useState('CC01')
  const [cancelPreview, setCancelPreview] = useState<SpeedafCancelPreviewResponse | null>(null)
  const [confirmCancel, setConfirmCancel] = useState(false)

  const waybillCode = defaultWaybill(activeCase)
  const callerID = defaultCallerId(activeCase)
  const hasMinimumData = Boolean(waybillCode && callerID)

  useEffect(() => {
    setAddressPhone(defaultCallerId(activeCase))
    setWorkOrderResult(null)
    setAddressResult(null)
    setCancelPreview(null)
    setConfirmCancel(false)
  }, [activeCase.id])

  const refresh = async () => {
    await client.invalidateQueries({ queryKey: ['caseDetail', activeCase.id] })
    await client.invalidateQueries({ queryKey: ['ticketTimeline', activeCase.id] })
    await client.invalidateQueries({ queryKey: ['cases'] })
  }

  const basePayload = useMemo(() => ({ waybillCode, callerID }), [waybillCode, callerID])

  const workOrderMutation = useMutation({
    mutationFn: () => speedafApi.createWorkOrder(activeCase.id, {
      ...basePayload,
      workOrderType: 'WT0103-05',
      description: workOrderDescription,
    }),
    onSuccess: async (result) => {
      setWorkOrderResult(result)
      onToast({ message: 'Speedaf 催派工单已排队', tone: 'success' })
      await refresh()
    },
    onError: (err: Error) => onToast({ message: err.message || 'Speedaf 催派工单提交失败', tone: 'danger' }),
  })

  const addressMutation = useMutation({
    mutationFn: () => speedafApi.addressUpdate(activeCase.id, {
      ...basePayload,
      whatsAppPhone: addressPhone,
    }),
    onSuccess: async (result) => {
      setAddressResult(result)
      onToast({ message: 'Speedaf 地址更新确认请求已排队', tone: 'success' })
      await refresh()
    },
    onError: (err: Error) => onToast({ message: err.message || 'Speedaf 地址更新请求失败', tone: 'danger' }),
  })

  const cancelPreviewMutation = useMutation({
    mutationFn: () => speedafApi.cancelPreview(activeCase.id, { ...basePayload, reasonCode }),
    onSuccess: async (result) => {
      setCancelPreview(result)
      setConfirmCancel(false)
      onToast({ message: result.cancelAllowed ? '取消预检通过，请二次确认' : '当前状态不允许取消', tone: result.cancelAllowed ? 'success' : 'danger' })
      await refresh()
    },
    onError: (err: Error) => onToast({ message: err.message || 'Speedaf 取消预检失败', tone: 'danger' }),
  })

  const cancelMutation = useMutation({
    mutationFn: () => speedafApi.cancel(activeCase.id, {
      ...basePayload,
      reasonCode,
      confirmToken: cancelPreview?.confirmToken || '',
    }),
    onSuccess: async (result) => {
      onToast({ message: result.message || 'Speedaf 取消请求已提交', tone: 'success' })
      setCancelPreview(null)
      setConfirmCancel(false)
      await refresh()
    },
    onError: (err: Error) => onToast({ message: err.message || 'Speedaf 取消请求失败', tone: 'danger' }),
  })

  const canSubmitWorkOrder = hasMinimumData && workOrderDescription.trim().length > 0 && !workOrderMutation.isPending
  const canSubmitAddress = hasMinimumData && addressPhone.trim().length >= 4 && !addressMutation.isPending
  const canPreviewCancel = hasMinimumData && !cancelPreviewMutation.isPending
  const canConfirmCancel = Boolean(cancelPreview?.cancelAllowed && cancelPreview.confirmToken && confirmCancel && !cancelMutation.isPending)

  return (
    <Card className="soft">
      <CardHeader title="Speedaf 操作" subtitle="高风险动作由后端 feature flag、权限、限流、幂等和审计统一保护；前端只提供受控入口。" />
      <CardBody>
        <div className="stack" data-testid="speedaf-actions-panel">
          <div className="badges">
            <Badge tone={hasMinimumData ? 'success' : 'warning'}>{hasMinimumData ? '基础信息完整' : '缺运单号或客户电话'}</Badge>
            <Badge tone="warning">写动作默认关闭</Badge>
            <Badge>后端审计</Badge>
          </div>

          <div className="kv-grid">
            <div className="kv"><label>运单号</label><div>{sanitizeDisplayText(waybillCode || '缺失')}</div></div>
            <div className="kv"><label>callerID</label><div>{sanitizeDisplayText(callerID || '缺失')}</div></div>
          </div>

          {!hasMinimumData ? (
            <div className="message" data-role="user">
              Speedaf 操作需要工单存在运单号和客户电话。缺失时请先补齐工单资料，不要绕过后端校验。
            </div>
          ) : null}

          <Card>
            <CardHeader title="催派工单" subtitle="创建 Speedaf WT0103-05 催派/派送跟进工单，后端会排队执行。" />
            <CardBody>
              <div className="stack">
                <Field label="工单说明" hint="发送给 Speedaf 前会按接口合同截断到 200 字符。">
                  <Textarea value={workOrderDescription} onChange={(event) => setWorkOrderDescription(event.target.value)} rows={4} />
                </Field>
                <Button variant="secondary" onClick={() => workOrderMutation.mutate()} disabled={!canSubmitWorkOrder}>
                  {workOrderMutation.isPending ? '排队中…' : '创建催派工单'}
                </Button>
                <ActionResult result={workOrderResult} />
              </div>
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="地址更新确认请求" subtitle="提交 WhatsApp/地址更新确认请求；这不代表地址已经修改成功。" />
            <CardBody>
              <div className="stack">
                <Field label="WhatsApp 电话">
                  <Input value={addressPhone} onChange={(event) => setAddressPhone(event.target.value)} placeholder="例如：41790000000" />
                </Field>
                <Button variant="secondary" onClick={() => addressMutation.mutate()} disabled={!canSubmitAddress}>
                  {addressMutation.isPending ? '排队中…' : '提交地址更新确认请求'}
                </Button>
                <ActionResult result={addressResult} />
              </div>
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="取消运单" subtitle="最高风险动作：必须先预检，再使用短效确认令牌提交。" />
            <CardBody>
              <div className="stack">
                <Field label="取消原因">
                  <Select value={reasonCode} onChange={(event) => { setReasonCode(event.target.value); setCancelPreview(null); setConfirmCancel(false); }}>
                    <option value="CC01">CC01</option>
                    <option value="CC02">CC02</option>
                    <option value="CC03">CC03</option>
                    <option value="CC04">CC04</option>
                    <option value="CC05">CC05</option>
                  </Select>
                </Field>
                <Button variant="secondary" onClick={() => cancelPreviewMutation.mutate()} disabled={!canPreviewCancel}>
                  {cancelPreviewMutation.isPending ? '预检中…' : '取消预检'}
                </Button>
                {cancelPreview ? (
                  <div className="message" data-role={cancelPreview.cancelAllowed ? 'agent' : 'user'}>
                    <strong>{cancelPreview.cancelAllowed ? '预检通过' : '不允许取消'}</strong>
                    <div>当前状态：{sanitizeDisplayText(cancelPreview.currentStatusLabel || cancelPreview.currentStatus || '未知')}</div>
                    {cancelPreview.reason ? <div>原因：{sanitizeDisplayText(cancelPreview.reason)}</div> : null}
                  </div>
                ) : null}
                {cancelPreview?.cancelAllowed ? (
                  <label className="checkbox-row">
                    <input type="checkbox" checked={confirmCancel} onChange={(event) => setConfirmCancel(event.target.checked)} />
                    <span>我确认取消请求已由人工核对，且理解 Nexus 工单不会自动关闭。</span>
                  </label>
                ) : null}
                <Button variant="danger" onClick={() => cancelMutation.mutate()} disabled={!canConfirmCancel}>
                  {cancelMutation.isPending ? '提交中…' : '确认提交取消'}
                </Button>
              </div>
            </CardBody>
          </Card>
        </div>
      </CardBody>
    </Card>
  )
}

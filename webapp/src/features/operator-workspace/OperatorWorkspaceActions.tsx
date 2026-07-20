import {
  Alert,
  AlertTitle,
  Box,
  Button,
  CircularProgress,
  Divider,
  MenuItem,
  Paper,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import { useEffect, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import {
  OperatorErrorNotice,
  OperatorSectionHeading,
  OperatorTechnicalDisclosure,
  operatorAlertSeverity,
} from '@/app/OperatorPresentation'
import { agentRoutingApi } from '@/lib/agentRoutingApi'
import {
  finiteNumber,
  recordValue,
  sanitizeDisplayText,
  stringValue,
} from '@/lib/format'
import type { OperatorWorkspaceThread } from '@/lib/operatorWorkspaceApi'
import { operatorWorkspaceApi } from '@/lib/operatorWorkspaceApi'
import type { UnifiedOperatorQueueItem } from '@/lib/operatorWorkspaceTypes'
import { outcomePresentation } from '@/lib/operatorWorkspacePresentation'
import type { SpeedafCancelPreviewResponse } from '@/lib/speedafTypes'
import { supportApi } from '@/lib/supportApi'
import {
  cancelPreviewFingerprint,
  hasWorkspaceCapability,
} from './operatorWorkspaceState'

type SpeedafActionKind = 'none' | 'waybill_lookup' | 'work_order' | 'address_update' | 'cancel'
type ActionResultEnvelope = { kind: SpeedafActionKind; result: Record<string, unknown> }
type CancelPreviewBinding = { fingerprint: string; result: SpeedafCancelPreviewResponse }
type ConversationOutcome = 'human_resolved' | 'no_action_required' | 'customer_abandoned' | 'unresolved'

const CONVERSATION_OUTCOMES: Array<{ value: ConversationOutcome; label: string }> = [
  { value: 'human_resolved', label: '人工在线解决' },
  { value: 'no_action_required', label: '无需后续处理' },
  { value: 'customer_abandoned', label: '客户离开' },
  { value: 'unresolved', label: '未解决结束' },
]

function actionDisabledReason({
  action,
  item,
  capabilities,
  waybill,
  caller,
  description,
  whatsappPhone,
}: {
  action: SpeedafActionKind
  item: UnifiedOperatorQueueItem
  capabilities: Set<string>
  waybill: string
  caller: string
  description: string
  whatsappPhone: string
}) {
  if (action === 'none') return '请先选择操作'
  if (!item.ticket_id) return '当前任务没有可操作的工单'
  if (action === 'waybill_lookup') return caller.trim() ? '' : '缺少客户电话'
  if (!waybill.trim()) return '缺少运单'
  if (!caller.trim()) return '缺少客户电话'
  if (action === 'work_order' && !hasWorkspaceCapability(capabilities, 'tool:speedaf.work_order.create:write')) return '无权创建催派工单'
  if (action === 'address_update' && !hasWorkspaceCapability(capabilities, 'tool:speedaf.order.update_address:write')) return '无权更新联系号码'
  if (action === 'cancel' && !hasWorkspaceCapability(capabilities, 'tool:speedaf.order.cancel:write')) return '无权申请取消'
  if (action === 'work_order' && !description.trim()) return '缺少催派说明'
  if (action === 'address_update' && !whatsappPhone.trim()) return '缺少确认后的联系号码'
  return ''
}

export function OperatorWorkspaceActions({
  item,
  thread,
  capabilities,
  onRefresh,
}: {
  item: UnifiedOperatorQueueItem
  thread: OperatorWorkspaceThread | null
  capabilities: Set<string>
  onRefresh: () => Promise<void>
}) {
  const [action, setAction] = useState<SpeedafActionKind>('none')
  const [waybill, setWaybill] = useState('')
  const [caller, setCaller] = useState('')
  const [countryCode, setCountryCode] = useState(item.country_code || 'CH')
  const [description, setDescription] = useState('')
  const [whatsappPhone, setWhatsappPhone] = useState('')
  const [reasonCode, setReasonCode] = useState('CC01')
  const [cancelPreview, setCancelPreview] = useState<CancelPreviewBinding | null>(null)
  const [conversationOutcome, setConversationOutcome] = useState<ConversationOutcome>('human_resolved')
  const [conversationCloseNote, setConversationCloseNote] = useState('')

  useEffect(() => {
    setAction('none')
    setWaybill('')
    setCaller(thread?.visitor?.phone || '')
    setWhatsappPhone(thread?.visitor?.phone || '')
    setCountryCode(item.country_code || 'CH')
    setDescription('')
    setReasonCode('CC01')
    setCancelPreview(null)
    setConversationOutcome('human_resolved')
    setConversationCloseNote('')
  }, [item.queue_id, item.country_code, thread?.visitor?.phone])

  const invalidateCancelPreview = () => setCancelPreview(null)
  const currentCancelFingerprint = cancelPreviewFingerprint(item.ticket_id, waybill, caller, reasonCode)

  const handoffMutation = useMutation({
    mutationFn: async (kind: 'accept' | 'force' | 'release' | 'resume' | 'decline') => {
      const handoff = thread?.handoff
      if (kind === 'accept' && handoff?.id) {
        return item.ticket_id
          ? supportApi.webchatAcceptHandoff(handoff.id, 'Accepted from Operator Workspace')
          : agentRoutingApi.acceptHandoff(handoff.id)
      }
      if (kind === 'force' && item.ticket_id) return supportApi.webchatForceTakeover(item.ticket_id, { reason_code: 'operator_takeover', note: 'Operator Workspace takeover' })
      if (kind === 'release' && handoff?.id) return supportApi.webchatReleaseHandoff(handoff.id, 'Released from Operator Workspace')
      if (kind === 'resume' && handoff?.id) return supportApi.webchatResumeAi(handoff.id, 'Resume AI from Operator Workspace')
      if (kind === 'decline' && handoff?.id) return operatorWorkspaceApi.declineHandoff(handoff.id, 'operator_capacity', 'Declined from Operator Workspace')
      throw new Error('当前接手操作不可执行')
    },
    onSuccess: onRefresh,
  })

  const conversationCloseMutation = useMutation({
    mutationFn: async () => {
      if (item.ticket_id || !thread?.conversation_id) throw new Error('当前不是可结束的独立会话')
      return agentRoutingApi.closeConversation(
        thread.conversation_id,
        conversationOutcome,
        conversationCloseNote.trim() || undefined,
      )
    },
    onSuccess: async () => {
      setConversationCloseNote('')
      await onRefresh()
    },
  })

  const actionMutation = useMutation({
    mutationFn: async (): Promise<ActionResultEnvelope> => {
      if (!item.ticket_id) throw new Error('当前任务没有可操作的工单')
      if (action === 'waybill_lookup') {
        const result = await supportApi.querySpeedafWaybills(item.ticket_id, {
          callerID: caller.trim(),
          countryCode: countryCode.trim().toUpperCase(),
        })
        return { kind: action, result: recordValue(result) }
      }
      if (action === 'work_order') {
        const result = await supportApi.createSpeedafWorkOrder(item.ticket_id, {
          waybillCode: waybill.trim().toUpperCase(),
          callerID: caller.trim(),
          workOrderType: 'WT0103-05',
          description: description.trim(),
        })
        return { kind: action, result: recordValue(result) }
      }
      if (action === 'address_update') {
        const result = await supportApi.submitSpeedafAddressUpdate(item.ticket_id, {
          waybillCode: waybill.trim().toUpperCase(),
          callerID: caller.trim(),
          whatsAppPhone: whatsappPhone.trim(),
        })
        return { kind: action, result: recordValue(result) }
      }
      throw new Error('请选择操作')
    },
    onSuccess: onRefresh,
  })

  const cancelPreviewMutation = useMutation({
    mutationFn: async () => {
      if (!item.ticket_id) throw new Error('当前任务没有可操作的工单')
      const fingerprint = currentCancelFingerprint
      const result = await supportApi.previewSpeedafCancel(item.ticket_id, {
        waybillCode: waybill.trim().toUpperCase(),
        callerID: caller.trim(),
        reasonCode,
      })
      return { fingerprint, result }
    },
    onSuccess: setCancelPreview,
  })

  const cancelConfirmMutation = useMutation({
    mutationFn: async (): Promise<ActionResultEnvelope> => {
      if (!item.ticket_id) throw new Error('当前任务没有可操作的工单')
      if (!cancelPreview || cancelPreview.fingerprint !== currentCancelFingerprint) throw new Error('检查结果已失效，请重新检查')
      if (!cancelPreview.result.cancelAllowed || !cancelPreview.result.confirmToken) throw new Error('当前不可申请取消')
      const result = await supportApi.confirmSpeedafCancel(item.ticket_id, {
        waybillCode: waybill.trim().toUpperCase(),
        callerID: caller.trim(),
        reasonCode,
        confirmToken: cancelPreview.result.confirmToken,
      })
      return { kind: 'cancel', result: recordValue(result) }
    },
    onSuccess: async () => {
      setCancelPreview(null)
      await onRefresh()
    },
  })

  const disabledReason = actionDisabledReason({ action, item, capabilities, waybill, caller, description, whatsappPhone })
  const busy = handoffMutation.isPending || conversationCloseMutation.isPending || actionMutation.isPending || cancelPreviewMutation.isPending || cancelConfirmMutation.isPending
  const actionError = handoffMutation.error || conversationCloseMutation.error || actionMutation.error || cancelPreviewMutation.error || cancelConfirmMutation.error
  const envelope = actionMutation.data || cancelConfirmMutation.data
  const resultRecord = envelope?.result ?? {}
  const resultPresentation = envelope ? outcomePresentation(resultRecord.status, resultRecord.message) : null
  const candidates = Array.isArray(resultRecord.candidates) ? resultRecord.candidates.map(recordValue) : []
  const handoff = thread?.handoff
  const canAcceptHandoff = hasWorkspaceCapability(capabilities, 'webchat.handoff.accept')
  const canDeclineHandoff = hasWorkspaceCapability(capabilities, 'webchat.handoff.decline')
  const canForceTakeover = hasWorkspaceCapability(capabilities, 'webchat.handoff.force_takeover')
  const canReleaseHandoff = hasWorkspaceCapability(capabilities, 'webchat.handoff.release')
  const canResumeAi = hasWorkspaceCapability(capabilities, 'webchat.handoff.resume_ai')
  const takeoverKind = handoff?.can_accept && canAcceptHandoff
    ? 'accept'
    : handoff?.can_force_takeover && canForceTakeover
      ? 'force'
      : null
  const canCloseConversation = Boolean(!item.ticket_id && thread?.conversation_id && handoff?.can_reply)
  const jobId = finiteNumber(resultRecord.jobId)

  return (
    <Box id="workspace-actions" component="section" aria-labelledby="operator-actions-title" tabIndex={-1}>
      <OperatorSectionHeading id="operator-actions-title" title="下一步" />
      <Divider sx={{ my: 2 }} />
      <Stack spacing={2.5}>
        {(handoff?.can_accept || handoff?.can_force_takeover || handoff?.can_decline || handoff?.can_release || handoff?.can_resume_ai) ? (
          <Box>
            <Typography component="h3" variant="subtitle1">人工接管</Typography>
            <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: 'wrap', mt: 1 }}>
              {handoff?.can_accept || handoff?.can_force_takeover ? (
                <Button
                  variant="contained"
                  disabled={!takeoverKind || handoffMutation.isPending}
                  startIcon={handoffMutation.isPending ? <CircularProgress color="inherit" size={16} /> : undefined}
                  onClick={() => { if (takeoverKind) handoffMutation.mutate(takeoverKind) }}
                >
                  接受会话
                </Button>
              ) : null}
              {handoff?.can_decline ? <Button color="inherit" variant="outlined" disabled={!canDeclineHandoff || handoffMutation.isPending} onClick={() => handoffMutation.mutate('decline')}>暂不处理</Button> : null}
              {handoff?.can_release ? <Button color="inherit" disabled={!canReleaseHandoff || handoffMutation.isPending} onClick={() => handoffMutation.mutate('release')}>转回待处理</Button> : null}
              {handoff?.can_resume_ai ? <Button color="inherit" disabled={!canResumeAi || handoffMutation.isPending} onClick={() => handoffMutation.mutate('resume')}>恢复自动回复</Button> : null}
            </Stack>
            {handoff?.reason_text ? <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>转接原因：{sanitizeDisplayText(handoff.reason_text)}</Typography> : null}
          </Box>
        ) : null}

        {canCloseConversation ? (
          <Box>
            <Typography component="h3" variant="subtitle1">结束当前会话</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
              结束会话会释放一个接线名额。会话结果与工单结案相互独立。
            </Typography>
            <Stack spacing={1.25} sx={{ mt: 1.25 }}>
              <TextField
                select
                label="会话结果"
                value={conversationOutcome}
                onChange={(event) => setConversationOutcome(event.target.value as ConversationOutcome)}
              >
                {CONVERSATION_OUTCOMES.map((option) => <MenuItem key={option.value} value={option.value}>{option.label}</MenuItem>)}
              </TextField>
              <TextField
                label="处理说明"
                value={conversationCloseNote}
                onChange={(event) => setConversationCloseNote(event.target.value)}
                multiline
                minRows={2}
                slotProps={{ htmlInput: { maxLength: 2000 } }}
              />
              <Button
                color={conversationOutcome === 'unresolved' ? 'warning' : 'success'}
                variant="contained"
                disabled={busy}
                startIcon={conversationCloseMutation.isPending ? <CircularProgress color="inherit" size={16} /> : undefined}
                onClick={() => conversationCloseMutation.mutate()}
              >
                结束会话并释放名额
              </Button>
            </Stack>
          </Box>
        ) : null}

        {item.ticket_id ? (
          <Box>
            <Typography component="h3" variant="subtitle1">物流操作</Typography>
            <Stack spacing={1.5} sx={{ mt: 1.25 }}>
              <TextField
                select
                label="选择操作"
                value={action}
                onChange={(event) => {
                  setAction(event.target.value as SpeedafActionKind)
                  invalidateCancelPreview()
                  actionMutation.reset()
                  cancelConfirmMutation.reset()
                }}
              >
                <MenuItem value="none">请选择操作</MenuItem>
                <MenuItem value="waybill_lookup">按电话查询运单</MenuItem>
                <MenuItem value="work_order">创建催派工单</MenuItem>
                <MenuItem value="address_update">更新联系号码</MenuItem>
                <MenuItem value="cancel">申请取消订单</MenuItem>
              </TextField>
              {action !== 'none' ? (
                <>
                  {action !== 'waybill_lookup' ? <TextField label="运单" required value={waybill} onChange={(event) => { setWaybill(event.target.value.toUpperCase()); invalidateCancelPreview() }} autoComplete="off" /> : null}
                  <TextField label="客户电话" required type="tel" value={caller} onChange={(event) => { setCaller(event.target.value); invalidateCancelPreview() }} autoComplete="off" />
                  {action === 'waybill_lookup' ? <TextField label="国家代码" required value={countryCode} onChange={(event) => setCountryCode(event.target.value.toUpperCase())} /> : null}
                  {action === 'work_order' ? <TextField label="催派说明" required value={description} onChange={(event) => setDescription(event.target.value)} multiline minRows={3} /> : null}
                  {action === 'address_update' ? <TextField label="确认后的联系号码" required type="tel" value={whatsappPhone} onChange={(event) => setWhatsappPhone(event.target.value)} /> : null}
                  {action === 'cancel' ? (
                    <TextField select label="取消原因" required value={reasonCode} onChange={(event) => { setReasonCode(event.target.value); invalidateCancelPreview() }}>
                      <MenuItem value="CC01">派送太慢</MenuItem>
                      <MenuItem value="CC02">快递员服务问题</MenuItem>
                      <MenuItem value="CC03">不支持验货</MenuItem>
                      <MenuItem value="CC04">不支持部分签收</MenuItem>
                      <MenuItem value="CC05">其他原因</MenuItem>
                    </TextField>
                  ) : null}
                </>
              ) : null}
              {disabledReason ? <Alert severity="info" variant="outlined">{disabledReason}</Alert> : null}
              {candidates.length ? (
                <Paper variant="outlined" sx={{ p: 1.5 }}>
                  <Typography variant="subtitle2">候选运单</Typography>
                  <Stack divider={<Divider flexItem />} sx={{ mt: 1 }}>
                    {candidates.map((candidate) => {
                      const candidateWaybill = stringValue(candidate.waybillCode)
                      return (
                        <Stack key={candidateWaybill} direction="row" spacing={1} sx={{ alignItems: 'center', justifyContent: 'space-between', py: 1 }}>
                          <Typography component="code" variant="body2">{sanitizeDisplayText(candidateWaybill)}</Typography>
                          <Button size="small" color="inherit" variant="outlined" onClick={() => { setWaybill(candidateWaybill); setAction('work_order'); invalidateCancelPreview() }}>填入催派</Button>
                        </Stack>
                      )
                    })}
                  </Stack>
                </Paper>
              ) : null}
              {cancelPreview ? (
                <Alert severity={cancelPreview.result.cancelAllowed ? 'info' : 'warning'} variant="outlined" role="status">
                  <AlertTitle>{cancelPreview.result.cancelAllowed ? '可以申请取消' : '当前不可取消'}</AlertTitle>
                  {sanitizeDisplayText(cancelPreview.result.currentStatusLabel || cancelPreview.result.reasonLabel || '未返回原因')}
                  <Typography variant="caption" sx={{ display: 'block', mt: 0.75 }}>修改运单、电话或原因后需重新检查。</Typography>
                </Alert>
              ) : null}
              {resultPresentation ? (
                <Alert severity={operatorAlertSeverity(resultPresentation.tone)} variant="outlined" role="status">
                  <AlertTitle>{resultPresentation.label}</AlertTitle>
                  {resultPresentation.detail}
                  {jobId !== null ? (
                    <Box sx={{ mt: 1 }}>
                      <OperatorTechnicalDisclosure title="处理编号" compact>
                        <Typography component="code" variant="caption">#{jobId}</Typography>
                      </OperatorTechnicalDisclosure>
                    </Box>
                  ) : null}
                </Alert>
              ) : null}
              <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: 'wrap' }}>
                {action === 'cancel' ? (
                  <>
                    <Button color="inherit" variant="outlined" disabled={Boolean(disabledReason) || busy} startIcon={cancelPreviewMutation.isPending ? <CircularProgress color="inherit" size={16} /> : undefined} onClick={() => cancelPreviewMutation.mutate()}>
                      检查是否可取消
                    </Button>
                    <Button color="error" variant="contained" disabled={!cancelPreview?.result.cancelAllowed || !cancelPreview.result.confirmToken || cancelPreview.fingerprint !== currentCancelFingerprint || busy} startIcon={cancelConfirmMutation.isPending ? <CircularProgress color="inherit" size={16} /> : undefined} onClick={() => cancelConfirmMutation.mutate()}>
                      确认申请取消
                    </Button>
                  </>
                ) : action !== 'none' ? (
                  <Button variant={action === 'work_order' ? 'contained' : 'outlined'} color={action === 'work_order' ? 'primary' : 'inherit'} disabled={Boolean(disabledReason) || busy} startIcon={actionMutation.isPending ? <CircularProgress color="inherit" size={16} /> : undefined} onClick={() => actionMutation.mutate()}>
                    {action === 'waybill_lookup' ? '查询运单' : action === 'work_order' ? '创建催派工单' : '更新联系号码'}
                  </Button>
                ) : null}
              </Stack>
            </Stack>
          </Box>
        ) : (
          <Alert severity="info" variant="outlined">
            当前是实时会话，不显示工单和物流写操作。需要后续业务处理时，应先通过受控流程创建工单。
          </Alert>
        )}

        {actionError ? <OperatorErrorNotice title="操作失败" error={actionError} fallback="请稍后重试" /> : null}
      </Stack>
    </Box>
  )
}

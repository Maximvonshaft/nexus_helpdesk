import ExpandMoreRoundedIcon from '@mui/icons-material/ExpandMoreRounded'
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
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
  MenuItem,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from '@mui/material'
import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  OperatorEmptyState,
  OperatorErrorNotice,
  OperatorFactGrid,
  operatorToneColor,
} from '@/app/OperatorPresentation'
import { operationalPresentation } from '@/domain/operationalPresentation'
import { formatDateTime, sanitizeDisplayText } from '@/lib/format'
import { supportApi } from '@/lib/supportApi'
import { healthPresentation } from '@/lib/supportStatus'
import type { ChannelOnboardingTask } from '@/lib/channelControlTypes'
import type { ChannelAccount } from '@/lib/types'

type PendingTaskAction = 'complete' | 'fail' | 'cancel' | null

type OnboardingDraft = {
  provider: string
  targetSlot: string
  displayName: string
  accountBinding: string
  externalAccountId: string
}

const emptyDraft: OnboardingDraft = {
  provider: 'whatsapp',
  targetSlot: '',
  displayName: '',
  accountBinding: '',
  externalAccountId: '',
}

function providerLabel(value: string) {
  if (value === 'webchat') return '网页客服'
  if (value === 'whatsapp') return 'WhatsApp'
  if (value === 'email') return '邮件'
  if (value === 'voice') return '语音'
  return sanitizeDisplayText(value)
}

function maskPhone(value: string | null | undefined) {
  const text = String(value || '').trim()
  if (!text) return '未返回'
  const digits = text.replace(/\D/g, '')
  return digits.length > 4 ? `•••• ${digits.slice(-4)}` : '已配置'
}

function taskStatus(task: ChannelOnboardingTask) {
  if (task.status === 'completed') return { tone: 'success' as const, label: '已完成' }
  if (task.status === 'failed') return { tone: 'danger' as const, label: '需要修复' }
  if (task.status === 'cancelled') return { tone: 'default' as const, label: '已取消' }
  if (task.status === 'in_progress') return { tone: 'warning' as const, label: '处理中' }
  return { tone: 'warning' as const, label: '待开始' }
}

function canStart(task: ChannelOnboardingTask) {
  return task.status === 'pending'
}

function canSettle(task: ChannelOnboardingTask) {
  return task.status === 'pending' || task.status === 'in_progress'
}

export function ChannelsPage() {
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<OnboardingDraft>(emptyDraft)
  const [selectedTask, setSelectedTask] = useState<ChannelOnboardingTask | null>(null)
  const [pendingAction, setPendingAction] = useState<PendingTaskAction>(null)
  const [failureReason, setFailureReason] = useState('')

  const accounts = useQuery({
    queryKey: ['canonicalChannelAccounts'],
    queryFn: supportApi.channelAccounts,
    refetchInterval: 30_000,
    retry: false,
  })
  const tasks = useQuery({
    queryKey: ['canonicalChannelOnboardingTasks'],
    queryFn: () => supportApi.channelOnboardingTasks({ limit: 50 }),
    refetchInterval: 15_000,
    retry: false,
  })
  const activeAccounts = useMemo(
    () => (accounts.data ?? []).filter((item: ChannelAccount) => item.is_active),
    [accounts.data],
  )
  const whatsappAccount = useMemo(
    () => activeAccounts.find((item: ChannelAccount) => item.provider === 'whatsapp'),
    [activeAccounts],
  )
  const whatsappStatus = useQuery({
    queryKey: ['canonicalWhatsappStatus', whatsappAccount?.account_id],
    queryFn: () => supportApi.whatsappNativeStatus(whatsappAccount?.account_id || ''),
    enabled: Boolean(whatsappAccount?.account_id),
    refetchInterval: 10_000,
    retry: false,
  })
  const whatsappHealth = healthPresentation(
    whatsappStatus.data?.channel_health_status
      || whatsappStatus.data?.status
      || whatsappAccount?.health_status,
  )

  const invalidateChannels = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['canonicalChannelAccounts'] }),
      queryClient.invalidateQueries({ queryKey: ['canonicalChannelOnboardingTasks'] }),
      queryClient.invalidateQueries({ queryKey: ['canonicalWhatsappStatus'] }),
    ])
  }

  const createTask = useMutation({
    mutationFn: () => supportApi.createChannelOnboardingTask({
      provider: draft.provider.trim(),
      target_slot: draft.targetSlot.trim() || null,
      desired_display_name: draft.displayName.trim() || null,
      desired_channel_account_binding: draft.accountBinding.trim() || null,
      external_channel_account_id: draft.externalAccountId.trim() || null,
    }),
    onSuccess: async () => {
      setDraft(emptyDraft)
      await invalidateChannels()
    },
  })

  const startTask = useMutation({
    mutationFn: (taskId: number) => supportApi.startChannelOnboardingTask(taskId),
    onSuccess: invalidateChannels,
  })

  const settleTask = useMutation({
    mutationFn: async () => {
      if (!selectedTask || !pendingAction) throw new Error('未选择操作')
      if (pendingAction === 'complete') {
        return supportApi.completeChannelOnboardingTask(selectedTask.id, {
          external_channel_account_id: selectedTask.external_channel_account_id || null,
          desired_channel_account_binding: selectedTask.desired_channel_account_binding || null,
        })
      }
      if (pendingAction === 'fail') {
        if (!failureReason.trim()) throw new Error('请填写失败原因')
        return supportApi.failChannelOnboardingTask(selectedTask.id, failureReason.trim())
      }
      return supportApi.cancelChannelOnboardingTask(selectedTask.id)
    },
    onSuccess: async () => {
      setSelectedTask(null)
      setPendingAction(null)
      setFailureReason('')
      await invalidateChannels()
    },
  })

  const actionError = createTask.error || startTask.error || settleTask.error
  const createReady = Boolean(draft.provider.trim() && (draft.displayName.trim() || draft.targetSlot.trim() || draft.externalAccountId.trim()))
  const closeTaskDialog = () => {
    if (settleTask.isPending) return
    setSelectedTask(null)
    setPendingAction(null)
    setFailureReason('')
  }

  return (
    <Box component="main" sx={{ p: { xs: 1.5, md: 2.5 } }}>
      <Stack
        direction={{ xs: 'column', sm: 'row' }}
        spacing={2}
        sx={{
          alignItems: { xs: 'stretch', sm: 'flex-start' },
          justifyContent: "space-between",
          mb: 2.5
        }}>
        <Typography component="h1" variant="h1">渠道管理</Typography>
        {accounts.isFetching || tasks.isFetching ? <CircularProgress size={22} aria-label="正在刷新" /> : null}
      </Stack>
      {actionError ? <Box sx={{ mb: 2 }}><OperatorErrorNotice title="操作失败" error={actionError} fallback="请稍后重试" /></Box> : null}
      <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', xl: 'minmax(0, 2fr) minmax(300px, 1fr)' } }}>
        <Paper component="section" variant="outlined" aria-labelledby="channel-accounts-title" sx={{ minWidth: 0, p: 2 }}>
          <Stack
            direction="row"
            spacing={1}
            sx={{
              alignItems: "center",
              justifyContent: "space-between"
            }}>
            <Typography id="channel-accounts-title" component="h2" variant="h3">已启用渠道</Typography>
            <Typography
              variant="body2"
              sx={{
                color: "text.secondary",
                fontVariantNumeric: 'tabular-nums'
              }}>{activeAccounts.length} 个账号</Typography>
          </Stack>
          <Divider sx={{ my: 2 }} />
          {accounts.isError ? (
            <OperatorErrorNotice title="无法读取渠道账号" error={accounts.error} fallback="请稍后重试" />
          ) : activeAccounts.length ? (
            <TableContainer>
              <Table size="small" aria-label="当前启用的渠道账号">
                <TableHead><TableRow><TableCell>渠道</TableCell><TableCell>账号名称</TableCell><TableCell>状态</TableCell><TableCell align="right">优先级</TableCell><TableCell>最近更新</TableCell></TableRow></TableHead>
                <TableBody>
                  {activeAccounts.map((item) => {
                    const health = healthPresentation(item.health_status)
                    return (
                      <TableRow key={item.id} hover>
                        <TableCell>{providerLabel(item.provider)}</TableCell>
                        <TableCell>{sanitizeDisplayText(item.display_name || `${providerLabel(item.provider)} 账号`)}</TableCell>
                        <TableCell><Chip color={operatorToneColor(health.tone)} label={health.label} /></TableCell>
                        <TableCell align="right">{item.priority}</TableCell>
                        <TableCell>{formatDateTime(item.updated_at)}</TableCell>
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>
            </TableContainer>
          ) : <OperatorEmptyState title="暂无已启用渠道" description="请先创建接入任务" />}
        </Paper>

        <Paper component="aside" variant="outlined" aria-labelledby="whatsapp-health-title" sx={{ minWidth: 0, p: 2, alignSelf: 'start' }}>
          <Stack
            direction="row"
            spacing={1}
            sx={{
              alignItems: "center",
              justifyContent: "space-between"
            }}>
            <Typography id="whatsapp-health-title" component="h2" variant="h3">WhatsApp 状态</Typography>
            <Chip color={operatorToneColor(whatsappHealth.tone)} label={whatsappHealth.label} />
          </Stack>
          <Divider sx={{ my: 2 }} />
          {!whatsappAccount ? <OperatorEmptyState title="未启用 WhatsApp" description="暂无账号" /> : whatsappStatus.isError ? (
            <OperatorErrorNotice title="无法读取 WhatsApp 状态" error={whatsappStatus.error} fallback="请稍后重试" />
          ) : (
            <Stack spacing={1.5}>
              <OperatorFactGrid facts={[
                ['状态', whatsappHealth.label],
                ['绑定号码', maskPhone(whatsappStatus.data?.phone_number)],
                ['登录状态', sanitizeDisplayText(whatsappStatus.data?.qr_status || '状态未知')],
                ['最近连接', whatsappStatus.data?.last_connected_at ? formatDateTime(whatsappStatus.data.last_connected_at) : '暂无'],
              ]} />
              {whatsappStatus.data?.last_error_message ? <Alert severity="error" variant="outlined">{sanitizeDisplayText(whatsappStatus.data.last_error_message)}</Alert> : null}
              <Accordion disableGutters variant="outlined" sx={{ '&:before': { display: 'none' } }}>
                <AccordionSummary expandIcon={<ExpandMoreRoundedIcon />}><Typography variant="subtitle2">系统信息</Typography></AccordionSummary>
                <AccordionDetails sx={{ borderTop: 1, borderColor: 'divider' }}>
                  <OperatorFactGrid facts={[
                    ['服务提供方', <Box component="code">{sanitizeDisplayText(whatsappAccount.provider)}</Box>],
                    ['外部账号编号', <Box component="code">{sanitizeDisplayText(whatsappAccount.account_id)}</Box>],
                    ['重连次数', whatsappStatus.data?.reconnect_count ?? 0],
                    ['错误编号', sanitizeDisplayText(whatsappStatus.data?.last_error_code || '无')],
                  ]} />
                </AccordionDetails>
              </Accordion>
            </Stack>
          )}
        </Paper>
      </Box>
      <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', xl: 'minmax(300px, 0.8fr) minmax(0, 1.2fr)' }, mt: 2 }}>
        <Paper component="section" variant="outlined" aria-labelledby="channel-onboarding-create-title" sx={{ p: 2 }}>
          <Typography id="channel-onboarding-create-title" component="h2" variant="h3">新建接入任务</Typography>
          <Stack spacing={1.5} sx={{ mt: 2 }}>
            <TextField select label="渠道" required value={draft.provider} onChange={(event) => setDraft((current) => ({ ...current, provider: event.target.value }))}>
              <MenuItem value="whatsapp">WhatsApp</MenuItem><MenuItem value="email">邮件</MenuItem><MenuItem value="webchat">网页客服</MenuItem><MenuItem value="voice">语音</MenuItem>
            </TextField>
            <TextField label="接入位置" helperText="内部接入位置，如 ch-primary" value={draft.targetSlot} onChange={(event) => setDraft((current) => ({ ...current, targetSlot: event.target.value }))} />
            <TextField label="账号名称" value={draft.displayName} onChange={(event) => setDraft((current) => ({ ...current, displayName: event.target.value }))} />
            <TextField label="绑定账号或号码" value={draft.accountBinding} onChange={(event) => setDraft((current) => ({ ...current, accountBinding: event.target.value }))} />
            <TextField label="外部账号编号" value={draft.externalAccountId} onChange={(event) => setDraft((current) => ({ ...current, externalAccountId: event.target.value }))} />
            <Button variant="contained" disabled={!createReady || createTask.isPending} startIcon={createTask.isPending ? <CircularProgress color="inherit" size={16} /> : undefined} onClick={() => createTask.mutate()}>
              {createTask.isPending ? '创建中…' : '创建接入任务'}
            </Button>
            <Typography variant="caption" sx={{
              color: "text.secondary"
            }}>验证通过后才能确认完成。</Typography>
          </Stack>
        </Paper>

        <Paper component="section" variant="outlined" aria-labelledby="channel-onboarding-list-title" sx={{ minWidth: 0, p: 2 }}>
          <Stack
            direction="row"
            spacing={1}
            sx={{
              alignItems: "center",
              justifyContent: "space-between"
            }}>
            <Typography id="channel-onboarding-list-title" component="h2" variant="h3">接入任务</Typography>
            <Typography
              variant="body2"
              sx={{
                color: "text.secondary",
                fontVariantNumeric: 'tabular-nums'
              }}>{tasks.data?.total ?? 0} 项</Typography>
          </Stack>
          <Divider sx={{ my: 2 }} />
          {tasks.isError ? <OperatorErrorNotice title="无法读取接入任务" error={tasks.error} fallback="请稍后重试" /> : !(tasks.data?.tasks.length) ? (
            <OperatorEmptyState title="暂无任务" description="可新建接入任务" />
          ) : (
            <Stack divider={<Divider flexItem />}>
              {tasks.data.tasks.map((task) => {
                const status = taskStatus(task)
                const result = operationalPresentation(task.status, task.last_error)
                return (
                  <Stack
                    component="article"
                    key={task.id}
                    direction={{ xs: 'column', md: 'row' }}
                    spacing={2}
                    sx={{
                      justifyContent: "space-between",
                      py: 1.5
                    }}>
                    <Box sx={{ minWidth: 0 }}>
                      <Stack
                        direction="row"
                        spacing={1}
                        useFlexGap
                        sx={{
                          alignItems: "center",
                          flexWrap: "wrap"
                        }}>
                        <Typography variant="subtitle2">{providerLabel(task.provider)} · {sanitizeDisplayText(task.desired_display_name || task.target_slot || `任务 #${task.id}`)}</Typography>
                        <Chip color={operatorToneColor(status.tone)} label={status.label} />
                      </Stack>
                      <Typography
                        variant="body2"
                        sx={{
                          color: "text.secondary",
                          mt: 0.75
                        }}>{task.last_error ? sanitizeDisplayText(task.last_error) : result.detail || '等待处理'}</Typography>
                      <Typography variant="caption" sx={{
                        color: "text.disabled"
                      }}>更新于 {formatDateTime(task.updated_at)}</Typography>
                    </Box>
                    <Stack
                      direction="row"
                      spacing={1}
                      useFlexGap
                      sx={{
                        flexWrap: "wrap",
                        flexShrink: 0
                      }}>
                      {canStart(task) ? <Button size="small" variant="outlined" color="inherit" disabled={startTask.isPending} onClick={() => startTask.mutate(task.id)}>开始处理</Button> : null}
                      {canSettle(task) ? (
                        <>
                          <Button size="small" variant="contained" onClick={() => { setSelectedTask(task); setPendingAction('complete') }}>确认完成</Button>
                          <Button size="small" variant="outlined" color="error" onClick={() => { setSelectedTask(task); setPendingAction('fail') }}>记录失败</Button>
                          <Button size="small" color="inherit" onClick={() => { setSelectedTask(task); setPendingAction('cancel') }}>取消任务</Button>
                        </>
                      ) : null}
                    </Stack>
                  </Stack>
                );
              })}
            </Stack>
          )}
        </Paper>
      </Box>
      <Dialog open={Boolean(selectedTask && pendingAction)} onClose={closeTaskDialog} aria-labelledby="channel-task-dialog-title">
        <DialogTitle id="channel-task-dialog-title">{pendingAction === 'complete' ? '确认任务完成？' : pendingAction === 'fail' ? '记录任务失败？' : '取消任务？'}</DialogTitle>
        <DialogContent>
          <DialogContentText>{pendingAction === 'complete' ? '确认已完成账号和绑定验证。' : pendingAction === 'fail' ? '请填写失败原因。' : '任务将停止，历史记录会保留。'}</DialogContentText>
          {pendingAction === 'fail' ? <TextField label="失败原因" required value={failureReason} onChange={(event) => setFailureReason(event.target.value)} multiline minRows={4} placeholder="填写具体失败原因" sx={{ mt: 2 }} /> : null}
        </DialogContent>
        <DialogActions>
          <Button color="inherit" disabled={settleTask.isPending} onClick={closeTaskDialog}>返回</Button>
          <Button color={pendingAction === 'complete' ? 'primary' : 'error'} variant="contained" disabled={settleTask.isPending || (pendingAction === 'fail' && !failureReason.trim())} startIcon={settleTask.isPending ? <CircularProgress color="inherit" size={16} /> : undefined} onClick={() => settleTask.mutate()}>
            {pendingAction === 'complete' ? '确认完成' : pendingAction === 'fail' ? '记录失败' : '确认取消'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}

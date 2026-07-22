import PauseCircleRoundedIcon from '@mui/icons-material/PauseCircleRounded'
import PlayCircleRoundedIcon from '@mui/icons-material/PlayCircleRounded'
import RocketLaunchRoundedIcon from '@mui/icons-material/RocketLaunchRounded'
import TuneRoundedIcon from '@mui/icons-material/TuneRounded'
import {
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
  MenuItem,
  Paper,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { OperatorEmptyState, OperatorErrorNotice, OperatorTechnicalDisclosure } from '@/app/OperatorPresentation'
import { governanceApi } from '@/lib/governanceApi'
import type { AgentControlSnapshot, AgentDeployment } from '@/lib/types'

type DeliveryAction = 'start' | 'adjust' | 'pause' | 'promote' | null

export function ReleaseDeliveryPanel({ snapshot, canDeploy }: { snapshot: AgentControlSnapshot; canDeploy: boolean }) {
  const queryClient = useQueryClient()
  const deployments = snapshot.deployments
  const [deploymentId, setDeploymentId] = useState<number | ''>(deployments[0]?.id || '')
  const [releaseId, setReleaseId] = useState<number | ''>('')
  const [percent, setPercent] = useState(5)
  const [reason, setReason] = useState('')
  const [pendingAction, setPendingAction] = useState<DeliveryAction>(null)
  const selected = useMemo(() => deployments.find((item) => item.id === deploymentId) ?? null, [deploymentId, deployments])
  const activeRelease = snapshot.releases.find((item) => item.id === selected?.active_release_id)
  const trialReleaseId = selected?.canary_release_id ?? null
  const trialRelease = snapshot.releases.find((item) => item.id === trialReleaseId)
  const candidateReleases = snapshot.releases.filter((item) => (
    item.status === 'approved'
    && item.id !== selected?.active_release_id
    && item.definition_id === activeRelease?.definition_id
  ))

  useEffect(() => {
    if (!deployments.some((item) => item.id === deploymentId)) setDeploymentId(deployments[0]?.id || '')
  }, [deploymentId, deployments])
  useEffect(() => {
    if (!candidateReleases.some((item) => item.id === releaseId)) setReleaseId(candidateReleases[0]?.id || '')
  }, [candidateReleases, releaseId])

  const delivery = useQuery({
    queryKey: ['governance', 'deployment-delivery', deploymentId],
    queryFn: () => governanceApi.deploymentDelivery(Number(deploymentId)),
    enabled: Boolean(deploymentId),
    refetchInterval: 30000,
  })
  const invalidate = async () => {
    setPendingAction(null)
    setReason('')
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['governance', 'deployment-delivery', deploymentId] }),
      queryClient.invalidateQueries({ queryKey: ['agentControlSnapshot'] }),
    ])
  }
  const start = useMutation({ mutationFn: () => governanceApi.startTrial(Number(deploymentId), { release_id: Number(releaseId), percent, reason }), onSuccess: invalidate })
  const adjust = useMutation({ mutationFn: () => governanceApi.adjustTrial(Number(deploymentId), { percent, reason }), onSuccess: invalidate })
  const pause = useMutation({ mutationFn: () => governanceApi.pauseTrial(Number(deploymentId), reason), onSuccess: invalidate })
  const promote = useMutation({ mutationFn: () => governanceApi.promoteTrial(Number(deploymentId), reason), onSuccess: invalidate })
  const mutationError = start.error || adjust.error || pause.error || promote.error
  const trialActive = Boolean(delivery.data?.deployment.canary_release_id ?? selected?.canary_release_id)
  const busy = start.isPending || adjust.isPending || pause.isPending || promote.isPending
  const reasonValid = reason.trim().length >= 2

  const executeAction = () => {
    if (pendingAction === 'start') start.mutate()
    if (pendingAction === 'adjust') adjust.mutate()
    if (pendingAction === 'pause') pause.mutate()
    if (pendingAction === 'promote') promote.mutate()
  }

  if (!deployments.length) return <OperatorEmptyState title="尚无可管理的发布范围" description="请先在“方案与测试”中发布并应用一个版本。" />

  return (
    <Stack spacing={2}>
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
          <RocketLaunchRoundedIcon color="primary" />
          <Typography component="h2" variant="h2">发布范围</Typography>
        </Stack>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 0.75 }}>
          先将新版本应用到少量流量，确认运行稳定后再扩大比例或设为正式版本。
        </Typography>
        {!canDeploy ? <Alert severity="info" sx={{ mt: 2 }}>当前账号只能查看发布状态，不能调整生效比例。</Alert> : null}
      </Paper>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Stack spacing={2}>
          <TextField select label="生效范围" value={deploymentId} onChange={(event) => setDeploymentId(Number(event.target.value))}>
            {deployments.map((item) => <MenuItem key={item.id} value={item.id}>{deploymentLabel(item)}</MenuItem>)}
          </TextField>
          {selected ? (
            <Stack direction="row" useFlexGap spacing={1} sx={{ flexWrap: 'wrap' }}>
              <Chip label={`正式版本 ${activeRelease ? `v${activeRelease.version}` : '未知'}`} color="success" />
              <Chip label={trialActive ? `小范围版本 ${trialRelease ? `v${trialRelease.version}` : '未知'}` : '无小范围版本'} color={trialActive ? 'warning' : 'default'} />
              <Chip label={`生效比例 ${delivery.data?.deployment.canary_percent ?? selected.canary_percent}%`} />
              <Chip label={environmentLabel(selected.environment)} />
            </Stack>
          ) : null}
          {!trialActive ? (
            <TextField select label="候选版本" value={releaseId} onChange={(event) => setReleaseId(Number(event.target.value))}>
              {!candidateReleases.length ? <MenuItem value="" disabled>暂无可用候选版本</MenuItem> : null}
              {candidateReleases.map((item) => <MenuItem key={item.id} value={item.id}>版本 v{item.version}</MenuItem>)}
            </TextField>
          ) : null}
          <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
            <TextField fullWidth type="number" label="小范围流量比例" value={percent} slotProps={{ htmlInput: { min: 1, max: 99 } }} onChange={(event) => setPercent(Math.min(99, Math.max(1, Number(event.target.value) || 1)))} />
            <TextField fullWidth label="变更原因" value={reason} onChange={(event) => setReason(event.target.value)} helperText="至少填写 2 个字符，便于后续审计。" />
          </Stack>
          <Stack direction={{ xs: 'column', md: 'row' }} spacing={1}>
            {!trialActive ? (
              <Button variant="contained" startIcon={busy ? <CircularProgress size={16} color="inherit" /> : <PlayCircleRoundedIcon />} disabled={!canDeploy || busy || !releaseId || !reasonValid} onClick={() => setPendingAction('start')}>开始小范围发布</Button>
            ) : (
              <>
                <Button variant="outlined" startIcon={<TuneRoundedIcon />} disabled={!canDeploy || busy || !reasonValid} onClick={() => setPendingAction('adjust')}>调整比例</Button>
                <Button color="warning" variant="outlined" startIcon={<PauseCircleRoundedIcon />} disabled={!canDeploy || busy || !reasonValid} onClick={() => setPendingAction('pause')}>暂停小范围发布</Button>
                <Button color="success" variant="contained" startIcon={<RocketLaunchRoundedIcon />} disabled={!canDeploy || busy || !reasonValid} onClick={() => setPendingAction('promote')}>设为正式版本</Button>
              </>
            )}
          </Stack>
          {mutationError ? <OperatorErrorNotice title="发布操作失败" error={mutationError} fallback="请刷新发布状态，并确认所选版本适用于当前范围" /> : null}
          <OperatorTechnicalDisclosure title="系统信息" summary="范围标识与版本编号">
            <Box component="pre" sx={{ m: 0, overflow: 'auto', whiteSpace: 'pre-wrap', fontSize: 12 }}>
              {JSON.stringify({
                deployment_id: selected?.id,
                scope_key: selected?.scope_key,
                active_release_id: selected?.active_release_id,
                canary_release_id: delivery.data?.deployment.canary_release_id ?? selected?.canary_release_id,
              }, null, 2)}
            </Box>
          </OperatorTechnicalDisclosure>
        </Stack>
      </Paper>

      {delivery.isLoading ? <Stack sx={{ alignItems: 'center', py: 4 }}><CircularProgress /></Stack> : null}
      {delivery.error ? <OperatorErrorNotice title="无法读取发布状态" error={delivery.error} fallback="请稍后重试" /> : null}
      {delivery.data ? (
        <>
          <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: 'repeat(3, minmax(0, 1fr))' }, gap: 2 }}>
            <Metric title="最近 24 小时运行总量" value={delivery.data.traffic_24h.total} />
            <Metric title="正式 / 小范围流量" value={`${delivery.data.traffic_24h.stable} / ${delivery.data.traffic_24h.trial}`} />
            <Metric title="小范围失败 / 降级" value={`${delivery.data.health_24h.trial.failed} / ${delivery.data.health_24h.trial.fallback}`} />
          </Box>
          <Paper variant="outlined" sx={{ p: 2 }}>
            <Typography variant="h3">最近发布记录</Typography>
            {!delivery.data.revisions.length ? <Alert severity="info" sx={{ mt: 1 }}>尚无发布记录。</Alert> : null}
            <Stack spacing={1} sx={{ mt: 1 }}>
              {delivery.data.revisions.map((row) => (
                <Paper key={row.id} variant="outlined" sx={{ p: 1.5 }}>
                  <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} sx={{ justifyContent: 'space-between' }}>
                    <Box>
                      <Typography variant="subtitle2">{revisionActionLabel(row.action)}</Typography>
                      <Typography variant="body2">{row.reason || '未填写原因'}</Typography>
                    </Box>
                    <Typography variant="caption" color="text.secondary">{new Date(row.created_at).toLocaleString()}</Typography>
                  </Stack>
                </Paper>
              ))}
            </Stack>
            <OperatorTechnicalDisclosure title="原始发布数据">
              <Box component="pre" sx={{ m: 0, overflow: 'auto', whiteSpace: 'pre-wrap', fontSize: 12 }}>{JSON.stringify(delivery.data, null, 2)}</Box>
            </OperatorTechnicalDisclosure>
          </Paper>
        </>
      ) : null}

      <Dialog open={Boolean(pendingAction)} onClose={() => { if (!busy) setPendingAction(null) }} fullWidth maxWidth="sm">
        <DialogTitle>{deliveryActionTitle(pendingAction)}</DialogTitle>
        <DialogContent>
          <DialogContentText>{deliveryActionDescription(pendingAction, percent)}</DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button color="inherit" disabled={busy} onClick={() => setPendingAction(null)}>取消</Button>
          <Button
            color={pendingAction === 'pause' ? 'warning' : pendingAction === 'promote' ? 'success' : 'primary'}
            variant="contained"
            disabled={!pendingAction || busy || !reasonValid}
            onClick={executeAction}
          >
            {busy ? '正在处理…' : deliveryActionButtonLabel(pendingAction)}
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  )
}

function deploymentLabel(item: AgentDeployment) {
  return `${environmentLabel(item.environment)} · ${item.scope_key}`
}

function environmentLabel(value: string) {
  if (value === 'production') return '生产环境'
  if (value === 'staging') return '预发布环境'
  if (value === 'test') return '测试环境'
  return value
}

function revisionActionLabel(action: string) {
  if (action === 'trial_started') return '开始小范围发布'
  if (action === 'trial_adjusted') return '调整小范围比例'
  if (action === 'trial_paused') return '暂停小范围发布'
  if (action === 'trial_promoted') return '设为正式版本'
  return action
}

function deliveryActionTitle(action: DeliveryAction) {
  if (action === 'start') return '开始小范围发布？'
  if (action === 'adjust') return '调整生效比例？'
  if (action === 'pause') return '暂停小范围发布？'
  if (action === 'promote') return '设为正式版本？'
  return '确认发布操作'
}

function deliveryActionDescription(action: DeliveryAction, percent: number) {
  if (action === 'start') return `候选版本将先应用到 ${percent}% 的流量。`
  if (action === 'adjust') return `小范围版本的流量比例将调整为 ${percent}%。`
  if (action === 'pause') return '小范围版本将停止接收新流量，正式版本继续运行。'
  if (action === 'promote') return '当前小范围版本将成为正式版本，并接收全部流量。'
  return ''
}

function deliveryActionButtonLabel(action: DeliveryAction) {
  if (action === 'start') return '确认开始'
  if (action === 'adjust') return '确认调整'
  if (action === 'pause') return '确认暂停'
  if (action === 'promote') return '确认设为正式版本'
  return '确认'
}

function Metric({ title, value }: { title: string; value: string | number }) {
  return <Paper variant="outlined" sx={{ p: 2 }}><Typography variant="caption" color="text.secondary">{title}</Typography><Typography variant="h3" sx={{ mt: 0.5 }}>{value}</Typography></Paper>
}

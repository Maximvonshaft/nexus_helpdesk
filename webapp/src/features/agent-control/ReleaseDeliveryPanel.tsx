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

export function ReleaseDeliveryPanel({ snapshot, canDeploy }: { snapshot: AgentControlSnapshot; canDeploy: boolean }) {
  const queryClient = useQueryClient()
  const deployments = snapshot.deployments
  const [deploymentId, setDeploymentId] = useState<number | ''>(deployments[0]?.id || '')
  const [releaseId, setReleaseId] = useState<number | ''>('')
  const [percent, setPercent] = useState(5)
  const [reason, setReason] = useState('')
  const selected = useMemo(() => deployments.find((item) => item.id === deploymentId) ?? null, [deploymentId, deployments])
  const activeRelease = snapshot.releases.find((item) => item.id === selected?.active_release_id)
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

  if (!deployments.length) return <OperatorEmptyState title="没有 Agent Deployment" description="先在定义、发布与测试中创建不可变 Release 并部署到目标作用域。" />

  return (
    <Stack spacing={2}>
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
          <RocketLaunchRoundedIcon color="primary" />
          <Typography component="h2" variant="h2">小范围发布</Typography>
        </Stack>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 0.75 }}>
          只修改现有 AgentDeployment 的稳定 Release、试验 Release 与流量比例。所有动作写入修订证据，并继续由唯一 Release Resolver 决定运行时选择。
        </Typography>
        {!canDeploy ? <Alert severity="info" sx={{ mt: 2 }}>当前账号可查看发布状态，但不能调整生产流量。</Alert> : null}
      </Paper>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Stack spacing={2}>
          <TextField select label="Deployment" value={deploymentId} onChange={(event) => setDeploymentId(Number(event.target.value))}>
            {deployments.map((item) => <MenuItem key={item.id} value={item.id}>{deploymentLabel(item)}</MenuItem>)}
          </TextField>
          {selected ? (
            <Stack direction="row" useFlexGap spacing={1} sx={{ flexWrap: 'wrap' }}>
              <Chip label={`稳定 Release #${selected.active_release_id}`} color="success" />
              <Chip label={trialActive ? `试验 Release #${delivery.data?.deployment.canary_release_id || selected.canary_release_id}` : '无试验 Release'} color={trialActive ? 'warning' : 'default'} />
              <Chip label={`流量 ${delivery.data?.deployment.canary_percent ?? selected.canary_percent}%`} />
              <Chip label={selected.environment} />
              <Chip label={selected.scope_key} />
            </Stack>
          ) : null}
          {!trialActive ? (
            <TextField select label="候选 Release" value={releaseId} onChange={(event) => setReleaseId(Number(event.target.value))}>
              {!candidateReleases.length ? <MenuItem value="" disabled>没有同一 Agent Definition 的其他已批准 Release</MenuItem> : null}
              {candidateReleases.map((item) => <MenuItem key={item.id} value={item.id}>Release #{item.id} · v{item.version} · {item.manifest_sha256.slice(0, 12)}</MenuItem>)}
            </TextField>
          ) : null}
          <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
            <TextField fullWidth type="number" label="试验流量百分比" value={percent} slotProps={{ htmlInput: { min: 1, max: 99 } }} onChange={(event) => setPercent(Math.min(99, Math.max(1, Number(event.target.value) || 1)))} />
            <TextField fullWidth label="变更原因" value={reason} onChange={(event) => setReason(event.target.value)} helperText="至少 2 个字符，将进入不可变修订证据。" />
          </Stack>
          <Stack direction={{ xs: 'column', md: 'row' }} spacing={1}>
            {!trialActive ? (
              <Button variant="contained" startIcon={busy ? <CircularProgress size={16} color="inherit" /> : <PlayCircleRoundedIcon />} disabled={!canDeploy || busy || !releaseId || !reasonValid} onClick={() => start.mutate()}>启动试验</Button>
            ) : (
              <>
                <Button variant="outlined" startIcon={<TuneRoundedIcon />} disabled={!canDeploy || busy || !reasonValid} onClick={() => adjust.mutate()}>调整流量</Button>
                <Button color="warning" variant="outlined" startIcon={<PauseCircleRoundedIcon />} disabled={!canDeploy || busy || !reasonValid} onClick={() => pause.mutate()}>暂停试验</Button>
                <Button color="success" variant="contained" startIcon={<RocketLaunchRoundedIcon />} disabled={!canDeploy || busy || !reasonValid} onClick={() => promote.mutate()}>提升为稳定版本</Button>
              </>
            )}
          </Stack>
          {mutationError ? <OperatorErrorNotice title="发布动作失败" error={mutationError} fallback="请刷新 Deployment 状态并确认 Release 属于同一 Agent Definition" /> : null}
        </Stack>
      </Paper>

      {delivery.isLoading ? <Stack sx={{ alignItems: 'center', py: 4 }}><CircularProgress /></Stack> : null}
      {delivery.error ? <OperatorErrorNotice title="无法读取发布运行证据" error={delivery.error} fallback="请稍后重试" /> : null}
      {delivery.data ? (
        <>
          <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: 'repeat(3, minmax(0, 1fr))' }, gap: 2 }}>
            <Metric title="24h 总运行" value={delivery.data.traffic_24h.total} />
            <Metric title="稳定 / 试验流量" value={`${delivery.data.traffic_24h.stable} / ${delivery.data.traffic_24h.trial}`} />
            <Metric title="试验失败 / 回退" value={`${delivery.data.health_24h.trial.failed} / ${delivery.data.health_24h.trial.fallback}`} />
          </Box>
          <Paper variant="outlined" sx={{ p: 2 }}>
            <Typography variant="h3">最近发布修订</Typography>
            {!delivery.data.revisions.length ? <Alert severity="info" sx={{ mt: 1 }}>尚无发布修订记录。</Alert> : null}
            <Stack spacing={1} sx={{ mt: 1 }}>
              {delivery.data.revisions.map((row) => (
                <Paper key={row.id} variant="outlined" sx={{ p: 1.5 }}>
                  <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} sx={{ justifyContent: 'space-between' }}>
                    <Box>
                      <Typography variant="subtitle2">#{row.revision} · {row.action}</Typography>
                      <Typography variant="body2">{row.reason || '未填写原因'}</Typography>
                    </Box>
                    <Typography variant="caption" color="text.secondary">{new Date(row.created_at).toLocaleString()}</Typography>
                  </Stack>
                </Paper>
              ))}
            </Stack>
            <OperatorTechnicalDisclosure title="发布证据">
              <Box component="pre" sx={{ m: 0, overflow: 'auto', whiteSpace: 'pre-wrap', fontSize: 12 }}>{JSON.stringify(delivery.data, null, 2)}</Box>
            </OperatorTechnicalDisclosure>
          </Paper>
        </>
      ) : null}
    </Stack>
  )
}

function deploymentLabel(item: AgentDeployment) {
  return `#${item.id} · ${item.environment} · ${item.scope_key}`
}

function Metric({ title, value }: { title: string; value: string | number }) {
  return <Paper variant="outlined" sx={{ p: 2 }}><Typography variant="caption" color="text.secondary">{title}</Typography><Typography variant="h3" sx={{ mt: 0.5 }}>{value}</Typography></Paper>
}

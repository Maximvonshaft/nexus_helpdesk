import ReplayRoundedIcon from '@mui/icons-material/ReplayRounded'
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Divider,
  Paper,
  Stack,
  Typography,
} from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import {
  OperatorErrorNotice,
  OperatorFactGrid,
  OperatorLoadingState,
} from '@/app/OperatorPresentation'
import { useSession } from '@/hooks/useAuth'
import { supportApi } from '@/lib/supportApi'

type RecoveryAction = 'jobs' | 'outbound' | null

export function RuntimeRecoveryPanel() {
  const queryClient = useQueryClient()
  const session = useSession()
  const capabilities = useMemo(() => new Set(session.data?.capabilities ?? []), [session.data?.capabilities])
  const canRecover = capabilities.has('runtime.manage')
  const [action, setAction] = useState<RecoveryAction>(null)

  const summary = useQuery({
    queryKey: ['canonicalQueueSummary'],
    queryFn: supportApi.queueSummary,
    refetchInterval: 15_000,
    retry: false,
  })

  const recovery = useMutation({
    mutationFn: async (selected: Exclude<RecoveryAction, null>) => selected === 'jobs'
      ? supportApi.requeueDeadJobs(50)
      : supportApi.requeueDeadOutbound(50),
    onSuccess: async () => {
      setAction(null)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['canonicalQueueSummary'] }),
        queryClient.invalidateQueries({ queryKey: ['securityAudit'] }),
      ])
    },
  })

  return (
    <Paper component="section" variant="outlined" aria-labelledby="runtime-recovery-title" sx={{ p: 2, mt: 2 }}>
      <Stack direction={{ xs: 'column', md: 'row' }} spacing={2} sx={{ alignItems: { xs: 'stretch', md: 'center' }, justifyContent: 'space-between' }}>
        <Box>
          <Typography id="runtime-recovery-title" component="h2" variant="h3">失败任务恢复</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            将失败的后台任务或外部消息重新加入处理队列。每次最多处理 50 项。
          </Typography>
        </Box>
        {summary.isFetching ? <CircularProgress size={20} aria-label="正在刷新队列" /> : null}
      </Stack>
      <Divider sx={{ my: 2 }} />

      {summary.isError ? <OperatorErrorNotice title="无法读取任务状态" error={summary.error} fallback="请稍后重试" /> : null}
      {recovery.isError ? <OperatorErrorNotice title="恢复失败" error={recovery.error} fallback="请检查操作权限或稍后重试" /> : null}
      {summary.isLoading ? <OperatorLoadingState label="正在读取任务状态…" minHeight={160} /> : summary.data ? (
        <Stack spacing={2}>
          <OperatorFactGrid columns={4} facts={[
            ['待处理后台任务', summary.data.pending_jobs],
            ['失败后台任务', summary.data.dead_jobs],
            ['待发送外部消息', summary.data.external_pending_outbound ?? summary.data.pending_outbound ?? 0],
            ['失败外部消息', summary.data.external_dead_outbound ?? summary.data.dead_outbound ?? 0],
          ]} />
          {!canRecover ? (
            <Alert severity="info" variant="outlined">当前账号只能查看，不能执行恢复。</Alert>
          ) : (
            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
              <Button
                variant="outlined"
                color="warning"
                startIcon={<ReplayRoundedIcon />}
                disabled={!summary.data.dead_jobs || recovery.isPending}
                onClick={() => setAction('jobs')}
              >
                恢复失败后台任务
              </Button>
              <Button
                variant="outlined"
                color="warning"
                startIcon={<ReplayRoundedIcon />}
                disabled={!(summary.data.external_dead_outbound ?? summary.data.dead_outbound) || recovery.isPending}
                onClick={() => setAction('outbound')}
              >
                恢复失败外部消息
              </Button>
            </Stack>
          )}
        </Stack>
      ) : null}

      <Dialog open={Boolean(action)} onClose={() => { if (!recovery.isPending) setAction(null) }} maxWidth="sm" fullWidth>
        <DialogTitle>{action === 'jobs' ? '恢复失败后台任务' : '恢复失败外部消息'}</DialogTitle>
        <DialogContent>
          <DialogContentText>
            系统将从最早失败的记录开始，最多重新处理 50 项。恢复操作只会重新安排处理，不代表业务结果已经完成。
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button color="inherit" disabled={recovery.isPending} onClick={() => setAction(null)}>取消</Button>
          <Button
            color="warning"
            variant="contained"
            disabled={!action || recovery.isPending}
            startIcon={recovery.isPending ? <CircularProgress color="inherit" size={16} /> : <ReplayRoundedIcon />}
            onClick={() => { if (action) recovery.mutate(action) }}
          >
            {recovery.isPending ? '正在恢复…' : '确认恢复'}
          </Button>
        </DialogActions>
      </Dialog>
    </Paper>
  )
}

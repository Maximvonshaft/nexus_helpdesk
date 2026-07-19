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
import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import {
  OperatorErrorNotice,
  OperatorTechnicalDisclosure,
} from '@/app/OperatorPresentation'
import { sanitizeDisplayText } from '@/lib/format'
import { supportApi } from '@/lib/supportApi'
import type {
  TicketClosureEvidenceKind,
  TicketClosureEvidenceSource,
  TicketClosureEvidenceState,
} from '@/lib/ticketClosureTypes'

interface MissingEvidenceOption {
  kind: TicketClosureEvidenceKind
  key: string
  label: string
}

const SOURCE_OPTIONS: Array<{ value: TicketClosureEvidenceSource; label: string }> = [
  { value: 'tracking', label: '物流权威状态' },
  { value: 'provider_receipt', label: 'Provider 回执' },
  { value: 'operations_dispatch', label: '运营执行结果' },
  { value: 'customer_confirmation', label: '客户确认' },
  { value: 'policy_decision', label: '受控政策决定' },
  { value: 'operator_observation', label: '运营观察（不能证明权威事实）' },
]

function evidenceOptions(receipt: Awaited<ReturnType<typeof supportApi.ticketClosureReadiness>> | undefined) {
  if (!receipt) return []
  const readiness = receipt.readiness
  return [
    ...readiness.missing_fact_classes.map((key) => ({ kind: 'fact' as const, key, label: `权威事实 · ${key}` })),
    ...readiness.missing_customer_inputs.map((key) => ({ kind: 'customer_input' as const, key, label: `客户输入 · ${key}` })),
    ...readiness.missing_action_classes.map((key) => ({ kind: 'action' as const, key, label: `受控动作 · ${key}` })),
    ...readiness.missing_outcome_levels.map((key) => ({ kind: 'outcome' as const, key, label: `业务结果 · ${key}` })),
  ] satisfies MissingEvidenceOption[]
}

function labels(values: string[]) {
  return values.length ? values.map((value) => sanitizeDisplayText(value)).join('、') : '无'
}

export function OperatorWorkspaceClosure({
  ticketId,
  sourceStatus,
  onRefresh,
}: {
  ticketId: number | null
  sourceStatus: string
  onRefresh: () => Promise<void>
}) {
  const [selectedEvidence, setSelectedEvidence] = useState('')
  const [sourceKind, setSourceKind] = useState<TicketClosureEvidenceSource>('tracking')
  const [sourceRef, setSourceRef] = useState('')
  const [sourceRevision, setSourceRevision] = useState('')
  const [note, setNote] = useState('')

  const readinessQuery = useQuery({
    queryKey: ['ticket-closure-readiness', ticketId],
    queryFn: () => {
      if (!ticketId) throw new Error('当前任务没有工单')
      return supportApi.ticketClosureReadiness(ticketId)
    },
    enabled: Boolean(ticketId),
    staleTime: 0,
  })

  const options = useMemo(() => evidenceOptions(readinessQuery.data), [readinessQuery.data])
  const selected = options.find((option) => `${option.kind}:${option.key}` === selectedEvidence) ?? null

  useEffect(() => {
    setSelectedEvidence('')
    setSourceRef('')
    setSourceRevision('')
    setNote('')
  }, [ticketId])

  useEffect(() => {
    if (selected?.kind === 'fact') setSourceKind('tracking')
    else if (selected?.kind === 'outcome') setSourceKind('operations_dispatch')
  }, [selected])

  const evidenceMutation = useMutation({
    mutationFn: async () => {
      if (!ticketId || !selected) throw new Error('请选择需要补充的关闭证据')
      const state: TicketClosureEvidenceState = selected.kind === 'fact' || selected.kind === 'customer_input'
        ? 'verified'
        : 'completed'
      return supportApi.recordTicketClosureEvidence(ticketId, {
        kind: selected.kind,
        key: selected.key,
        state,
        source_kind: sourceKind,
        source_ref: sourceRef.trim(),
        source_revision: sourceRevision.trim(),
        observed_at: new Date().toISOString(),
        note: note.trim() || null,
      })
    },
    onSuccess: async () => {
      setSelectedEvidence('')
      setSourceRef('')
      setSourceRevision('')
      setNote('')
      await readinessQuery.refetch()
      await onRefresh()
    },
  })

  const closeMutation = useMutation({
    mutationFn: async () => {
      if (!ticketId) throw new Error('当前任务没有工单')
      const latest = await supportApi.ticketClosureReadiness(ticketId)
      if (!latest.readiness.closure_ready) throw new Error('关闭凭证已变化，请重新检查')
      return supportApi.closeTicket(ticketId, `Safe Effective Closure ${latest.receipt_sha256}`)
    },
    onSuccess: async () => {
      await readinessQuery.refetch()
      await onRefresh()
    },
  })

  if (!ticketId) {
    return <Alert severity="info" variant="outlined">当前任务没有可关闭的工单。</Alert>
  }

  if (readinessQuery.isPending) {
    return (
      <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
        <CircularProgress size={18} />
        <Typography variant="body2">正在核对关闭条件…</Typography>
      </Stack>
    )
  }

  if (readinessQuery.error) {
    return <OperatorErrorNotice title="无法核对关闭条件" error={readinessQuery.error} fallback="请刷新后重试" />
  }

  const receipt = readinessQuery.data
  const readiness = receipt.readiness
  const busy = evidenceMutation.isPending || closeMutation.isPending || readinessQuery.isFetching
  const error = evidenceMutation.error || closeMutation.error
  const canRecord = Boolean(selected && sourceRef.trim() && sourceRevision.trim())
  const alreadyClosed = sourceStatus.toLowerCase() === 'closed'

  return (
    <Box>
      <Stack direction="row" spacing={1.5} sx={{ alignItems: 'flex-start', justifyContent: 'space-between', flexWrap: 'wrap' }}>
        <Box>
          <Typography component="h3" variant="subtitle1">安全关闭</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            关闭资格由服务器基于场景、事实、动作、业务结果、客户通知和观察期计算。
          </Typography>
        </Box>
        <Button
          variant="contained"
          color="success"
          disabled={!readiness.closure_ready || alreadyClosed || busy}
          startIcon={closeMutation.isPending ? <CircularProgress color="inherit" size={16} /> : undefined}
          onClick={() => closeMutation.mutate()}
        >
          {alreadyClosed ? '已关闭' : '确认安全关闭'}
        </Button>
      </Stack>

      <Alert severity={readiness.closure_ready ? 'success' : 'warning'} variant="outlined" sx={{ mt: 1.5 }} role="status">
        <AlertTitle>{readiness.closure_ready ? '关闭条件已满足' : '关闭条件尚未满足'}</AlertTitle>
        场景：{sanitizeDisplayText(receipt.scenario_key || readiness.scenario_key || '无法识别')}
        {!readiness.notification_satisfied ? '；客户通知尚未满足' : ''}
      </Alert>

      <Paper variant="outlined" sx={{ p: 1.5, mt: 1.5 }}>
        <Stack spacing={0.75}>
          <Typography variant="body2"><strong>缺少权威事实：</strong>{labels(readiness.missing_fact_classes)}</Typography>
          <Typography variant="body2"><strong>缺少客户输入：</strong>{labels(readiness.missing_customer_inputs)}</Typography>
          <Typography variant="body2"><strong>缺少受控动作：</strong>{labels(readiness.missing_action_classes)}</Typography>
          <Typography variant="body2"><strong>缺少业务结果：</strong>{labels(readiness.missing_outcome_levels)}</Typography>
          <Typography variant="body2"><strong>其他阻断：</strong>{labels(readiness.blocked_reasons)}</Typography>
        </Stack>
      </Paper>

      {!readiness.closure_ready && options.length ? (
        <Stack spacing={1.25} sx={{ mt: 1.5 }}>
          <Divider />
          <Typography variant="subtitle2">补充可核验关闭证据</Typography>
          <TextField
            select
            label="缺失条件"
            value={selectedEvidence}
            onChange={(event) => setSelectedEvidence(event.target.value)}
          >
            {options.map((option) => (
              <MenuItem key={`${option.kind}:${option.key}`} value={`${option.kind}:${option.key}`}>
                {sanitizeDisplayText(option.label)}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            select
            label="证据来源"
            value={sourceKind}
            onChange={(event) => setSourceKind(event.target.value as TicketClosureEvidenceSource)}
          >
            {SOURCE_OPTIONS.map((option) => (
              <MenuItem key={option.value} value={option.value}>{option.label}</MenuItem>
            ))}
          </TextField>
          <TextField
            label="来源标识"
            required
            value={sourceRef}
            onChange={(event) => setSourceRef(event.target.value)}
            helperText="例如物流查询回执、Dispatch 任务或客户确认记录的稳定标识"
          />
          <TextField
            label="来源版本"
            required
            value={sourceRevision}
            onChange={(event) => setSourceRevision(event.target.value)}
            helperText="填写回执版本、更新时间或不可变修订号"
          />
          <TextField
            label="说明"
            value={note}
            onChange={(event) => setNote(event.target.value)}
            multiline
            minRows={2}
          />
          <Button
            variant="outlined"
            disabled={!canRecord || busy}
            startIcon={evidenceMutation.isPending ? <CircularProgress color="inherit" size={16} /> : undefined}
            onClick={() => evidenceMutation.mutate()}
          >
            记录证据并重新核对
          </Button>
        </Stack>
      ) : null}

      {error ? <Box sx={{ mt: 1.5 }}><OperatorErrorNotice title="关闭操作失败" error={error} fallback="请核对证据后重试" /></Box> : null}

      <Box sx={{ mt: 1.5 }}>
        <OperatorTechnicalDisclosure title="关闭凭证" compact>
          <Stack spacing={0.5}>
            <Typography component="code" variant="caption">{receipt.receipt_sha256}</Typography>
            <Typography variant="caption">场景版本：{sanitizeDisplayText(receipt.scenario_catalog_version || '不可用')}</Typography>
            <Typography variant="caption">工单修订：{sanitizeDisplayText(receipt.ticket_revision)}</Typography>
            <Typography variant="caption">观察期：{receipt.evidence.observation_elapsed ? '已满足' : '未满足'}</Typography>
          </Stack>
        </OperatorTechnicalDisclosure>
      </Box>
    </Box>
  )
}

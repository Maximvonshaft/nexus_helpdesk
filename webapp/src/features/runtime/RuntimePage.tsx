import ExpandMoreRoundedIcon from '@mui/icons-material/ExpandMoreRounded'
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  AlertTitle,
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  Paper,
  Stack,
  Typography,
} from '@mui/material'
import { lazy, Suspense, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { sanitizeDisplayText } from '@/lib/format'
import { supportApi } from '@/lib/supportApi'
import { runtimePresentation } from '@/lib/supportStatus'

const LazyRuntimeEvidenceAudit = lazy(async () => {
  const module = await import('./RuntimeEvidenceAudit')
  return { default: module.RuntimeEvidenceAudit }
})

function compactLatency(value: number | null | undefined) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '暂无'
  if (value >= 1000) return `${(value / 1000).toFixed(value >= 10000 ? 0 : 1)}s`
  return `${Math.max(0, Math.round(value))}ms`
}

function errorCopy(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback
}

function statusColor(tone: string) {
  if (tone === 'success') return 'success'
  if (tone === 'warning') return 'warning'
  if (tone === 'danger') return 'error'
  return 'default'
}

function FactGrid({ facts }: { facts: Array<[string, React.ReactNode]> }) {
  return (
    <Box component="dl" sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr 1fr', md: 'repeat(4, 1fr)' }, m: 0 }}>
      {facts.map(([label, value]) => (
        <Box key={label} sx={{ minWidth: 0 }}>
          <Typography component="dt" variant="caption" color="text.secondary">{label}</Typography>
          <Typography component="dd" variant="body2" sx={{ m: 0, mt: 0.5, overflowWrap: 'anywhere', fontVariantNumeric: 'tabular-nums' }}>{value}</Typography>
        </Box>
      ))}
    </Box>
  )
}

function TechnicalDisclosure({ title, summary, children }: { title: string; summary: string; children: React.ReactNode }) {
  return (
    <Accordion disableGutters variant="outlined" sx={{ '&:before': { display: 'none' } }}>
      <AccordionSummary expandIcon={<ExpandMoreRoundedIcon />}>
        <Box><Typography variant="subtitle2">{title}</Typography><Typography variant="caption" color="text.secondary">{summary}</Typography></Box>
      </AccordionSummary>
      <AccordionDetails sx={{ borderTop: 1, borderColor: 'divider' }}>{children}</AccordionDetails>
    </Accordion>
  )
}

export function RuntimePage() {
  const [showEvidenceAudit, setShowEvidenceAudit] = useState(false)
  const runtime = useQuery({
    queryKey: ['canonicalProviderRuntimeStatus'],
    queryFn: supportApi.providerRuntimeStatus,
    refetchInterval: 15_000,
    retry: false,
  })
  const metrics = useQuery({
    queryKey: ['canonicalConversationMetrics'],
    queryFn: () => supportApi.supportConversationMetrics(24),
    refetchInterval: 15_000,
    retry: false,
  })
  const state = runtimePresentation({
    isLoading: runtime.isLoading,
    isError: runtime.isError,
    ok: runtime.data?.ok,
    warnings: runtime.data?.warnings,
  })
  const selectedProvider = runtime.data?.providers?.find((item) => item.selected)
  const latency = metrics.data?.runtime_latency

  return (
    <Box component="main" sx={{ p: { xs: 1.5, md: 2.5 } }}>
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} alignItems={{ xs: 'stretch', sm: 'flex-start' }} justifyContent="space-between" sx={{ mb: 2.5 }}>
        <Typography component="h1" variant="h1">系统运行</Typography>
        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
          <Chip color={statusColor(state.tone)} label={state.label} />
          <Button variant="outlined" color="inherit" onClick={() => setShowEvidenceAudit((current) => !current)}>
            {showEvidenceAudit ? '运行概览' : '证据审计'}
          </Button>
        </Stack>
      </Stack>

      {showEvidenceAudit ? (
        <Suspense fallback={<Stack role="status" alignItems="center" spacing={1.5} sx={{ minHeight: 240, justifyContent: 'center' }}><CircularProgress size={30} /><Typography variant="subtitle2">正在加载…</Typography></Stack>}>
          <LazyRuntimeEvidenceAudit />
        </Suspense>
      ) : (
        <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', xl: 'minmax(0, 1.4fr) minmax(300px, 0.8fr)' } }}>
          <Paper component="section" variant="outlined" aria-labelledby="service-readiness-title" sx={{ minWidth: 0, p: 2 }}>
            <Stack direction="row" spacing={1} alignItems="center" justifyContent="space-between">
              <Typography id="service-readiness-title" component="h2" variant="h3">系统状态</Typography>
              {runtime.isFetching ? <CircularProgress size={18} aria-label="正在检查" /> : null}
            </Stack>
            <Divider sx={{ my: 2 }} />
            {runtime.isError ? (
              <Alert severity="error" variant="outlined"><AlertTitle>无法读取系统状态</AlertTitle>{errorCopy(runtime.error, '请稍后重试')}</Alert>
            ) : runtime.isLoading ? (
              <Stack role="status" alignItems="center" spacing={1.5} sx={{ minHeight: 180, justifyContent: 'center' }}><CircularProgress size={28} /><Typography variant="subtitle2">正在检查…</Typography></Stack>
            ) : (
              <Stack spacing={2}>
                <FactGrid facts={[
                  ['状态', state.label],
                  ['处理方式', runtime.data?.webchat_runtime_enabled ? '自动处理' : '人工处理'],
                  ['备用方式', runtime.data?.fallback_provider ? '已配置' : '未配置'],
                  ['配置状态', selectedProvider?.configured ? '已配置' : '需要检查'],
                ]} />
                {runtime.data?.warnings?.length ? (
                  <Stack spacing={1} aria-label="运行提醒">
                    {runtime.data.warnings.map((item) => <Alert key={String(item)} severity="warning" variant="outlined">{sanitizeDisplayText(String(item))}</Alert>)}
                  </Stack>
                ) : <Alert severity="success" variant="outlined">无运行提醒</Alert>}
                <TechnicalDisclosure title="系统信息" summary="状态代码、服务配置与诊断">
                  <FactGrid facts={[
                    ['状态代码', <Box component="code">{sanitizeDisplayText(runtime.data?.status || 'unknown')}</Box>],
                    ['服务提供方', <Box component="code">{sanitizeDisplayText(runtime.data?.configured_provider || selectedProvider?.name || '未配置')}</Box>],
                    ['备用服务', <Box component="code">{sanitizeDisplayText(runtime.data?.fallback_provider || '无')}</Box>],
                    ['运行环境', <Box component="code">{sanitizeDisplayText(runtime.data?.app_env || 'unknown')}</Box>],
                  ]} />
                  <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 2 }}>服务诊断</Typography>
                  <Box component="pre" sx={{ m: 0, mt: 0.5, maxHeight: 280, overflow: 'auto', whiteSpace: 'pre-wrap', fontSize: 12 }}>{JSON.stringify(selectedProvider?.diagnostics || {}, null, 2)}</Box>
                  <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 2 }}>安全配置</Typography>
                  <Box component="pre" sx={{ m: 0, mt: 0.5, maxHeight: 280, overflow: 'auto', whiteSpace: 'pre-wrap', fontSize: 12 }}>{JSON.stringify(runtime.data?.boundary || {}, null, 2)}</Box>
                </TechnicalDisclosure>
              </Stack>
            )}
          </Paper>

          <Paper component="aside" variant="outlined" aria-labelledby="runtime-workload-title" sx={{ minWidth: 0, p: 2, alignSelf: 'start' }}>
            <Stack direction="row" spacing={1} alignItems="center" justifyContent="space-between">
              <Typography id="runtime-workload-title" component="h2" variant="h3">最近 24 小时</Typography>
              {metrics.isFetching ? <CircularProgress size={18} aria-label="正在刷新" /> : null}
            </Stack>
            <Divider sx={{ my: 2 }} />
            {metrics.isError ? (
              <Alert severity="error" variant="outlined"><AlertTitle>无法读取统计数据</AlertTitle>{errorCopy(metrics.error, '请稍后重试')}</Alert>
            ) : (
              <Stack spacing={2}>
                <FactGrid facts={[
                  ['会话总量', metrics.data?.total ?? 0],
                  ['等待人工', metrics.data?.needs_human ?? 0],
                  ['自动处理中', metrics.data?.ai_active ?? 0],
                  ['WhatsApp', metrics.data?.by_channel?.whatsapp ?? 0],
                ]} />
                {latency ? (
                  <TechnicalDisclosure title="响应时间" summary="高级指标">
                    <FactGrid facts={[
                      ['样本数', latency.sample_count],
                      ['端到端 p50 / p90', `${compactLatency(latency.total_turn.p50_ms)} / ${compactLatency(latency.total_turn.p90_ms)}`],
                      ['运行处理 p50 / p90', `${compactLatency(latency.runtime_total.p50_ms)} / ${compactLatency(latency.runtime_total.p90_ms)}`],
                      ['生成 p50 / p90', `${compactLatency(latency.runtime_eval.p50_ms)} / ${compactLatency(latency.runtime_eval.p90_ms)}`],
                      ['冷加载', latency.cold_load_count],
                      ['慢输入处理', latency.slow_prompt_eval_count],
                    ]} />
                  </TechnicalDisclosure>
                ) : null}
              </Stack>
            )}
          </Paper>
        </Box>
      )}
    </Box>
  )
}

import {
  Alert,
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
import {
  OperatorErrorNotice,
  OperatorFactGrid,
  OperatorLoadingState,
  OperatorTechnicalDisclosure,
  operatorToneColor,
} from '@/app/OperatorPresentation'
import { sanitizeDisplayText } from '@/lib/format'
import { supportApi } from '@/lib/supportApi'
import { runtimePresentation } from '@/lib/supportStatus'
import type { ReleaseReadiness, ReleaseReadinessProfile } from '@/lib/types'
import { RuntimeRecoveryPanel } from './RuntimeRecoveryPanel'

const LazyRuntimeEvidenceAudit = lazy(async () => {
  const module = await import('./RuntimeEvidenceAudit')
  return { default: module.RuntimeEvidenceAudit }
})

const READINESS_PROFILES: Array<{
  key: ReleaseReadinessProfile
  label: string
  description: string
}> = [
  {
    key: 'controlled',
    label: '受控部署',
    description: '所有外部写操作关闭，用于验证服务器、数据库、备份和基础运行。',
  },
  {
    key: 'provider_canary',
    label: '模型小流量',
    description: '仅允许经过证据验收的模型小流量，其他外部能力继续关闭。',
  },
  {
    key: 'full',
    label: '正式生产',
    description: '身份、迁移、队列、存储、Provider、渠道和真实 E2E 证据全部通过。',
  },
]

type ReadinessProfiles = Record<ReleaseReadinessProfile, ReleaseReadiness>

function compactLatency(value: number | null | undefined) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '暂无'
  if (value >= 1000) return `${(value / 1000).toFixed(value >= 10000 ? 0 : 1)}s`
  return `${Math.max(0, Math.round(value))}ms`
}

function ProductionActivationPanel({
  data,
  isLoading,
  isFetching,
  error,
}: {
  data?: ReadinessProfiles
  isLoading: boolean
  isFetching: boolean
  error: unknown
}) {
  const full = data?.full
  return (
    <Paper component="section" variant="outlined" aria-labelledby="production-activation-title" sx={{ mt: 2, p: 2 }}>
      <Stack direction={{ xs: 'column', md: 'row' }} spacing={1} sx={{ alignItems: { md: 'center' }, justifyContent: 'space-between' }}>
        <Box>
          <Typography id="production-activation-title" component="h2" variant="h3">上线与激活</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            同一套门禁分别判断受控部署、模型小流量和正式生产，不再依赖人工猜测。
          </Typography>
        </Box>
        {isFetching ? <CircularProgress size={18} aria-label="正在刷新上线门禁" /> : null}
      </Stack>
      <Divider sx={{ my: 2 }} />
      {error ? (
        <OperatorErrorNotice title="无法读取上线门禁" error={error} fallback="请检查数据库、运行身份和管理员权限" />
      ) : isLoading || !data ? (
        <OperatorLoadingState label="正在核对上线条件…" minHeight={160} />
      ) : (
        <Stack spacing={2}>
          <Box sx={{ display: 'grid', gap: 1.5, gridTemplateColumns: { xs: '1fr', lg: 'repeat(3, minmax(0, 1fr))' } }}>
            {READINESS_PROFILES.map((profile) => {
              const result = data[profile.key]
              const authorized = profile.key === 'full'
                ? result.production_authorized
                : profile.key === 'provider_canary'
                  ? result.provider_enablement_authorized
                  : result.status === 'ready'
              return (
                <Paper key={profile.key} variant="outlined" sx={{ p: 1.5 }}>
                  <Stack direction="row" spacing={1} sx={{ alignItems: 'center', justifyContent: 'space-between' }}>
                    <Typography variant="subtitle1">{profile.label}</Typography>
                    <Chip
                      size="small"
                      color={authorized ? 'success' : result.status === 'ready' ? 'info' : 'warning'}
                      label={authorized ? '已授权' : result.status === 'ready' ? '条件通过' : '被阻断'}
                    />
                  </Stack>
                  <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                    {profile.description}
                  </Typography>
                  <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
                    阻断项 {result.reason_codes.length}
                  </Typography>
                </Paper>
              )
            })}
          </Box>
          <Alert severity={full?.production_authorized ? 'success' : 'warning'} variant="outlined">
            {full?.production_authorized
              ? '正式生产门禁已授权。仍应按变更窗口执行部署并保留回滚观察。'
              : '正式生产尚未授权。请按下方阻断项补齐真实证据，不要直接开启客户流量。'}
          </Alert>
          {full?.reason_codes.length ? (
            <Stack spacing={0.75} aria-label="正式生产阻断项">
              {full.reason_codes.slice(0, 8).map((code) => (
                <Alert key={code} severity="warning" variant="outlined">
                  {sanitizeDisplayText(code)}
                </Alert>
              ))}
              {full.reason_codes.length > 8 ? (
                <Typography variant="caption" color="text.secondary">
                  另有 {full.reason_codes.length - 8} 项，请展开系统信息查看。
                </Typography>
              ) : null}
            </Stack>
          ) : null}
          <OperatorTechnicalDisclosure title="上线门禁系统信息" summary="身份、迁移、存储、队列、渠道与证据">
            <Box component="pre" sx={{ m: 0, maxHeight: 420, overflow: 'auto', whiteSpace: 'pre-wrap', fontSize: 12 }}>
              {JSON.stringify(data, null, 2)}
            </Box>
          </OperatorTechnicalDisclosure>
        </Stack>
      )}
    </Paper>
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
  const readiness = useQuery({
    queryKey: ['releaseReadinessProfiles'],
    queryFn: async (): Promise<ReadinessProfiles> => {
      const [controlled, providerCanary, full] = await Promise.all([
        supportApi.releaseReadiness('controlled'),
        supportApi.releaseReadiness('provider_canary'),
        supportApi.releaseReadiness('full'),
      ])
      return {
        controlled,
        provider_canary: providerCanary,
        full,
      }
    },
    refetchInterval: 30_000,
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
      <Stack
        direction={{ xs: 'column', sm: 'row' }}
        spacing={2}
        sx={{ alignItems: { xs: 'stretch', sm: 'flex-start' }, justifyContent: 'space-between', mb: 2.5 }}
      >
        <Typography component="h1" variant="h1">系统运行</Typography>
        <Stack direction="row" spacing={1} useFlexGap sx={{ alignItems: 'center', flexWrap: 'wrap' }}>
          <Chip color={operatorToneColor(state.tone)} label={state.label} />
          <Button variant="outlined" color="inherit" onClick={() => setShowEvidenceAudit((current) => !current)}>
            {showEvidenceAudit ? '运行概览' : '证据审计'}
          </Button>
        </Stack>
      </Stack>
      {showEvidenceAudit ? (
        <Suspense fallback={<OperatorLoadingState label="正在加载证据审计…" minHeight={240} />}>
          <LazyRuntimeEvidenceAudit />
        </Suspense>
      ) : (
        <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', xl: 'minmax(0, 1.4fr) minmax(300px, 0.8fr)' } }}>
          <Paper component="section" variant="outlined" aria-labelledby="service-readiness-title" sx={{ minWidth: 0, p: 2 }}>
            <Stack direction="row" spacing={1} sx={{ alignItems: 'center', justifyContent: 'space-between' }}>
              <Typography id="service-readiness-title" component="h2" variant="h3">系统状态</Typography>
              {runtime.isFetching ? <CircularProgress size={18} aria-label="正在检查" /> : null}
            </Stack>
            <Divider sx={{ my: 2 }} />
            {runtime.isError ? (
              <OperatorErrorNotice title="无法读取系统状态" error={runtime.error} fallback="请稍后重试" />
            ) : runtime.isLoading ? (
              <OperatorLoadingState label="正在检查…" minHeight={180} />
            ) : (
              <Stack spacing={2}>
                <OperatorFactGrid facts={[
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
                <OperatorTechnicalDisclosure title="系统信息" summary="状态代码、服务配置与诊断">
                  <OperatorFactGrid facts={[
                    ['状态代码', <Box component="code">{sanitizeDisplayText(runtime.data?.status || 'unknown')}</Box>],
                    ['服务提供方', <Box component="code">{sanitizeDisplayText(runtime.data?.configured_provider || selectedProvider?.name || '未配置')}</Box>],
                    ['备用服务', <Box component="code">{sanitizeDisplayText(runtime.data?.fallback_provider || '无')}</Box>],
                    ['运行环境', <Box component="code">{sanitizeDisplayText(runtime.data?.app_env || 'unknown')}</Box>],
                  ]} />
                  <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 2 }}>服务诊断</Typography>
                  <Box component="pre" sx={{ m: 0, mt: 0.5, maxHeight: 280, overflow: 'auto', whiteSpace: 'pre-wrap', fontSize: 12 }}>{JSON.stringify(selectedProvider?.diagnostics || {}, null, 2)}</Box>
                  <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 2 }}>安全配置</Typography>
                  <Box component="pre" sx={{ m: 0, mt: 0.5, maxHeight: 280, overflow: 'auto', whiteSpace: 'pre-wrap', fontSize: 12 }}>{JSON.stringify(runtime.data?.boundary || {}, null, 2)}</Box>
                </OperatorTechnicalDisclosure>
              </Stack>
            )}
          </Paper>

          <Paper component="aside" variant="outlined" aria-labelledby="runtime-workload-title" sx={{ minWidth: 0, p: 2, alignSelf: 'start' }}>
            <Stack direction="row" spacing={1} sx={{ alignItems: 'center', justifyContent: 'space-between' }}>
              <Typography id="runtime-workload-title" component="h2" variant="h3">最近 24 小时</Typography>
              {metrics.isFetching ? <CircularProgress size={18} aria-label="正在刷新" /> : null}
            </Stack>
            <Divider sx={{ my: 2 }} />
            {metrics.isError ? (
              <OperatorErrorNotice title="无法读取统计数据" error={metrics.error} fallback="请稍后重试" />
            ) : (
              <Stack spacing={2}>
                <OperatorFactGrid facts={[
                  ['会话总量', metrics.data?.total ?? 0],
                  ['等待人工', metrics.data?.needs_human ?? 0],
                  ['自动处理中', metrics.data?.ai_active ?? 0],
                  ['WhatsApp', metrics.data?.by_channel?.whatsapp ?? 0],
                ]} />
                {latency ? (
                  <OperatorTechnicalDisclosure title="响应时间" summary="高级指标">
                    <OperatorFactGrid facts={[
                      ['样本数', latency.sample_count],
                      ['端到端 p50 / p90', `${compactLatency(latency.total_turn.p50_ms)} / ${compactLatency(latency.total_turn.p90_ms)}`],
                      ['运行处理 p50 / p90', `${compactLatency(latency.runtime_total.p50_ms)} / ${compactLatency(latency.runtime_total.p90_ms)}`],
                      ['生成 p50 / p90', `${compactLatency(latency.runtime_eval.p50_ms)} / ${compactLatency(latency.runtime_eval.p90_ms)}`],
                      ['冷加载', latency.cold_load_count],
                      ['慢输入处理', latency.slow_prompt_eval_count],
                    ]} />
                  </OperatorTechnicalDisclosure>
                ) : null}
              </Stack>
            )}
          </Paper>
        </Box>
      )}
      <ProductionActivationPanel
        data={readiness.data}
        isLoading={readiness.isLoading}
        isFetching={readiness.isFetching}
        error={readiness.error}
      />
      <RuntimeRecoveryPanel />
    </Box>
  )
}

import OpenInNewRoundedIcon from '@mui/icons-material/OpenInNewRounded'
import {
  Box,
  Button,
  CircularProgress,
  Divider,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material'
import { useQuery } from '@tanstack/react-query'
import {
  OperatorEmptyState,
  OperatorErrorNotice,
  OperatorFactGrid,
  OperatorLoadingState,
  OperatorStatusLine,
  normalizeOperatorTone,
} from '@/app/OperatorPresentation'
import { canonicalAppHref } from '@/app/canonicalRoutes'
import { sanitizeDisplayText } from '@/lib/format'
import { outcomeMetricsApi } from '@/lib/outcomeMetricsApi'
import { supportApi } from '@/lib/supportApi'
import type {
  ControlTowerAction,
  ControlTowerGovernanceLane,
  ControlTowerOutcomeMetric,
} from '@/lib/types'

function ActionRow({ item }: { item: ControlTowerAction }) {
  const href = canonicalAppHref(item.href)
  return (
    <Stack
      component="article"
      direction={{ xs: 'column', sm: 'row' }}
      spacing={2}
      sx={{
        alignItems: { xs: 'stretch', sm: 'center' },
        justifyContent: 'space-between',
        py: 1.5,
      }}
    >
      <Box sx={{ minWidth: 0 }}>
        <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
          <Typography variant="subtitle2">{sanitizeDisplayText(item.label)}</Typography>
          <OperatorStatusLine
            compact
            presentation={{ label: String(item.count), tone: normalizeOperatorTone(item.tone) }}
          />
        </Stack>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
          {sanitizeDisplayText(item.next)}
        </Typography>
      </Box>
      {item.enabled && href ? (
        <Button component="a" href={href} variant="outlined" color="inherit" endIcon={<OpenInNewRoundedIcon />} sx={{ flexShrink: 0 }}>
          去处理
        </Button>
      ) : (
        <Typography variant="caption" color="text.secondary">{item.enabled ? '暂时无法打开' : '无操作权限'}</Typography>
      )}
    </Stack>
  )
}

function GovernanceRow({ item }: { item: ControlTowerGovernanceLane }) {
  const href = canonicalAppHref(item.href)
  return (
    <TableRow hover>
      <TableCell>{sanitizeDisplayText(item.area)}</TableCell>
      <TableCell>
        <OperatorStatusLine
          compact
          presentation={{ label: String(item.value), tone: normalizeOperatorTone(item.risk) }}
        />
      </TableCell>
      <TableCell>{sanitizeDisplayText(item.next)}</TableCell>
      <TableCell>
        {item.enabled && href ? (
          <Button component="a" href={href} size="small" color="inherit">查看</Button>
        ) : (
          <Typography variant="caption" color="text.secondary">不可用</Typography>
        )}
      </TableCell>
    </TableRow>
  )
}

function metricValue(item: ControlTowerOutcomeMetric) {
  if (item.value == null) return '暂无样本'
  if (item.unit === 'ratio') return `${(item.value * 100).toFixed(1)}%`
  if (item.unit === 'seconds') return `${Math.round(item.value)} 秒`
  return item.value.toFixed(item.value % 1 === 0 ? 0 : 1)
}

function metricTarget(item: ControlTowerOutcomeMetric) {
  const operator = item.direction === 'min' ? '≥' : '≤'
  if (item.unit === 'ratio') return `${operator} ${(item.target * 100).toFixed(1)}%`
  if (item.unit === 'seconds') return `${operator} ${Math.round(item.target)} 秒`
  return `${operator} ${item.target}`
}

function metricTrend(item: ControlTowerOutcomeMetric) {
  if (item.previous_value == null || item.delta == null) return '无上一窗口样本'
  const label = item.trend === 'improving' ? '改善' : item.trend === 'declining' ? '下降' : '稳定'
  const delta = item.unit === 'ratio'
    ? `${item.delta >= 0 ? '+' : ''}${(item.delta * 100).toFixed(1)} 个百分点`
    : `${item.delta >= 0 ? '+' : ''}${item.delta.toFixed(1)}${item.unit === 'seconds' ? ' 秒' : ''}`
  return `${label} · ${delta}`
}

function OutcomeMetricRow({ item }: { item: ControlTowerOutcomeMetric }) {
  const href = canonicalAppHref(item.drilldown)
  const denominatorLabel = item.denominator > 0
    ? `${item.numerator} / ${item.denominator}`
    : '暂无可评估样本'
  return (
    <TableRow hover>
      <TableCell>
        <Typography variant="subtitle2">{sanitizeDisplayText(item.label)}</Typography>
        <Typography variant="caption" color="text.secondary">{denominatorLabel}</Typography>
      </TableCell>
      <TableCell>
        <OperatorStatusLine
          compact
          presentation={{ label: metricValue(item), tone: normalizeOperatorTone(item.status) }}
        />
      </TableCell>
      <TableCell>{metricTarget(item)}</TableCell>
      <TableCell>{metricTrend(item)}</TableCell>
      <TableCell>
        {href ? <Button component="a" href={href} size="small" color="inherit">下钻</Button> : null}
      </TableCell>
    </TableRow>
  )
}

export function ControlTowerPage() {
  const tower = useQuery({
    queryKey: ['canonicalControlTower'],
    queryFn: supportApi.controlTower,
    refetchInterval: 30_000,
    retry: false,
  })
  const outcomes = useQuery({
    queryKey: ['canonicalControlTowerOutcomes'],
    queryFn: outcomeMetricsApi.controlTower,
    refetchInterval: 60_000,
    retry: false,
    placeholderData: (previous) => previous,
  })

  return (
    <Box component="main" sx={{ p: { xs: 1.5, md: 2.5 } }}>
      <Stack
        direction={{ xs: 'column', sm: 'row' }}
        spacing={2}
        sx={{
          alignItems: { xs: 'stretch', sm: 'flex-start' },
          justifyContent: 'space-between',
          mb: 2.5,
        }}
      >
        <Typography component="h1" variant="h1">运营监控</Typography>
        {tower.isFetching || outcomes.isFetching ? <CircularProgress size={22} aria-label="正在刷新" /> : null}
      </Stack>
      {tower.isError ? (
        <OperatorErrorNotice title="无法读取运营监控" error={tower.error} fallback="请稍后重试" />
      ) : tower.isLoading ? (
        <OperatorLoadingState label="正在加载…" minHeight={240} />
      ) : tower.data ? (
        <Stack spacing={2}>
          <Paper variant="outlined" aria-label="关键运营指标" sx={{ overflow: 'hidden' }}>
            <Box sx={{ display: 'grid', gridTemplateColumns: { xs: 'repeat(2, minmax(0, 1fr))', md: `repeat(${Math.min(4, Math.max(1, tower.data.kpis.length))}, minmax(0, 1fr))` } }}>
              {tower.data.kpis.map((item, index) => (
                <Box key={item.key} sx={{ borderBottom: { xs: 1, md: 0 }, borderColor: 'divider', borderRight: { md: index === tower.data.kpis.length - 1 ? 0 : 1 }, minWidth: 0, p: 2 }}>
                  <Typography variant="caption" color="text.secondary">{sanitizeDisplayText(item.label)}</Typography>
                  <Typography variant="h2" sx={{ mt: 0.5, fontVariantNumeric: 'tabular-nums' }}>{item.value}</Typography>
                </Box>
              ))}
            </Box>
          </Paper>

          <Paper component="section" variant="outlined" aria-labelledby="outcome-metrics-title" sx={{ minWidth: 0, p: 2 }}>
            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} sx={{ alignItems: { sm: 'center' }, justifyContent: 'space-between' }}>
              <Box>
                <Typography id="outcome-metrics-title" component="h2" variant="h3">业务结果</Typography>
                <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
                  明确分母、目标和上一窗口趋势；技术成功不等于业务完成。
                </Typography>
              </Box>
              {outcomes.data ? (
                <Typography variant="caption" color="text.secondary">
                  最近 {outcomes.data.window.days} 天 · {outcomes.data.source_ticket_count} 个案例
                </Typography>
              ) : null}
            </Stack>
            <Divider sx={{ my: 2 }} />
            {outcomes.isError ? (
              <OperatorErrorNotice title="无法读取业务结果" error={outcomes.error} fallback="保留上次确认结果并稍后重试" />
            ) : outcomes.isLoading ? (
              <OperatorLoadingState label="正在计算业务结果…" minHeight={180} />
            ) : outcomes.data?.items.length ? (
              <TableContainer>
                <Table size="small" aria-label="业务结果指标">
                  <TableHead>
                    <TableRow>
                      <TableCell>指标与分母</TableCell>
                      <TableCell>当前</TableCell>
                      <TableCell>目标</TableCell>
                      <TableCell>上一窗口</TableCell>
                      <TableCell>操作</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>{outcomes.data.items.map((item) => <OutcomeMetricRow key={item.key} item={item} />)}</TableBody>
                </Table>
              </TableContainer>
            ) : <OperatorEmptyState title="暂无业务结果样本" description="当案例产生结构化关闭、通知和动作结果后，这里会显示可评估指标。" />}
          </Paper>

          <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', xl: 'minmax(0, 1.4fr) minmax(300px, 0.8fr)' } }}>
            <Paper component="section" variant="outlined" aria-labelledby="control-actions-title" sx={{ minWidth: 0, p: 2 }}>
              <Stack direction="row" spacing={1} sx={{ alignItems: 'center', justifyContent: 'space-between' }}>
                <Typography id="control-actions-title" component="h2" variant="h3">需要处理</Typography>
                <Typography variant="body2" color="text.secondary" sx={{ fontVariantNumeric: 'tabular-nums' }}>
                  {tower.data.manager_actions.reduce((sum, item) => sum + item.count, 0)} 项
                </Typography>
              </Stack>
              <Divider sx={{ mt: 2 }} />
              {tower.data.manager_actions.length
                ? <Stack divider={<Divider flexItem />}>{tower.data.manager_actions.map((item) => <ActionRow key={item.key} item={item} />)}</Stack>
                : <OperatorEmptyState title="暂无管理待办" description="无需处理" />}
            </Paper>

            <Paper component="aside" variant="outlined" aria-labelledby="team-workload-title" sx={{ minWidth: 0, p: 2, alignSelf: 'start' }}>
              <Typography id="team-workload-title" component="h2" variant="h3">团队负载</Typography>
              <Divider sx={{ my: 2 }} />
              {tower.data.team_workload.length ? (
                <Stack divider={<Divider flexItem />}>
                  {tower.data.team_workload.map((team) => (
                    <Box component="article" key={`${team.team_id || 'none'}-${team.team_name}`} sx={{ py: 1.5 }}>
                      <Typography variant="subtitle2">{sanitizeDisplayText(team.team_name)}</Typography>
                      <Box sx={{ mt: 1 }}>
                        <OperatorFactGrid columns={4} facts={[
                          ['处理中', team.active_tickets],
                          ['未分配', team.unassigned],
                          ['即将超时', team.sla_risk],
                          ['已超时', team.overdue],
                        ]} />
                      </Box>
                    </Box>
                  ))}
                </Stack>
              ) : <OperatorEmptyState title="暂无团队数据" description="暂无数据" />}
            </Paper>
          </Box>

          <Paper component="section" variant="outlined" aria-labelledby="governance-lanes-title" sx={{ minWidth: 0, p: 2 }}>
            <Typography id="governance-lanes-title" component="h2" variant="h3">系统与配置问题</Typography>
            <Divider sx={{ my: 2 }} />
            <TableContainer>
              <Table size="small" aria-label="系统与配置问题列表">
                <TableHead><TableRow><TableCell>问题类型</TableCell><TableCell>待处理</TableCell><TableCell>下一步</TableCell><TableCell>操作</TableCell></TableRow></TableHead>
                <TableBody>{tower.data.governance_lanes.map((item) => <GovernanceRow key={item.key} item={item} />)}</TableBody>
              </Table>
            </TableContainer>
          </Paper>
        </Stack>
      ) : null}
    </Box>
  )
}

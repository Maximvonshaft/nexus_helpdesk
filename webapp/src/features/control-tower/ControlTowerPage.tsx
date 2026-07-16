import OpenInNewRoundedIcon from '@mui/icons-material/OpenInNewRounded'
import {
  Alert,
  AlertTitle,
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
import { canonicalAppHref } from '@/app/canonicalRoutes'
import { sanitizeDisplayText } from '@/lib/format'
import { supportApi } from '@/lib/supportApi'
import type { BadgeTone, ControlTowerAction, ControlTowerGovernanceLane } from '@/lib/types'

function errorCopy(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback
}

function safeTone(value: string | null | undefined): BadgeTone {
  return value === 'success' || value === 'warning' || value === 'danger' || value === 'default'
    ? value
    : 'default'
}

const toneColor: Record<BadgeTone, string> = {
  default: 'text.secondary',
  warning: 'warning.main',
  success: 'success.main',
  danger: 'error.main',
}

function StatusCount({ value, tone }: { value: number; tone: BadgeTone }) {
  return (
    <Stack direction="row" spacing={0.75} alignItems="center">
      <Box aria-hidden="true" sx={{ bgcolor: toneColor[tone], borderRadius: '50%', height: 8, width: 8 }} />
      <Typography variant="subtitle2" sx={{ fontVariantNumeric: 'tabular-nums' }}>{value}</Typography>
    </Stack>
  )
}

function EmptyState({ title, description }: { title: string; description: string }) {
  return (
    <Stack role="status" alignItems="center" justifyContent="center" spacing={0.75} sx={{ minHeight: 140, p: 3, textAlign: 'center' }}>
      <Typography variant="subtitle2">{title}</Typography>
      <Typography variant="body2" color="text.secondary">{description}</Typography>
    </Stack>
  )
}

function ActionRow({ item }: { item: ControlTowerAction }) {
  const href = canonicalAppHref(item.href)
  const tone = safeTone(item.tone)
  return (
    <Stack component="article" direction={{ xs: 'column', sm: 'row' }} spacing={2} alignItems={{ xs: 'stretch', sm: 'center' }} justifyContent="space-between" sx={{ py: 1.5 }}>
      <Box sx={{ minWidth: 0 }}>
        <Stack direction="row" spacing={1} alignItems="center">
          <Typography variant="subtitle2">{sanitizeDisplayText(item.label)}</Typography>
          <StatusCount value={item.count} tone={tone} />
        </Stack>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>{sanitizeDisplayText(item.next)}</Typography>
      </Box>
      {item.enabled && href ? (
        <Button component="a" href={href} variant="outlined" color="inherit" endIcon={<OpenInNewRoundedIcon />} sx={{ flexShrink: 0 }}>
          打开处理页面
        </Button>
      ) : (
        <Typography variant="caption" color="text.secondary">{item.enabled ? '后端未返回受支持的处理入口' : '当前账号无执行权限'}</Typography>
      )}
    </Stack>
  )
}

function GovernanceRow({ item }: { item: ControlTowerGovernanceLane }) {
  const href = canonicalAppHref(item.href)
  return (
    <TableRow hover>
      <TableCell>{sanitizeDisplayText(item.area)}</TableCell>
      <TableCell><StatusCount value={item.value} tone={safeTone(item.risk)} /></TableCell>
      <TableCell>{sanitizeDisplayText(item.next)}</TableCell>
      <TableCell>
        {item.enabled && href ? <Button component="a" href={href} size="small" color="inherit">查看</Button> : <Typography variant="caption" color="text.secondary">不可用</Typography>}
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

  return (
    <Box component="main" sx={{ p: { xs: 1.5, md: 2.5 } }}>
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} alignItems={{ xs: 'stretch', sm: 'flex-start' }} justifyContent="space-between" sx={{ mb: 2.5 }}>
        <Box>
          <Typography component="h1" variant="h1">运营监控</Typography>
          <Typography color="text.secondary" sx={{ mt: 0.75 }}>查看未分配任务、SLA 风险、渠道异常和需要修复的工作，并进入对应的唯一处理页面。</Typography>
        </Box>
        {tower.isFetching ? <CircularProgress size={22} aria-label="正在刷新" /> : null}
      </Stack>

      {tower.isError ? (
        <Alert severity="error" variant="outlined"><AlertTitle>无法读取运营监控</AlertTitle>{errorCopy(tower.error, '请稍后重试')}</Alert>
      ) : tower.isLoading ? (
        <Stack role="status" alignItems="center" spacing={1.5} sx={{ minHeight: 240, justifyContent: 'center' }}><CircularProgress size={30} /><Typography variant="subtitle2">正在汇总当前账号可见的工作和风险…</Typography></Stack>
      ) : tower.data ? (
        <Stack spacing={2}>
          <Paper variant="outlined" aria-label="关键运营指标" sx={{ overflow: 'hidden' }}>
            <Box sx={{ display: 'grid', gridTemplateColumns: { xs: 'repeat(2, minmax(0, 1fr))', md: `repeat(${Math.min(4, Math.max(1, tower.data.kpis.length))}, minmax(0, 1fr))` } }}>
              {tower.data.kpis.map((item, index) => (
                <Box key={item.key} sx={{ borderBottom: { xs: 1, md: 0 }, borderColor: 'divider', borderRight: { md: index === tower.data.kpis.length - 1 ? 0 : 1 }, minWidth: 0, p: 2 }}>
                  <Typography variant="caption" color="text.secondary">{sanitizeDisplayText(item.label)}</Typography>
                  <Typography variant="h2" sx={{ mt: 0.5, fontVariantNumeric: 'tabular-nums' }}>{item.value}</Typography>
                  <Typography variant="caption" color="text.secondary">{sanitizeDisplayText(item.hint)}</Typography>
                </Box>
              ))}
            </Box>
          </Paper>

          <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', xl: 'minmax(0, 1.4fr) minmax(300px, 0.8fr)' } }}>
            <Paper component="section" variant="outlined" aria-labelledby="control-actions-title" sx={{ minWidth: 0, p: 2 }}>
              <Stack direction="row" spacing={1} alignItems="center" justifyContent="space-between">
                <Typography id="control-actions-title" component="h2" variant="h3">需要处理</Typography>
                <Typography variant="body2" color="text.secondary" sx={{ fontVariantNumeric: 'tabular-nums' }}>{tower.data.manager_actions.reduce((sum, item) => sum + item.count, 0)} 项</Typography>
              </Stack>
              <Divider sx={{ mt: 2 }} />
              {tower.data.manager_actions.length
                ? <Stack divider={<Divider flexItem />}>{tower.data.manager_actions.map((item) => <ActionRow key={item.key} item={item} />)}</Stack>
                : <EmptyState title="当前没有管理待办" description="当前可见范围没有需要管理介入的工作。" />}
            </Paper>

            <Paper component="aside" variant="outlined" aria-labelledby="team-workload-title" sx={{ minWidth: 0, p: 2, alignSelf: 'start' }}>
              <Typography id="team-workload-title" component="h2" variant="h3">团队负载</Typography>
              <Divider sx={{ my: 2 }} />
              {tower.data.team_workload.length ? (
                <Stack divider={<Divider flexItem />}>
                  {tower.data.team_workload.map((team) => (
                    <Box component="article" key={`${team.team_id || 'none'}-${team.team_name}`} sx={{ py: 1.5 }}>
                      <Typography variant="subtitle2">{sanitizeDisplayText(team.team_name)}</Typography>
                      <Box component="dl" sx={{ display: 'grid', gap: 1, gridTemplateColumns: 'repeat(4, 1fr)', m: 0, mt: 1 }}>
                        {[
                          ['处理中', team.active_tickets],
                          ['未分配', team.unassigned],
                          ['SLA 风险', team.sla_risk],
                          ['已超时', team.overdue],
                        ].map(([label, value]) => (
                          <Box key={String(label)}><Typography component="dt" variant="caption" color="text.secondary">{label}</Typography><Typography component="dd" variant="subtitle2" sx={{ m: 0, mt: 0.25, fontVariantNumeric: 'tabular-nums' }}>{value}</Typography></Box>
                        ))}
                      </Box>
                    </Box>
                  ))}
                </Stack>
              ) : <EmptyState title="暂无团队负载" description="当前账号没有可见的团队工作数据。" />}
            </Paper>
          </Box>

          <Paper component="section" variant="outlined" aria-labelledby="governance-lanes-title" sx={{ minWidth: 0, p: 2 }}>
            <Typography id="governance-lanes-title" component="h2" variant="h3">运行与治理风险</Typography>
            <Divider sx={{ my: 2 }} />
            <TableContainer>
              <Table size="small" aria-label="运行与治理风险列表">
                <TableHead><TableRow><TableCell>领域</TableCell><TableCell>待处理</TableCell><TableCell>下一步</TableCell><TableCell>入口</TableCell></TableRow></TableHead>
                <TableBody>{tower.data.governance_lanes.map((item) => <GovernanceRow key={item.key} item={item} />)}</TableBody>
              </Table>
            </TableContainer>
          </Paper>
        </Stack>
      ) : null}
    </Box>
  )
}

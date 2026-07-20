import RefreshRoundedIcon from '@mui/icons-material/RefreshRounded'
import {
  Alert,
  Box,
  Button,
  Chip,
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
  OperatorTechnicalDisclosure,
} from '@/app/OperatorPresentation'
import { formatDateTime, sanitizeDisplayText } from '@/lib/format'
import { supportApi } from '@/lib/supportApi'

function auditActionLabel(action: string) {
  const labels: Record<string, string> = {
    'auth.password_changed': '用户修改密码',
    'user.create': '创建用户',
    'user.update': '更新用户',
    'user.activate': '启用用户',
    'user.deactivate': '停用用户',
    'user.reset_password': '管理员重置密码',
    'user.team_cleared': '清除用户团队',
    'team.create': '创建团队',
    'team.update': '更新团队',
  }
  return labels[action] || action
}

export function SecurityAuditPanel({ readOnly }: { readOnly: boolean }) {
  const audit = useQuery({
    queryKey: ['securityAudit'],
    queryFn: () => supportApi.securityAudit(100),
    refetchInterval: 30_000,
    retry: false,
  })

  if (audit.isLoading) return <OperatorLoadingState label="正在加载安全审计…" minHeight={260} />
  if (audit.isError) return <OperatorErrorNotice title="无法读取安全审计" error={audit.error} fallback="请稍后重试" />
  if (!audit.data) return null

  return (
    <Stack spacing={2}>
      <Paper component="section" variant="outlined" aria-labelledby="security-summary-title" sx={{ p: 2 }}>
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} sx={{ alignItems: { xs: 'stretch', sm: 'center' }, justifyContent: 'space-between' }}>
          <Box>
            <Typography id="security-summary-title" component="h2" variant="h2">安全治理概览</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
              有效权限由角色默认值和用户覆盖共同计算；高风险权限与管理操作需要定期复核。
            </Typography>
          </Box>
          <Button color="inherit" variant="outlined" startIcon={audit.isFetching ? <CircularProgress size={16} /> : <RefreshRoundedIcon />} disabled={audit.isFetching} onClick={() => audit.refetch()}>
            刷新
          </Button>
        </Stack>
        <Divider sx={{ my: 2 }} />
        {readOnly ? <Alert severity="info" variant="outlined" sx={{ mb: 2 }}>当前为只读审计视图。</Alert> : null}
        <OperatorFactGrid columns={4} facts={[
          ['用户总数', audit.data.summary.total_users],
          ['活跃用户', audit.data.summary.active_users],
          ['停用用户', audit.data.summary.inactive_users],
          ['管理员', audit.data.summary.admin_users],
          ['审计员', audit.data.summary.auditor_users],
          ['高风险覆盖', audit.data.summary.high_risk_overrides],
          ['24 小时管理操作', audit.data.summary.recent_audit_24h],
          ['权限目录', audit.data.summary.catalog_size],
        ]} />
      </Paper>

      <Paper component="section" variant="outlined" aria-labelledby="security-users-title" sx={{ p: 2 }}>
        <Typography id="security-users-title" component="h2" variant="h3">权限风险视图</Typography>
        <Divider sx={{ my: 2 }} />
        {!audit.data.users.length ? <OperatorEmptyState title="暂无用户权限数据" description="暂无数据" /> : (
          <TableContainer>
            <Table size="small" aria-label="用户权限风险">
              <TableHead>
                <TableRow>
                  <TableCell>用户</TableCell>
                  <TableCell>角色</TableCell>
                  <TableCell>状态</TableCell>
                  <TableCell align="right">有效权限</TableCell>
                  <TableCell align="right">权限覆盖</TableCell>
                  <TableCell align="right">高风险权限</TableCell>
                  <TableCell>详情</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {audit.data.users.map((user) => (
                  <TableRow key={user.user_id} hover>
                    <TableCell>
                      <Typography variant="subtitle2">{sanitizeDisplayText(user.display_name)}</Typography>
                      <Typography variant="caption" color="text.secondary">{sanitizeDisplayText(user.username)}</Typography>
                    </TableCell>
                    <TableCell>{sanitizeDisplayText(user.role)}</TableCell>
                    <TableCell><Chip size="small" color={user.is_active ? 'success' : 'default'} label={user.is_active ? '启用' : '停用'} /></TableCell>
                    <TableCell align="right">{user.effective_capabilities.length}</TableCell>
                    <TableCell align="right">{user.override_count}</TableCell>
                    <TableCell align="right">
                      <Chip size="small" color={user.high_risk_count > 0 ? 'warning' : 'success'} label={String(user.high_risk_count)} />
                    </TableCell>
                    <TableCell>
                      <OperatorTechnicalDisclosure title="查看权限" summary={`${user.effective_capabilities.length} 项`}>
                        <Stack direction="row" spacing={0.75} useFlexGap sx={{ flexWrap: 'wrap' }}>
                          {user.effective_capabilities.map((capability) => <Chip key={capability} size="small" variant="outlined" label={sanitizeDisplayText(capability)} />)}
                        </Stack>
                      </OperatorTechnicalDisclosure>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </Paper>

      <Paper component="section" variant="outlined" aria-labelledby="security-audit-title" sx={{ p: 2 }}>
        <Typography id="security-audit-title" component="h2" variant="h3">最近管理操作</Typography>
        <Divider sx={{ my: 2 }} />
        {!audit.data.recent_audit.length ? <OperatorEmptyState title="暂无管理操作" description="当前时间范围内没有审计记录。" /> : (
          <TableContainer>
            <Table size="small" aria-label="最近管理操作">
              <TableHead>
                <TableRow>
                  <TableCell>时间</TableCell>
                  <TableCell>操作人</TableCell>
                  <TableCell>操作</TableCell>
                  <TableCell>对象</TableCell>
                  <TableCell>变更证据</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {audit.data.recent_audit.map((item) => (
                  <TableRow key={item.id} hover>
                    <TableCell>{formatDateTime(item.created_at)}</TableCell>
                    <TableCell>{sanitizeDisplayText(item.actor_display_name || item.actor_username || '系统')}</TableCell>
                    <TableCell>{sanitizeDisplayText(auditActionLabel(item.action))}</TableCell>
                    <TableCell>{sanitizeDisplayText(item.target_type)}{item.target_id ? ` #${item.target_id}` : ''}</TableCell>
                    <TableCell sx={{ minWidth: 220 }}>
                      <OperatorTechnicalDisclosure title="查看变更" summary="已脱敏">
                        <Typography component="pre" variant="caption" sx={{ m: 0, maxHeight: 260, overflow: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                          {JSON.stringify({ before: item.old_value ?? null, after: item.new_value ?? null }, null, 2)}
                        </Typography>
                      </OperatorTechnicalDisclosure>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </Paper>
    </Stack>
  )
}

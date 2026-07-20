import AddRoundedIcon from '@mui/icons-material/AddRounded'
import EditRoundedIcon from '@mui/icons-material/EditRounded'
import GroupOffRoundedIcon from '@mui/icons-material/GroupOffRounded'
import GroupWorkRoundedIcon from '@mui/icons-material/GroupWorkRounded'
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
  Divider,
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
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useState } from 'react'
import {
  OperatorEmptyState,
  OperatorErrorNotice,
  OperatorLoadingState,
} from '@/app/OperatorPresentation'
import { formatDateTime } from '@/lib/format'
import { supportApi } from '@/lib/supportApi'
import type { IdentityTeam } from '@/lib/types'

type TeamDraft = {
  name: string
  teamType: string
  marketId: string
}

const emptyTeamDraft: TeamDraft = { name: '', teamType: 'support', marketId: '' }

export function TeamGovernance({
  teams,
  isLoading,
  error,
}: {
  teams: IdentityTeam[]
  isLoading: boolean
  error: unknown
}) {
  const queryClient = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [selectedTeam, setSelectedTeam] = useState<IdentityTeam | null>(null)
  const [draft, setDraft] = useState<TeamDraft>(emptyTeamDraft)

  const invalidate = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['identityTeams'] }),
      queryClient.invalidateQueries({ queryKey: ['adminUsers'] }),
      queryClient.invalidateQueries({ queryKey: ['securityAudit'] }),
    ])
  }

  const saveTeam = useMutation({
    mutationFn: () => {
      const marketId = draft.marketId.trim() ? Number(draft.marketId) : null
      if (marketId !== null && (!Number.isInteger(marketId) || marketId <= 0)) {
        throw new Error('市场编号必须为正整数')
      }
      const payload = {
        name: draft.name.trim(),
        team_type: draft.teamType.trim(),
        market_id: marketId,
      }
      return selectedTeam
        ? supportApi.updateIdentityTeam(selectedTeam.id, payload)
        : supportApi.createIdentityTeam(payload)
    },
    onSuccess: async () => {
      setDialogOpen(false)
      setSelectedTeam(null)
      setDraft(emptyTeamDraft)
      await invalidate()
    },
  })

  const toggleTeam = useMutation({
    mutationFn: (team: IdentityTeam) => supportApi.updateIdentityTeam(team.id, { is_active: !team.is_active }),
    onSuccess: invalidate,
  })

  const openCreate = () => {
    saveTeam.reset()
    setSelectedTeam(null)
    setDraft(emptyTeamDraft)
    setDialogOpen(true)
  }

  const openEdit = (team: IdentityTeam) => {
    saveTeam.reset()
    setSelectedTeam(team)
    setDraft({
      name: team.name,
      teamType: team.team_type,
      marketId: team.market_id ? String(team.market_id) : '',
    })
    setDialogOpen(true)
  }

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (draft.name.trim() && draft.teamType.trim()) saveTeam.mutate()
  }

  return (
    <Paper component="section" variant="outlined" aria-labelledby="team-governance-title" sx={{ p: 2 }}>
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} sx={{ alignItems: { xs: 'stretch', sm: 'center' }, justifyContent: 'space-between' }}>
        <Box>
          <Typography id="team-governance-title" component="h2" variant="h2">团队与工作范围</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            团队是用户工作范围的组织边界。停用团队前必须先迁移所有活跃用户。
          </Typography>
        </Box>
        <Button variant="contained" startIcon={<AddRoundedIcon />} onClick={openCreate}>新建团队</Button>
      </Stack>
      <Divider sx={{ my: 2 }} />

      {error ? <OperatorErrorNotice title="无法读取团队" error={error} fallback="请稍后重试" /> : null}
      {toggleTeam.isError ? <Box sx={{ mb: 2 }}><OperatorErrorNotice title="团队状态更新失败" error={toggleTeam.error} fallback="请检查团队成员后重试" /></Box> : null}
      {isLoading ? <OperatorLoadingState label="正在加载团队…" minHeight={180} /> : !teams.length ? (
        <OperatorEmptyState title="暂无团队" description="创建第一个团队后即可给用户分配工作范围。" />
      ) : (
        <TableContainer>
          <Table size="small" aria-label="团队治理列表">
            <TableHead>
              <TableRow>
                <TableCell>团队</TableCell>
                <TableCell>类型</TableCell>
                <TableCell>市场</TableCell>
                <TableCell align="right">活跃用户</TableCell>
                <TableCell>状态</TableCell>
                <TableCell>最近更新</TableCell>
                <TableCell align="right">操作</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {teams.map((team) => (
                <TableRow key={team.id} hover>
                  <TableCell>
                    <Typography variant="subtitle2">{team.name}</Typography>
                    <Typography variant="caption" color="text.secondary">#{team.id}</Typography>
                  </TableCell>
                  <TableCell>{team.team_type}</TableCell>
                  <TableCell>{team.market_id ? `#${team.market_id}` : '全局/未绑定'}</TableCell>
                  <TableCell align="right">{team.active_users}</TableCell>
                  <TableCell><Chip size="small" color={team.is_active ? 'success' : 'default'} label={team.is_active ? '启用' : '停用'} /></TableCell>
                  <TableCell>{formatDateTime(team.updated_at)}</TableCell>
                  <TableCell align="right">
                    <Stack direction="row" spacing={0.5} sx={{ justifyContent: 'flex-end' }}>
                      <Button size="small" color="inherit" startIcon={<EditRoundedIcon />} onClick={() => openEdit(team)}>编辑</Button>
                      <Button
                        size="small"
                        color={team.is_active ? 'warning' : 'success'}
                        startIcon={team.is_active ? <GroupOffRoundedIcon /> : <GroupWorkRoundedIcon />}
                        disabled={toggleTeam.isPending || (team.is_active && team.active_users > 0)}
                        onClick={() => toggleTeam.mutate(team)}
                      >
                        {team.is_active ? '停用' : '启用'}
                      </Button>
                    </Stack>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      <Dialog open={dialogOpen} onClose={() => { if (!saveTeam.isPending) setDialogOpen(false) }} fullWidth maxWidth="sm">
        <Box component="form" onSubmit={submit}>
          <DialogTitle>{selectedTeam ? '编辑团队' : '新建团队'}</DialogTitle>
          <DialogContent>
            <DialogContentText>
              市场编号来自现有市场配置；留空表示不绑定单一市场。团队名称在系统内必须唯一。
            </DialogContentText>
            <Stack spacing={2} sx={{ mt: 2 }}>
              {saveTeam.isError ? <OperatorErrorNotice title="保存失败" error={saveTeam.error} fallback="请检查名称和市场配置" /> : null}
              <TextField label="团队名称" required value={draft.name} onChange={(event) => setDraft((current) => ({ ...current, name: event.target.value }))} />
              <TextField label="团队类型" required value={draft.teamType} onChange={(event) => setDraft((current) => ({ ...current, teamType: event.target.value }))} />
              <TextField label="市场编号" type="number" value={draft.marketId} onChange={(event) => setDraft((current) => ({ ...current, marketId: event.target.value }))} inputProps={{ min: 1 }} />
              {selectedTeam?.active_users ? (
                <Alert severity="info" variant="outlined">当前团队有 {selectedTeam.active_users} 个活跃用户。修改市场会影响其工作范围。</Alert>
              ) : null}
            </Stack>
          </DialogContent>
          <DialogActions>
            <Button color="inherit" disabled={saveTeam.isPending} onClick={() => setDialogOpen(false)}>取消</Button>
            <Button type="submit" variant="contained" disabled={!draft.name.trim() || !draft.teamType.trim() || saveTeam.isPending} startIcon={saveTeam.isPending ? <CircularProgress color="inherit" size={16} /> : undefined}>
              {saveTeam.isPending ? '保存中…' : '保存'}
            </Button>
          </DialogActions>
        </Box>
      </Dialog>
    </Paper>
  )
}

import AdminPanelSettingsRoundedIcon from '@mui/icons-material/AdminPanelSettingsRounded'
import GroupsRoundedIcon from '@mui/icons-material/GroupsRounded'
import ManageAccountsRoundedIcon from '@mui/icons-material/ManageAccountsRounded'
import SecurityRoundedIcon from '@mui/icons-material/SecurityRounded'
import {
  Alert,
  Box,
  Paper,
  Stack,
  Tab,
  Tabs,
  Typography,
} from '@mui/material'
import { useQuery } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { OperatorErrorNotice } from '@/app/OperatorPresentation'
import { useSession } from '@/hooks/useAuth'
import { supportApi } from '@/lib/supportApi'
import { SecurityAuditPanel } from './SecurityAuditPanel'
import { TeamGovernance } from './TeamGovernance'
import { UserGovernance } from './UserGovernance'

type AdministrationTab = 'users' | 'teams' | 'security'

export function AdministrationPage() {
  const session = useSession()
  const capabilities = useMemo(() => new Set(session.data?.capabilities ?? []), [session.data?.capabilities])
  const canManageUsers = capabilities.has('user.manage')
  const canReadSecurity = canManageUsers || capabilities.has('security.read') || capabilities.has('audit.read')
  const [tab, setTab] = useState<AdministrationTab>(canManageUsers ? 'users' : 'security')

  const roles = useQuery({
    queryKey: ['identityRolePolicies'],
    queryFn: supportApi.rolePolicies,
    enabled: canManageUsers,
    retry: false,
  })
  const teams = useQuery({
    queryKey: ['identityTeams'],
    queryFn: supportApi.identityTeams,
    enabled: canManageUsers,
    retry: false,
  })

  useEffect(() => { document.title = '系统管理 · Nexus OSR' }, [])
  useEffect(() => {
    if (!canManageUsers && tab !== 'security') setTab('security')
  }, [canManageUsers, tab])

  const referenceError = roles.error || teams.error

  return (
    <Box component="main" sx={{ p: { xs: 1.5, md: 2.5 } }}>
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} sx={{ alignItems: { xs: 'stretch', sm: 'center' }, justifyContent: 'space-between' }}>
        <Box>
          <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
            <AdminPanelSettingsRoundedIcon color="primary" aria-hidden="true" />
            <Typography component="h1" variant="h1">系统管理</Typography>
          </Stack>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.75 }}>
            管理人员、角色权限、团队工作范围与安全审计。所有变更由服务端授权并记录审计。
          </Typography>
        </Box>
      </Stack>

      {!canManageUsers && canReadSecurity ? (
        <Alert severity="info" variant="outlined" sx={{ mt: 2 }}>
          当前账号为只读审计视图，不能修改用户、角色或团队。
        </Alert>
      ) : null}
      {referenceError ? (
        <Box sx={{ mt: 2 }}>
          <OperatorErrorNotice title="无法读取身份治理配置" error={referenceError} fallback="请稍后重试" />
        </Box>
      ) : null}

      <Paper variant="outlined" sx={{ mt: 2.5, overflow: 'hidden' }}>
        <Tabs
          value={tab}
          onChange={(_, next: AdministrationTab) => setTab(next)}
          variant="scrollable"
          scrollButtons="auto"
          aria-label="系统管理分类"
        >
          {canManageUsers ? <Tab icon={<ManageAccountsRoundedIcon />} iconPosition="start" value="users" label="用户与权限" /> : null}
          {canManageUsers ? <Tab icon={<GroupsRoundedIcon />} iconPosition="start" value="teams" label="团队与范围" /> : null}
          {canReadSecurity ? <Tab icon={<SecurityRoundedIcon />} iconPosition="start" value="security" label="安全审计" /> : null}
        </Tabs>
      </Paper>

      <Box sx={{ mt: 2 }}>
        {tab === 'users' && canManageUsers ? (
          <UserGovernance
            currentUserId={session.data?.id ?? 0}
            roles={roles.data ?? []}
            teams={teams.data ?? []}
            referencesLoading={roles.isLoading || teams.isLoading}
          />
        ) : null}
        {tab === 'teams' && canManageUsers ? (
          <TeamGovernance teams={teams.data ?? []} isLoading={teams.isLoading} error={teams.error} />
        ) : null}
        {tab === 'security' && canReadSecurity ? <SecurityAuditPanel readOnly={!canManageUsers} /> : null}
      </Box>
    </Box>
  )
}

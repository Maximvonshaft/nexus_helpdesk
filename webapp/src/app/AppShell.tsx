import LogoutRoundedIcon from '@mui/icons-material/LogoutRounded'
import {
  AppBar,
  Avatar,
  Box,
  Button,
  Chip,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  Toolbar,
  Typography,
} from '@mui/material'
import type { SelectChangeEvent } from '@mui/material/Select'
import type { ReactNode } from 'react'
import type { AuthorizedWorkspaceScope } from '@/lib/operatorWorkspaceTypes'
import { AppNavigation } from './AppNavigation'
import type { AppRouteKey } from './navigation'

function channelLabel(channel: string) {
  if (channel === 'webchat') return '网页客服'
  if (channel === 'whatsapp') return 'WhatsApp'
  if (channel === 'email') return '邮件'
  if (channel === 'voice') return '语音'
  return channel
}

function scopeLabel(scope: AuthorizedWorkspaceScope, duplicatePosition?: number) {
  const base = `${scope.country_code} · ${channelLabel(scope.channel_key)}`
  return duplicatePosition ? `${base} · 范围 ${duplicatePosition}` : base
}

function sameScope(left: AuthorizedWorkspaceScope, right: AuthorizedWorkspaceScope) {
  return left.tenant_key === right.tenant_key
    && left.country_code === right.country_code
    && left.channel_key === right.channel_key
}

function initials(value: string) {
  const compact = value.trim()
  if (!compact) return 'N'
  return [...compact].slice(0, 2).join('').toUpperCase()
}

export function AppShell({
  activeRoute,
  capabilities,
  userLabel,
  scopes = [],
  selectedScope,
  onScopeChange,
  onLogout,
  children,
}: {
  activeRoute: AppRouteKey
  capabilities: Set<string>
  userLabel: string
  scopes?: AuthorizedWorkspaceScope[]
  selectedScope?: AuthorizedWorkspaceScope | null
  onScopeChange?: (scope: AuthorizedWorkspaceScope) => void
  onLogout: () => void
  children: ReactNode
}) {
  const selectedIndex = selectedScope ? scopes.findIndex((scope) => sameScope(scope, selectedScope)) : -1
  const labelCounts = new Map<string, number>()
  for (const scope of scopes) {
    const label = `${scope.country_code}\u0000${scope.channel_key}`
    labelCounts.set(label, (labelCounts.get(label) ?? 0) + 1)
  }

  const handleScopeChange = (event: SelectChangeEvent<string>) => {
    const next = scopes[Number.parseInt(event.target.value, 10)]
    if (next) onScopeChange?.(next)
  }

  return (
    <Box sx={{ minHeight: '100dvh', bgcolor: 'background.default' }}>
      <Box
        component="a"
        href="#nd-main-content"
        sx={{
          position: 'fixed',
          left: 16,
          top: 8,
          zIndex: (theme) => theme.zIndex.tooltip + 1,
          transform: 'translateY(-160%)',
          bgcolor: 'background.paper',
          color: 'primary.main',
          border: 1,
          borderColor: 'primary.main',
          borderRadius: 1,
          px: 2,
          py: 1,
          textDecoration: 'none',
          fontWeight: 700,
          '&:focus': { transform: 'translateY(0)' },
        }}
      >
        跳到主要内容
      </Box>

      <AppBar
        position="sticky"
        color="inherit"
        elevation={0}
        sx={{ borderBottom: 1, borderColor: 'divider', bgcolor: 'rgba(255,255,255,0.96)', backdropFilter: 'blur(12px)' }}
      >
        <Toolbar
          sx={{
            minHeight: { xs: 64, lg: 68 },
            gap: { xs: 1.5, lg: 2.5 },
            px: { xs: 1.5, md: 2.5 },
          }}
        >
          <Stack direction="row" alignItems="center" spacing={1.25} sx={{ flexShrink: 0 }} aria-label="Nexus OSR">
            <Avatar
              variant="rounded"
              sx={{ width: 38, height: 38, bgcolor: 'primary.main', fontSize: 15, fontWeight: 800 }}
              aria-hidden="true"
            >
              N
            </Avatar>
            <Typography translate="no" variant="subtitle1" sx={{ color: 'text.primary', display: { xs: 'none', sm: 'block' }, lineHeight: 1.2 }}>
              Nexus OSR
            </Typography>
          </Stack>

          <Box sx={{ minWidth: 0, flex: 1 }}>
            <AppNavigation capabilities={capabilities} activeRoute={activeRoute} />
          </Box>

          <Stack direction="row" alignItems="center" spacing={1} sx={{ flexShrink: 0 }}>
            {selectedScope && scopes.length === 1 ? (
              <Chip label={scopeLabel(selectedScope)} aria-label="当前工作范围" sx={{ display: { xs: 'none', md: 'inline-flex' } }} />
            ) : null}

            {selectedScope && scopes.length > 1 && onScopeChange ? (
              <FormControl sx={{ minWidth: 150, display: { xs: 'none', md: 'flex' } }}>
                <InputLabel id="nd-work-scope-label">工作范围</InputLabel>
                <Select
                  labelId="nd-work-scope-label"
                  label="工作范围"
                  value={selectedIndex >= 0 ? String(selectedIndex) : '0'}
                  onChange={handleScopeChange}
                  inputProps={{ 'aria-label': '工作范围' }}
                >
                  {scopes.map((scope, index) => {
                    const duplicateKey = `${scope.country_code}\u0000${scope.channel_key}`
                    const duplicate = (labelCounts.get(duplicateKey) ?? 0) > 1
                    return (
                      <MenuItem key={`${scope.tenant_hash}-${scope.country_code}-${scope.channel_key}`} value={String(index)}>
                        {scopeLabel(scope, duplicate ? index + 1 : undefined)}
                      </MenuItem>
                    )
                  })}
                </Select>
              </FormControl>
            ) : null}

            <Avatar sx={{ width: 34, height: 34, bgcolor: 'secondary.main', fontSize: 12, fontWeight: 700 }} aria-hidden="true">
              {initials(userLabel)}
            </Avatar>
            <Typography variant="body2" sx={{ color: 'text.secondary', display: { xs: 'none', lg: 'block' }, maxWidth: 140 }} noWrap>
              {userLabel}
            </Typography>
            <Button color="inherit" startIcon={<LogoutRoundedIcon />} onClick={onLogout} sx={{ color: 'text.secondary', minWidth: 44 }}>
              <Box component="span" sx={{ display: { xs: 'none', md: 'inline' } }}>退出</Box>
            </Button>
          </Stack>
        </Toolbar>
      </AppBar>

      <Box id="nd-main-content" component="main" tabIndex={-1} sx={{ minHeight: 'calc(100dvh - 68px)', outline: 'none' }}>
        {children}
      </Box>
    </Box>
  )
}

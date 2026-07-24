import AdminPanelSettingsRoundedIcon from '@mui/icons-material/AdminPanelSettingsRounded'
import HubRoundedIcon from '@mui/icons-material/HubRounded'
import InsightsRoundedIcon from '@mui/icons-material/InsightsRounded'
import MenuBookRoundedIcon from '@mui/icons-material/MenuBookRounded'
import MonitorHeartRoundedIcon from '@mui/icons-material/MonitorHeartRounded'
import PsychologyRoundedIcon from '@mui/icons-material/PsychologyRounded'
import WorkOutlineRoundedIcon from '@mui/icons-material/WorkOutlineRounded'
import { Box, Stack, Typography } from '@mui/material'
import { Link } from '@tanstack/react-router'
import { APP_NAVIGATION, canSeeNavigationItem } from './navigation'
import type { AppNavigationRouteKey, AppRouteKey } from './navigation'

const routeIcons = {
  workspace: WorkOutlineRoundedIcon,
  knowledge: MenuBookRoundedIcon,
  'agent-control': PsychologyRoundedIcon,
  channels: HubRoundedIcon,
  runtime: MonitorHeartRoundedIcon,
  'control-tower': InsightsRoundedIcon,
  administration: AdminPanelSettingsRoundedIcon,
} satisfies Record<AppNavigationRouteKey, typeof WorkOutlineRoundedIcon>

export function AppNavigation({
  capabilities,
  activeRoute,
  orientation = 'horizontal',
  onNavigate,
}: {
  capabilities: Set<string>
  activeRoute: AppRouteKey
  orientation?: 'horizontal' | 'vertical'
  onNavigate?: () => void
}) {
  const items = APP_NAVIGATION.filter((item) => canSeeNavigationItem(capabilities, item))
  const vertical = orientation === 'vertical'

  return (
    <Stack
      component="nav"
      aria-label="主导航"
      direction={vertical ? 'column' : 'row'}
      spacing={0.5}
      sx={vertical
        ? { width: '100%' }
        : {
            minWidth: 0,
            overflowX: 'auto',
            overscrollBehaviorX: 'contain',
            scrollbarWidth: 'thin',
          }}
    >
      {items.map((item) => {
        const Icon = routeIcons[item.key]
        const active = item.key === activeRoute
        return (
          <Link
            key={item.key}
            to={item.currentHref}
            aria-current={active ? 'page' : undefined}
            data-canonical-route={item.canonicalRoute}
            data-route-status={item.status}
            onClick={onNavigate}
            style={{ color: 'inherit', textDecoration: 'none', width: vertical ? '100%' : undefined }}
          >
            <Box
              component="span"
              sx={{
                alignItems: 'center',
                bgcolor: active ? 'action.selected' : 'transparent',
                borderInlineStart: vertical ? 3 : 0,
                borderColor: active ? 'primary.main' : 'transparent',
                borderRadius: 1,
                color: active ? 'primary.main' : 'text.secondary',
                display: 'inline-flex',
                gap: 0.75,
                justifyContent: 'flex-start',
                minHeight: vertical ? 44 : 40,
                px: vertical ? 1.5 : 1.25,
                py: vertical ? 0.5 : 0,
                whiteSpace: 'nowrap',
                width: vertical ? '100%' : 'auto',
                transition: (theme) => theme.transitions.create(['background-color', 'color', 'border-color'], { duration: theme.transitions.duration.shorter }),
                '&:hover': { bgcolor: active ? 'action.selected' : 'action.hover', color: active ? 'primary.main' : 'text.primary' },
                '&:active': { bgcolor: 'action.selected' },
                '&:focus-visible': { outline: '3px solid', outlineColor: 'primary.main', outlineOffset: 2 },
              }}
            >
              <Icon sx={{ fontSize: 18 }} aria-hidden="true" />
              <Typography component="span" variant="button">{item.label}</Typography>
            </Box>
          </Link>
        )
      })}
    </Stack>
  )
}

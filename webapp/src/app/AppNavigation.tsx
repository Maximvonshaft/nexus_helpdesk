import { Box, Stack, Typography } from '@mui/material'
import HubRoundedIcon from '@mui/icons-material/HubRounded'
import InsightsRoundedIcon from '@mui/icons-material/InsightsRounded'
import MenuBookRoundedIcon from '@mui/icons-material/MenuBookRounded'
import MonitorHeartRoundedIcon from '@mui/icons-material/MonitorHeartRounded'
import WorkOutlineRoundedIcon from '@mui/icons-material/WorkOutlineRounded'
import { Link } from '@tanstack/react-router'
import { APP_NAVIGATION, canSeeNavigationItem } from './navigation'
import type { AppRouteKey } from './navigation'

const routeIcons = {
  workspace: WorkOutlineRoundedIcon,
  knowledge: MenuBookRoundedIcon,
  channels: HubRoundedIcon,
  runtime: MonitorHeartRoundedIcon,
  'control-tower': InsightsRoundedIcon,
} satisfies Record<AppRouteKey, typeof WorkOutlineRoundedIcon>

export function AppNavigation({
  capabilities,
  activeRoute,
}: {
  capabilities: Set<string>
  activeRoute: AppRouteKey
}) {
  const items = APP_NAVIGATION.filter((item) => canSeeNavigationItem(capabilities, item))

  return (
    <Stack
      component="nav"
      aria-label="主导航"
      direction="row"
      spacing={0.5}
      sx={{ minWidth: 0, overflowX: 'auto', scrollbarWidth: 'none', '&::-webkit-scrollbar': { display: 'none' } }}
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
            style={{ color: 'inherit', textDecoration: 'none' }}
          >
            <Box
              component="span"
              sx={{
                alignItems: 'center',
                bgcolor: active ? 'action.selected' : 'transparent',
                borderRadius: 1,
                color: active ? 'primary.main' : 'text.secondary',
                display: 'inline-flex',
                gap: 0.75,
                minHeight: 40,
                px: 1.25,
                whiteSpace: 'nowrap',
                transition: (theme) => theme.transitions.create(['background-color', 'color'], { duration: theme.transitions.duration.shorter }),
                '&:hover': { bgcolor: active ? 'action.selected' : 'action.hover', color: active ? 'primary.main' : 'text.primary' },
                '&:focus-visible': { outline: '3px solid rgba(23, 92, 211, 0.24)', outlineOffset: 2 },
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

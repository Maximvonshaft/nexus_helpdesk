import { Box, Button, Paper, Stack, Typography } from '@mui/material'
import { createRootRoute, Link, Outlet } from '@tanstack/react-router'
import { getSupportToken } from '@/lib/supportApi'

export function NotFoundBoundary() {
  const destination = getSupportToken() ? '/workspace' : '/login'
  return (
    <Box component="main" sx={{ alignItems: 'center', display: 'flex', justifyContent: 'center', minHeight: '100dvh', p: 3 }}>
      <Paper variant="outlined" data-testid="unknown-route-boundary" sx={{ maxWidth: 560, p: { xs: 3, sm: 4 }, width: '100%' }}>
        <Stack spacing={1.5} alignItems="flex-start">
          <Typography component="h1" variant="h2">当前入口不存在</Typography>
          <Typography color="text.secondary">操作员工作已收敛到统一案例工作台；旧 WebChat 入口仅保留兼容访问。</Typography>
          <Link to={destination} style={{ textDecoration: 'none' }}>
            <Button component="span" variant="contained">进入案例处理</Button>
          </Link>
        </Stack>
      </Paper>
    </Box>
  )
}

export const Route = createRootRoute({
  component: () => <Outlet />,
  notFoundComponent: NotFoundBoundary,
})

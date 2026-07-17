import { Box, Button, Paper, Stack, Typography } from '@mui/material'
import { createRootRoute, Link, Outlet } from '@tanstack/react-router'
import { getSupportToken } from '@/lib/supportApi'

export function NotFoundBoundary() {
  const authenticated = Boolean(getSupportToken())
  const destination = authenticated ? '/workspace' : '/login'
  return (
    <Box component="main" sx={{ alignItems: 'center', display: 'flex', justifyContent: 'center', minHeight: '100dvh', p: 3 }}>
      <Paper variant="outlined" data-testid="unknown-route-boundary" sx={{ maxWidth: 560, p: { xs: 3, sm: 4 }, width: '100%' }}>
        <Stack spacing={1.5} sx={{
          alignItems: "flex-start"
        }}>
          <Typography component="h1" variant="h2">页面不存在</Typography>
          <Link to={destination} style={{ textDecoration: 'none' }}>
            <Button component="span" variant="contained">{authenticated ? '返回案例处理' : '返回登录'}</Button>
          </Link>
        </Stack>
      </Paper>
    </Box>
  );
}

export const Route = createRootRoute({
  component: () => <Outlet />,
  notFoundComponent: NotFoundBoundary,
})

import InboxRoundedIcon from '@mui/icons-material/InboxRounded'
import { Box, Stack, Typography } from '@mui/material'
import type { ReactNode } from 'react'

export function EmptyState({
  text,
  title,
  description,
  reason,
  action,
}: {
  text?: string
  title?: string
  description?: string
  reason?: string
  action?: ReactNode
}) {
  const heading = title ?? text ?? '暂无内容'

  return (
    <Stack
      role="status"
      alignItems="center"
      spacing={1.25}
      textAlign="center"
      sx={{ color: 'text.secondary', minHeight: 160, justifyContent: 'center', p: 3 }}
    >
      <Box sx={{ alignItems: 'center', bgcolor: 'action.hover', borderRadius: '50%', display: 'flex', height: 44, justifyContent: 'center', width: 44 }}>
        <InboxRoundedIcon aria-hidden="true" sx={{ color: 'text.secondary', fontSize: 22 }} />
      </Box>
      <Typography variant="subtitle1" color="text.primary">{heading}</Typography>
      {description ? <Typography variant="body2" sx={{ maxWidth: 520 }}>{description}</Typography> : null}
      {reason ? <Typography variant="caption" sx={{ maxWidth: 520 }}>{reason}</Typography> : null}
      {action ? <Box sx={{ pt: 0.5 }}>{action}</Box> : null}
    </Stack>
  )
}

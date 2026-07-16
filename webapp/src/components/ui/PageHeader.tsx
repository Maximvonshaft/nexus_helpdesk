import { Box, Stack, Typography } from '@mui/material'
import type { ElementType, ReactNode } from 'react'

type HeadingLevel = 1 | 2 | 3 | 4 | 5 | 6

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
  headingLevel = 2,
}: {
  eyebrow?: string
  title: string
  description?: string
  actions?: ReactNode
  headingLevel?: HeadingLevel
}) {
  const Heading = `h${headingLevel}` as ElementType

  return (
    <Stack
      direction={{ xs: 'column', sm: 'row' }}
      spacing={2}
      alignItems={{ xs: 'stretch', sm: 'flex-start' }}
      justifyContent="space-between"
    >
      <Box sx={{ minWidth: 0 }}>
        {eyebrow ? (
          <Typography variant="overline" color="text.secondary">
            {eyebrow}
          </Typography>
        ) : null}
        <Typography component={Heading} variant={headingLevel === 1 ? 'h1' : 'h2'}>
          {title}
        </Typography>
        {description ? (
          <Typography color="text.secondary" sx={{ mt: 0.75, maxWidth: 760 }}>
            {description}
          </Typography>
        ) : null}
      </Box>
      {actions ? <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>{actions}</Stack> : null}
    </Stack>
  )
}

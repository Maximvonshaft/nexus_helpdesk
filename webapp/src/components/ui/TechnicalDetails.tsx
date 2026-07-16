import ExpandMoreRoundedIcon from '@mui/icons-material/ExpandMoreRounded'
import { Accordion, AccordionDetails, AccordionSummary, Box, Typography } from '@mui/material'
import type { ReactNode } from 'react'

export function TechnicalDetails({ title = '高级技术详情', summary, children }: { title?: string; summary?: string; children: ReactNode }) {
  return (
    <Accordion disableGutters variant="outlined" sx={{ '&:before': { display: 'none' } }}>
      <AccordionSummary expandIcon={<ExpandMoreRoundedIcon />}>
        <Box sx={{ minWidth: 0 }}>
          <Typography variant="subtitle2">{title}</Typography>
          {summary ? <Typography variant="caption" color="text.secondary">{summary}</Typography> : null}
        </Box>
      </AccordionSummary>
      <AccordionDetails sx={{ borderTop: 1, borderColor: 'divider' }}>
        {children}
      </AccordionDetails>
    </Accordion>
  )
}

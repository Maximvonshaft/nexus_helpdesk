import { Box } from '@mui/material'
import { ChannelsPage } from './ChannelsPage'
import { EmailAccountGovernance } from './EmailAccountGovernance'

export function ChannelsControlPlane() {
  return (
    <>
      <ChannelsPage />
      <Box component="section" aria-label="邮件账号治理" sx={{ px: { xs: 1.5, md: 2.5 }, pb: { xs: 1.5, md: 2.5 } }}>
        <EmailAccountGovernance />
      </Box>
    </>
  )
}

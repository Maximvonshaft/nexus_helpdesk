import ArrowBackRoundedIcon from '@mui/icons-material/ArrowBackRounded'
import {
  Box,
  Button,
  Divider,
  Paper,
  Stack,
  Typography,
} from '@mui/material'
import { Link } from '@tanstack/react-router'
import { useMemo } from 'react'
import {
  INCOMING_VOICE_CONTEXT_PREFIX,
  type IncomingVoiceContext,
} from '@/lib/telephonyTypes'

function readContext(voiceSessionId: string): IncomingVoiceContext | null {
  const key = `${INCOMING_VOICE_CONTEXT_PREFIX}${voiceSessionId}`
  const raw = sessionStorage.getItem(key)
  if (!raw) return null
  try {
    const value = JSON.parse(raw) as IncomingVoiceContext
    if (value.voice_session_id !== voiceSessionId) return null
    return value
  } catch {
    sessionStorage.removeItem(key)
    return null
  }
}

export function WebCallOperatorContext({ voiceSessionId }: { voiceSessionId: string }) {
  const context = useMemo(() => readContext(voiceSessionId), [voiceSessionId])
  if (!context) return null

  return (
    <Paper
      component="aside"
      variant="outlined"
      aria-label="当前来电上下文"
      sx={{
        position: 'fixed',
        zIndex: (theme) => theme.zIndex.appBar,
        top: { xs: 8, md: 16 },
        left: { xs: 8, md: 16 },
        width: { xs: 'calc(100vw - 16px)', md: 300 },
        p: 2,
      }}
    >
      <Stack spacing={1.25}>
        <Stack direction="row" spacing={1} sx={{ alignItems: 'center', justifyContent: 'space-between' }}>
          <Typography component="h2" variant="h4">来电上下文</Typography>
          <Button component={Link} to="/workspace" size="small" color="inherit" startIcon={<ArrowBackRoundedIcon />}>
            工作台
          </Button>
        </Stack>
        <Divider />
        <Box>
          <Typography variant="caption" color="text.secondary">客户</Typography>
          <Typography variant="subtitle2">{context.visitor_label || '电话客户'}</Typography>
        </Box>
        {context.ticket_no || context.ticket_title ? (
          <Box>
            <Typography variant="caption" color="text.secondary">关联工单</Typography>
            <Typography variant="body2">
              {[context.ticket_no, context.ticket_title].filter(Boolean).join(' · ')}
            </Typography>
          </Box>
        ) : (
          <Typography variant="body2" color="text.secondary">实时 Conversation；仅在需要后续处理时创建工单。</Typography>
        )}
      </Stack>
    </Paper>
  )
}

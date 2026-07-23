import PhoneDisabledRoundedIcon from '@mui/icons-material/PhoneDisabledRounded'
import PhoneInTalkRoundedIcon from '@mui/icons-material/PhoneInTalkRounded'
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Stack,
  Typography,
} from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { telephonyApi } from '@/lib/telephonyApi'
import {
  INCOMING_VOICE_CONTEXT_PREFIX,
  type IncomingVoiceContext,
  type IncomingVoiceSession,
} from '@/lib/telephonyTypes'

const INCOMING_QUERY_KEY = ['incomingVoiceOffers'] as const

function remainingSeconds(offer: IncomingVoiceSession, now: number) {
  const expiresAt = Date.parse(offer.voice_offer.expires_at)
  if (!Number.isFinite(expiresAt)) return 0
  return Math.max(0, Math.ceil((expiresAt - now) / 1000))
}

function safeContext(offer: IncomingVoiceSession): IncomingVoiceContext {
  return {
    voice_session_id: offer.voice_session_id,
    conversation_id: offer.conversation_id || null,
    ticket_id: offer.ticket_id ?? null,
    ticket_no: offer.ticket_no || null,
    ticket_title: offer.ticket_title || null,
    visitor_label: offer.visitor_label || null,
    origin: offer.origin || null,
    page_url: offer.page_url || null,
  }
}

export function IncomingVoiceCallControl({ capabilities }: { capabilities: Set<string> }) {
  const queryClient = useQueryClient()
  const enabled = capabilities.has('webchat.handoff.accept')
  const [now, setNow] = useState(() => Date.now())
  const offers = useQuery({
    queryKey: INCOMING_QUERY_KEY,
    queryFn: () => telephonyApi.incomingOffers(10),
    enabled,
    refetchInterval: 2_000,
    retry: false,
  })
  const current = offers.data?.items[0] ?? null
  const seconds = useMemo(() => (current ? remainingSeconds(current, now) : 0), [current, now])

  useEffect(() => {
    if (!current) return undefined
    setNow(Date.now())
    const timer = window.setInterval(() => setNow(Date.now()), 1_000)
    return () => window.clearInterval(timer)
  }, [current])

  useEffect(() => {
    if (current && seconds <= 0) void offers.refetch()
  }, [current, offers, seconds])

  const reject = useMutation({
    mutationFn: () => {
      if (!current) throw new Error('来电已失效')
      return telephonyApi.rejectOffer(current.voice_session_id)
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: INCOMING_QUERY_KEY })
    },
  })

  if (!enabled) return null
  if (offers.isError) {
    return <Chip size="small" color="warning" label="来电检查失败" aria-label="来电检查失败" />
  }
  if (!current) return null

  const accept = () => {
    sessionStorage.setItem(
      `${INCOMING_VOICE_CONTEXT_PREFIX}${current.voice_session_id}`,
      JSON.stringify(safeContext(current)),
    )
    window.location.assign(`/webcall/${encodeURIComponent(current.voice_session_id)}`)
  }

  return (
    <>
      <Chip
        size="small"
        color="error"
        variant="filled"
        icon={<PhoneInTalkRoundedIcon />}
        label={`来电 ${offers.data?.items.length ?? 1}`}
        aria-label="有新的语音来电"
      />
      <Dialog
        open
        disableEscapeKeyDown
        aria-labelledby="incoming-voice-title"
        aria-describedby="incoming-voice-description"
        sx={{
          '& .MuiDialog-paper': {
            width: 'calc(100% - 32px)',
            maxWidth: 444,
          },
        }}
      >
        <DialogTitle id="incoming-voice-title">
          <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
            <PhoneInTalkRoundedIcon color="error" aria-hidden="true" />
            <Box>
              <Typography component="span" variant="h3">新的语音来电</Typography>
              <Typography component="div" variant="caption" color="text.secondary" sx={{ mt: 0.25 }}>
                该来电仅分配给当前坐席
              </Typography>
            </Box>
          </Stack>
        </DialogTitle>
        <DialogContent>
          <DialogContentText id="incoming-voice-description" component="div">
            <Stack spacing={1.25}>
              <Box>
                <Typography variant="caption" color="text.secondary">客户</Typography>
                <Typography variant="subtitle1">{current.visitor_label || '电话客户'}</Typography>
              </Box>
              {current.ticket_no || current.ticket_title ? (
                <Box>
                  <Typography variant="caption" color="text.secondary">关联工单</Typography>
                  <Typography variant="body2">
                    {[current.ticket_no, current.ticket_title].filter(Boolean).join(' · ')}
                  </Typography>
                </Box>
              ) : (
                <Alert severity="info" variant="outlined">当前为实时会话，无需先创建工单。</Alert>
              )}
              <Typography variant="body2" color={seconds <= 5 ? 'error.main' : 'text.secondary'} aria-live="polite">
                接听机会将在 {seconds} 秒后轮转给下一位坐席。
              </Typography>
              {(offers.data?.items.length ?? 0) > 1 ? (
                <Typography variant="caption" color="text.secondary">
                  当前还有 {(offers.data?.items.length ?? 1) - 1} 个来电 Offer 等待处理。
                </Typography>
              ) : null}
              {reject.isError ? <Alert severity="error">拒绝来电失败，请重试。</Alert> : null}
            </Stack>
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button
            color="inherit"
            variant="outlined"
            startIcon={reject.isPending ? <CircularProgress size={16} /> : <PhoneDisabledRoundedIcon />}
            disabled={reject.isPending || seconds <= 0}
            onClick={() => reject.mutate()}
          >
            暂不接听
          </Button>
          <Button
            color="error"
            variant="contained"
            startIcon={<PhoneInTalkRoundedIcon />}
            disabled={reject.isPending || seconds <= 0}
            onClick={accept}
          >
            接听通话
          </Button>
        </DialogActions>
      </Dialog>
    </>
  )
}

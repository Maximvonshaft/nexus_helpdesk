import CallEndRoundedIcon from '@mui/icons-material/CallEndRounded'
import DialpadRoundedIcon from '@mui/icons-material/DialpadRounded'
import MicOffRoundedIcon from '@mui/icons-material/MicOffRounded'
import MicRoundedIcon from '@mui/icons-material/MicRounded'
import PauseRoundedIcon from '@mui/icons-material/PauseRounded'
import PhoneForwardedRoundedIcon from '@mui/icons-material/PhoneForwardedRounded'
import PlayArrowRoundedIcon from '@mui/icons-material/PlayArrowRounded'
import SwapCallsRoundedIcon from '@mui/icons-material/SwapCallsRounded'
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Paper,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import { Room, RoomEvent, Track, type RemoteTrack } from 'livekit-client'
import { useCallback, useEffect, useRef, useState } from 'react'
import { supportApi } from '@/lib/supportApi'
import type { VoiceSessionBootstrap } from '@/lib/telephonyTypes'

interface VisitorBootstrap extends VoiceSessionBootstrap {
  role: 'visitor'
  visitor_token: string
  conversation_id: string
}

type TransferAction = 'cold_transfer' | 'warm_transfer'

function readVisitorBootstrap(): VisitorBootstrap | null {
  const raw = window.location.hash.replace(/^#/, '')
  if (!raw) return null
  try {
    const decoded = decodeURIComponent(escape(window.atob(raw.replace(/-/g, '+').replace(/_/g, '/'))))
    const parsed = JSON.parse(decoded) as VisitorBootstrap
    window.history.replaceState(null, '', window.location.pathname)
    return parsed.role === 'visitor' ? parsed : null
  } catch {
    return null
  }
}

function dtmfCode(digit: string) {
  const map: Record<string, number> = { '0': 0, '1': 1, '2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8, '9': 9, '*': 10, '#': 11 }
  return map[digit]
}

export function WebCallPage({ voiceSessionId }: { voiceSessionId: string }) {
  const roomRef = useRef<Room | null>(null)
  const [bootstrap] = useState(readVisitorBootstrap)
  const [status, setStatus] = useState('正在建立安全语音连接…')
  const [error, setError] = useState<string | null>(null)
  const [muted, setMuted] = useState(false)
  const [held, setHeld] = useState(false)
  const [digits, setDigits] = useState('')
  const [connected, setConnected] = useState(false)
  const [transferTarget, setTransferTarget] = useState('')
  const [transferPending, setTransferPending] = useState<TransferAction | null>(null)

  const recordAction = useCallback(async (action_type: string, extra: Record<string, unknown> = {}) => {
    if (bootstrap) return null
    return supportApi.recordVoiceAction(voiceSessionId, {
      action_type,
      idempotency_key: `webcall-${voiceSessionId}-${action_type}-${crypto.randomUUID()}`,
      ...extra,
    })
  }, [bootstrap, voiceSessionId])

  useEffect(() => {
    let active = true
    const room = new Room({ adaptiveStream: true, dynacast: true })
    roomRef.current = room
    const attach = (track: RemoteTrack) => {
      if (track.kind !== Track.Kind.Audio) return
      const element = track.attach()
      element.autoplay = true
      element.setAttribute('data-livekit-remote-audio', 'true')
      document.body.appendChild(element)
    }
    room.on(RoomEvent.TrackSubscribed, attach)
    room.on(RoomEvent.Disconnected, () => {
      if (active) {
        setConnected(false)
        setStatus('通话已断开')
      }
    })

    const start = async () => {
      try {
        const session = bootstrap || await supportApi.acceptVoiceSession(voiceSessionId)
        if (!session.livekit_url || !session.participant_token) throw new Error('LiveKit 会话凭证不可用')
        await room.connect(session.livekit_url, session.participant_token)
        await room.localParticipant.setMicrophoneEnabled(true, {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        })
        if (!active) return
        setConnected(true)
        setStatus(bootstrap ? 'AI/客服语音已连接' : '客户语音已接通')
      } catch (cause) {
        if (!active) return
        setError(cause instanceof Error ? cause.message : '语音连接失败')
        setStatus('连接失败')
      }
    }
    void start()
    return () => {
      active = false
      room.disconnect()
      document.querySelectorAll('[data-livekit-remote-audio=true]').forEach((element) => element.remove())
      roomRef.current = null
    }
  }, [bootstrap, voiceSessionId])

  const toggleMute = async () => {
    const next = !muted
    await roomRef.current?.localParticipant.setMicrophoneEnabled(!next)
    setMuted(next)
    await recordAction(next ? 'mute' : 'unmute')
  }
  const toggleHold = async () => {
    const next = !held
    await roomRef.current?.localParticipant.setMicrophoneEnabled(!next)
    setHeld(next)
    await recordAction(next ? 'hold' : 'resume')
  }
  const sendDigits = async () => {
    const room = roomRef.current
    if (!room || !digits) return
    for (const digit of digits) {
      await room.localParticipant.publishDtmf(dtmfCode(digit), digit)
    }
    await recordAction('keypad', { digits })
    setDigits('')
  }
  const transferCall = async (action: TransferAction) => {
    const target = transferTarget.trim()
    if (!target) return
    setTransferPending(action)
    setError(null)
    try {
      const result = await recordAction(action, { target })
      if (!result?.action?.id) throw new Error('转接命令未被系统接受')
      setStatus(
        action === 'cold_transfer'
          ? '直接转接命令已提交，等待 Provider 确认'
          : '咨询转接已发起，等待目标方加入通话',
      )
      setTransferTarget('')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '转接失败')
    } finally {
      setTransferPending(null)
    }
  }
  const endCall = async () => {
    try {
      if (bootstrap) {
        await supportApi.endPublicVoiceSession(
          bootstrap.conversation_id,
          voiceSessionId,
          bootstrap.visitor_token,
        )
      } else {
        await supportApi.endVoiceSession(voiceSessionId)
      }
    } finally {
      await roomRef.current?.disconnect()
      setConnected(false)
      setStatus('通话已结束')
    }
  }

  return (
    <Box component="main" sx={{ minHeight: '100dvh', display: 'grid', placeItems: 'center', p: 2, bgcolor: 'background.default' }}>
      <Paper variant="outlined" sx={{ width: 'min(640px, 100%)', p: { xs: 2, sm: 3 } }}>
        <Stack spacing={2.5} sx={{ alignItems: 'stretch' }}>
          <Box>
            <Typography component="h1" variant="h2">Nexus Live Voice</Typography>
            <Typography color="text.secondary" sx={{ mt: 0.5 }}>{status}</Typography>
          </Box>
          {!connected && !error ? <CircularProgress size={28} /> : null}
          {error ? <Alert severity="error">{error}</Alert> : null}
          <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
            <Button variant={muted ? 'contained' : 'outlined'} startIcon={muted ? <MicOffRoundedIcon /> : <MicRoundedIcon />} disabled={!connected} onClick={() => void toggleMute()}>
              {muted ? '取消静音' : '静音'}
            </Button>
            {!bootstrap ? (
              <Button variant={held ? 'contained' : 'outlined'} startIcon={held ? <PlayArrowRoundedIcon /> : <PauseRoundedIcon />} disabled={!connected} onClick={() => void toggleHold()}>
                {held ? '恢复通话' : '保持'}
              </Button>
            ) : null}
            <Button color="error" variant="contained" startIcon={<CallEndRoundedIcon />} disabled={!connected} onClick={() => void endCall()}>
              结束
            </Button>
          </Stack>
          <Stack direction="row" spacing={1}>
            <TextField fullWidth label="DTMF" value={digits} slotProps={{ htmlInput: { pattern: '[0-9*#]*', maxLength: 32 } }} onChange={(event) => setDigits(event.target.value.replace(/[^0-9*#]/g, ''))} />
            <Button variant="outlined" startIcon={<DialpadRoundedIcon />} disabled={!connected || !digits} onClick={() => void sendDigits()}>发送</Button>
          </Stack>
          {!bootstrap ? (
            <Stack spacing={1}>
              <Typography component="h2" variant="h4">转接通话</Typography>
              <TextField
                fullWidth
                label="目标坐席、队列或电话号码"
                value={transferTarget}
                helperText="内部目标使用系统身份；外部目标使用完整电话号码。实际支持范围由 LiveKit/SIP Provider 决定。"
                slotProps={{ htmlInput: { maxLength: 240 } }}
                onChange={(event) => setTransferTarget(event.target.value)}
              />
              <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
                <Button
                  variant="outlined"
                  startIcon={<PhoneForwardedRoundedIcon />}
                  disabled={!connected || !transferTarget.trim() || transferPending !== null}
                  onClick={() => void transferCall('cold_transfer')}
                >
                  {transferPending === 'cold_transfer' ? '提交中…' : '直接转接'}
                </Button>
                <Button
                  variant="outlined"
                  startIcon={<SwapCallsRoundedIcon />}
                  disabled={!connected || !transferTarget.trim() || transferPending !== null}
                  onClick={() => void transferCall('warm_transfer')}
                >
                  {transferPending === 'warm_transfer' ? '邀请中…' : '咨询后转接'}
                </Button>
              </Stack>
            </Stack>
          ) : null}
          <Alert severity="info" variant="outlined">请勿在通话中披露密码、支付验证码或其他高敏感凭证。</Alert>
        </Stack>
      </Paper>
    </Box>
  )
}

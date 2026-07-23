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
import { telephonyApi, type VoiceCommandRequest } from '@/lib/telephonyApi'
import type { VoiceCommandRead, VoiceSessionBootstrap } from '@/lib/telephonyTypes'

interface VisitorBootstrap extends VoiceSessionBootstrap {
  role: 'visitor'
  visitor_token: string
  conversation_id: string
}

type TransferAction = 'cold_transfer' | 'warm_transfer'
type PendingAction = VoiceCommandRequest['action_type'] | 'visitor_hangup' | null

const COMMAND_POLL_INTERVAL_MS = 500
const COMMAND_POLL_TIMEOUT_MS = 100_000

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

function sleep(milliseconds: number) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds))
}

function commandFailure(command: VoiceCommandRead) {
  const reason = command.provider_reason || command.provider_status || command.status
  return new Error(`通话操作未完成：${reason}`)
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
  const [pendingAction, setPendingAction] = useState<PendingAction>(null)

  const waitForCommand = useCallback(async (initial: VoiceCommandRead) => {
    let current = initial
    const deadline = Date.now() + COMMAND_POLL_TIMEOUT_MS
    while (true) {
      if (current.status === 'succeeded') return current
      if (current.status === 'failed' || current.status === 'cancelled') throw commandFailure(current)
      if (Date.now() >= deadline) {
        throw new Error('通话操作的 Provider 状态确认超时。系统不会将未确认操作显示为成功，请在通话记录中核对后重试。')
      }
      await sleep(COMMAND_POLL_INTERVAL_MS)
      try {
        const response = await telephonyApi.listCommands(voiceSessionId)
        current = response.items.find((item) => item.id === initial.id) || current
      } catch {
        // A transient read failure must not turn an unconfirmed command into success.
      }
    }
  }, [voiceSessionId])

  const recordAction = useCallback(async (
    action_type: VoiceCommandRequest['action_type'],
    extra: Omit<VoiceCommandRequest, 'action_type' | 'idempotency_key'> = {},
  ) => {
    if (bootstrap) return null
    const response = await telephonyApi.recordCommand(voiceSessionId, {
      action_type,
      idempotency_key: `webcall-${voiceSessionId}-${action_type}-${crypto.randomUUID()}`,
      ...extra,
    })
    return waitForCommand(response.action)
  }, [bootstrap, voiceSessionId, waitForCommand])

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

  const setLocalMicrophoneState = async (nextMuted: boolean, nextHeld: boolean) => {
    await roomRef.current?.localParticipant.setMicrophoneEnabled(!(nextMuted || nextHeld))
  }

  const toggleMute = async () => {
    const next = !muted
    setError(null)
    try {
      // Agent self-mute is a local media publication control. Sending a Provider
      // mute command here would target another participant and create false semantics.
      await setLocalMicrophoneState(next, held)
      setMuted(next)
      setStatus(next ? '你的麦克风已静音' : '你的麦克风已恢复')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '静音操作失败')
    }
  }

  const toggleHold = async () => {
    const next = !held
    const action = next ? 'hold' : 'resume'
    setError(null)
    setPendingAction(action)
    setStatus(next ? '正在等待 Provider 确认保持状态…' : '正在等待 Provider 确认恢复状态…')
    try {
      // Keep the operator microphone closed until the durable Provider result is final.
      await setLocalMicrophoneState(muted, true)
      const result = await recordAction(action)
      if (!result) throw new Error('坐席通话控制不可用')
      setHeld(next)
      await setLocalMicrophoneState(muted, next)
      setStatus(next ? '通话已保持，客户正在听等待音乐' : '通话已恢复')
    } catch (cause) {
      await setLocalMicrophoneState(muted, held).catch(() => undefined)
      setError(cause instanceof Error ? cause.message : '保持操作失败')
      setStatus(held ? '通话仍处于保持状态' : '通话保持状态未改变')
    } finally {
      setPendingAction(null)
    }
  }

  const sendDigits = async () => {
    const room = roomRef.current
    if (!room || !digits) return
    setError(null)
    setPendingAction('keypad')
    setStatus('正在发送 DTMF 并等待 Provider 确认…')
    try {
      if (bootstrap) {
        for (const digit of digits) {
          await room.localParticipant.publishDtmf(dtmfCode(digit), digit)
        }
      } else {
        const result = await recordAction('keypad', { digits })
        if (!result) throw new Error('DTMF 控制不可用')
      }
      setDigits('')
      setStatus('DTMF 已由 Provider 确认发送')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'DTMF 发送失败')
      setStatus('DTMF 未确认发送')
    } finally {
      setPendingAction(null)
    }
  }

  const transferCall = async (action: TransferAction) => {
    const target = transferTarget.trim()
    if (!target) return
    setPendingAction(action)
    setError(null)
    setStatus(action === 'cold_transfer' ? '正在执行直接转接…' : '正在执行咨询后转接…')
    try {
      const result = await recordAction(action, { target })
      if (!result) throw new Error('转接命令不可用')
      setStatus(action === 'cold_transfer' ? 'Provider 已确认直接转接' : 'Provider 已确认咨询转接完成')
      setTransferTarget('')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '转接失败')
      setStatus('转接未完成，当前通话保持连接')
    } finally {
      setPendingAction(null)
    }
  }

  const endCall = async () => {
    setError(null)
    setPendingAction(bootstrap ? 'visitor_hangup' : 'hangup')
    setStatus('正在结束通话…')
    try {
      if (bootstrap) {
        await supportApi.endPublicVoiceSession(
          bootstrap.conversation_id,
          voiceSessionId,
          bootstrap.visitor_token,
        )
      } else {
        const response = await telephonyApi.endSession(voiceSessionId)
        if (!response.command) throw new Error('挂断命令未被系统接受')
        await waitForCommand(response.command)
      }
      await roomRef.current?.disconnect()
      setConnected(false)
      setStatus('通话已结束')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '挂断失败')
      setStatus('挂断未完成，当前通话保持连接')
    } finally {
      setPendingAction(null)
    }
  }

  const commandBusy = pendingAction !== null

  return (
    <Box component="main" sx={{ minHeight: '100dvh', display: 'grid', placeItems: 'center', p: 2, bgcolor: 'background.default' }}>
      <Paper variant="outlined" sx={{ width: 'min(640px, 100%)', p: { xs: 2, sm: 3 } }}>
        <Stack spacing={2.5} sx={{ alignItems: 'stretch' }}>
          <Box>
            <Typography component="h1" variant="h2">Nexus Live Voice</Typography>
            <Typography color="text.secondary" sx={{ mt: 0.5 }}>{status}</Typography>
          </Box>
          {(!connected && !error) || commandBusy ? <CircularProgress size={28} /> : null}
          {error ? <Alert severity="error">{error}</Alert> : null}
          <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
            <Button variant={muted ? 'contained' : 'outlined'} startIcon={muted ? <MicOffRoundedIcon /> : <MicRoundedIcon />} disabled={!connected || commandBusy} onClick={() => void toggleMute()}>
              {muted ? '取消静音' : '静音'}
            </Button>
            {!bootstrap ? (
              <Button variant={held ? 'contained' : 'outlined'} startIcon={held ? <PlayArrowRoundedIcon /> : <PauseRoundedIcon />} disabled={!connected || commandBusy} onClick={() => void toggleHold()}>
                {pendingAction === 'hold' || pendingAction === 'resume' ? '确认中…' : held ? '恢复通话' : '保持'}
              </Button>
            ) : null}
            <Button color="error" variant="contained" startIcon={<CallEndRoundedIcon />} disabled={!connected || commandBusy} onClick={() => void endCall()}>
              {pendingAction === 'hangup' || pendingAction === 'visitor_hangup' ? '结束中…' : '结束'}
            </Button>
          </Stack>
          <Stack direction="row" spacing={1}>
            <TextField fullWidth label="DTMF" value={digits} disabled={commandBusy} slotProps={{ htmlInput: { pattern: '[0-9*#]*', maxLength: 32 } }} onChange={(event) => setDigits(event.target.value.replace(/[^0-9*#]/g, ''))} />
            <Button variant="outlined" startIcon={<DialpadRoundedIcon />} disabled={!connected || !digits || commandBusy} onClick={() => void sendDigits()}>{pendingAction === 'keypad' ? '发送中…' : '发送'}</Button>
          </Stack>
          {!bootstrap ? (
            <Stack spacing={1}>
              <Typography component="h2" variant="h4">转接通话</Typography>
              <TextField
                fullWidth
                label="目标坐席、队列或电话号码"
                value={transferTarget}
                disabled={commandBusy}
                helperText="内部目标使用系统身份；外部目标使用完整电话号码。只有 Provider 最终确认后才显示转接成功。"
                slotProps={{ htmlInput: { maxLength: 240 } }}
                onChange={(event) => setTransferTarget(event.target.value)}
              />
              <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
                <Button
                  variant="outlined"
                  startIcon={<PhoneForwardedRoundedIcon />}
                  disabled={!connected || !transferTarget.trim() || commandBusy}
                  onClick={() => void transferCall('cold_transfer')}
                >
                  {pendingAction === 'cold_transfer' ? '执行中…' : '直接转接'}
                </Button>
                <Button
                  variant="outlined"
                  startIcon={<SwapCallsRoundedIcon />}
                  disabled={!connected || !transferTarget.trim() || commandBusy}
                  onClick={() => void transferCall('warm_transfer')}
                >
                  {pendingAction === 'warm_transfer' ? '咨询中…' : '咨询后转接'}
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

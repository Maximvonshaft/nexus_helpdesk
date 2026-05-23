export const voiceCallLabels: Record<string, string> = {
  status: '通话状态',
  provider: '语音服务',
  accepted_by: '接听人',
  ended_by: '结束方',
  ringing_duration_seconds: '等待接听时长',
  talk_duration_seconds: '实际通话时长',
  total_duration_seconds: '总耗时',
  recording_status: '录音状态',
  transcript_status: '文字记录状态',
  summary_status: '摘要状态',
}

export const aiConfigTypeLabels: Record<string, string> = {
  persona: '助手人设',
  knowledge: '业务知识',
  sop: '处理流程',
  policy: '执行边界',
}

export const credentialTermLabels: Record<string, string> = {
  active: '已连接',
  pending: '等待授权',
  revoked: '已撤销',
  expired: '已过期',
}

export const accountHealthLabels: Record<string, string> = {
  healthy: '正常',
  degraded: '受限',
  offline: '离线',
  unknown: '未知',
}

export function formatDurationSeconds(value: unknown) {
  const seconds = Number(value)
  if (!Number.isFinite(seconds) || seconds < 0) return '-'
  if (seconds < 60) return `${seconds} 秒`
  const minutes = Math.floor(seconds / 60)
  const rest = seconds % 60
  return rest ? `${minutes} 分 ${rest} 秒` : `${minutes} 分钟`
}

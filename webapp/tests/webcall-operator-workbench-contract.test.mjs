import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import test from 'node:test'

const root = resolve(import.meta.dirname, '..')
const route = readFileSync(resolve(root, 'src/routes/webcall-operator.tsx'), 'utf8')
const router = readFileSync(resolve(root, 'src/router.tsx'), 'utf8')
const appShell = readFileSync(resolve(root, 'src/layouts/AppShell.tsx'), 'utf8')
const commandPalette = readFileSync(resolve(root, 'src/components/ui/CommandPalette.tsx'), 'utf8')
const rbac = readFileSync(resolve(root, 'src/lib/rbac.ts'), 'utf8')
const publicWebcall = readFileSync(resolve(root, 'src/routes/webcall.tsx'), 'utf8')
const apiClient = readFileSync(resolve(root, 'src/lib/api.ts'), 'utf8')
const voiceApi = readFileSync(resolve(root, 'src/lib/webchatVoiceApi.ts'), 'utf8')
const agentPanel = readFileSync(resolve(root, 'src/components/webcall/AgentWebCallPanel.tsx'), 'utf8')

test('top-level /webcall operator route is registered without replacing customer room route', () => {
  assert.match(route, /path: '\/webcall'/)
  assert.match(route, /WebCall Operator Workbench/)
  assert.match(router, /WebCallOperatorRoute/)
  assert.match(router, /@\/routes\/webcall-operator/)
  assert.match(router, /WebCallRoute/)
  assert.match(publicWebcall, /path: '\/webcall\/\$voice_session_id'/)
})

test('webcall operator entry is routeAccess gated and visible in operator navigation', () => {
  assert.match(rbac, /'\/webcall': \{ allOf: \[CAPABILITIES\.webcallVoiceQueueView\] \}/)
  assert.match(route, /<RequireCapability requirement=\{routeAccess\['\/webcall'\]\}>/)
  assert.match(appShell, /to: '\/webcall'[\s\S]*label: 'WebCall'[\s\S]*access: routeAccess\['\/webcall'\]/)
  assert.match(appShell, /isActiveNavPath\(location\.pathname, item\.to\)/)
  assert.match(appShell, /pathname\.startsWith\(`\$\{target\}\/`\)/)
  assert.match(commandPalette, /id: 'webcall-workbench'[\s\S]*to: '\/webcall'[\s\S]*access: routeAccess\['\/webcall'\]/)
})

test('webcall workbench uses real backend contracts for queue, identity, AI, handoff, and audit', () => {
  for (const apiCall of [
    'api.webchatVoiceIncomingSessions',
    'api.webchatHandoffQueue',
    'api.webchatConversations',
    'api.webchatThread',
    'api.caseDetail',
    'api.ticketTimeline',
    'api.webcallAIDemoStatus',
    'api.webchatAcceptHandoff',
    'api.webchatDeclineHandoff',
    'api.webchatReleaseHandoff',
    'api.webchatResumeAi',
    'api.webchatForceTakeover',
  ]) {
    assert.match(route, new RegExp(apiCall.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
  }
  assert.match(route, /<AgentWebCallPanel/)
  assert.match(route, /data-testid="webcall-identity-verification"/)
  assert.match(route, /data-testid="webcall-ai-suggestions"/)
  assert.match(route, /data-testid="webcall-handoff-actions"/)
  assert.match(route, /data-testid="webcall-demo-shape"/)
  assert.match(route, /data-testid="webcall-timeline-audit"/)
  assert.match(route, /thread\?\.ai_turns/)
  assert.match(route, /thread\.data\?\.events/)
  assert.doesNotMatch(route, /\bfetch\s*\(/)
  assert.doesNotMatch(route, /Mock voice session|Accept mock call|End mock call/)
})

test('webcall operator handoff actions are capability-gated', () => {
  assert.match(route, /canAcceptWebchatHandoff/)
  assert.match(route, /canDeclineWebchatHandoff/)
  assert.match(route, /canReleaseWebchatHandoff/)
  assert.match(route, /canResumeWebchatHandoff/)
  assert.match(rbac, /acceptWebchatHandoff: \{ allOf: \[CAPABILITIES\.webchatHandoffAccept\] \}/)
  assert.match(rbac, /declineWebchatHandoff: \{ allOf: \[CAPABILITIES\.webchatHandoffDecline\] \}/)
  assert.match(rbac, /releaseWebchatHandoff: \{ allOf: \[CAPABILITIES\.webchatHandoffRelease\] \}/)
  assert.match(rbac, /resumeWebchatHandoff: \{ allOf: \[CAPABILITIES\.webchatHandoffResumeAI\] \}/)
  assert.match(rbac, /webchatHandoffAccept: 'webchat\.handoff\.accept'/)
  assert.match(rbac, /webchatHandoffDecline: 'webchat\.handoff\.decline'/)
  assert.match(rbac, /webchatHandoffRelease: 'webchat\.handoff\.release'/)
  assert.match(rbac, /webchatHandoffResumeAI: 'webchat\.handoff\.resume_ai'/)
  assert.match(rbac, /webchatHandoffForceTakeover/)
})

test('webcall call notes are saved through unified api client and timeline/audit refresh', () => {
  assert.match(apiClient, /webchatVoiceSaveNote: \(ticketId: number, voiceSessionId: string, payload: \{ body: string; source\?: string \| null \}\)/)
  assert.match(apiClient, /`\/api\/webchat\/admin\/tickets\/\$\{ticketId\}\/voice\/\$\{voiceSessionId\}\/notes`/)
  assert.match(voiceApi, /saveNote: api\.webchatVoiceSaveNote/)
  assert.match(agentPanel, /data-testid="webcall-call-notes"/)
  assert.match(agentPanel, /webchatVoiceApi\.saveNote/)
  assert.match(agentPanel, /queryKey: \['ticketTimeline', ticketId\]/)
  assert.match(agentPanel, /TicketInternalNote、ticket timeline、WebChat event 和 admin audit/)
})

test('webcall transcript and AI evidence use real redacted voice evidence API', () => {
  assert.match(apiClient, /webchatVoiceEvidence: \(ticketId: number, voiceSessionId: string, params\?: \{ limit\?: number \}, init\?: RequestInit\)/)
  assert.match(apiClient, /`\/api\/webchat\/admin\/tickets\/\$\{ticketId\}\/voice\/\$\{voiceSessionId\}\/evidence\?\$\{search\.toString\(\)\}`/)
  assert.match(voiceApi, /evidence: api\.webchatVoiceEvidence/)
  assert.match(agentPanel, /data-testid="webcall-live-transcript-evidence"/)
  assert.match(agentPanel, /webchatVoiceApi\.evidence/)
  assert.match(agentPanel, /data-testid="webcall-ai-turn-evidence"/)
  assert.match(agentPanel, /data-testid="webcall-ai-action-evidence"/)
  assert.doesNotMatch(agentPanel, /text_raw/)
})

test('webcall template session actions use audited backend command API', () => {
  assert.match(rbac, /webcallVoiceControl: 'webcall\.voice\.control'/)
  assert.match(rbac, /controlWebcallVoice: \{ allOf: \[CAPABILITIES\.webcallVoiceControl\] \}/)
  assert.match(apiClient, /webchatVoiceActions: \(ticketId: number, voiceSessionId: string, params\?: \{ limit\?: number \}, init\?: RequestInit\)/)
  assert.match(apiClient, /webchatVoiceCreateAction: \(ticketId: number, voiceSessionId: string, actionType: WebchatVoiceActionType, payload\?: WebchatVoiceActionPayload\)/)
  assert.match(apiClient, /`\/api\/webchat\/admin\/tickets\/\$\{ticketId\}\/voice\/\$\{voiceSessionId\}\/actions/)
  assert.match(voiceApi, /actions: api\.webchatVoiceActions/)
  assert.match(voiceApi, /createAction: api\.webchatVoiceCreateAction/)
  assert.match(agentPanel, /data-testid="webcall-session-actions"/)
  for (const action of ['hold', 'resume', 'keypad', 'transfer', 'add_participant']) {
    assert.match(agentPanel, new RegExp(action))
  }
  assert.match(agentPanel, /provider_adapter_pending/)
  assert.match(agentPanel, /queryKey: \['ticketTimeline', ticketId\]/)
  assert.doesNotMatch(agentPanel, /\bfetch\s*\(/)
})

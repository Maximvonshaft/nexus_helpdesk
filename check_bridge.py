import sys
with open('backend/scripts/openclaw_bridge_server.js', 'r') as f:
    c = f.read()
    sb = c.split('async sendMessage(payload) {')[1].split('async listConversations')[0]
    ab = c.split('async aiReply(payload) {')[1].split('async lookupSpeedaf')[0]
    assert 'bridge_writes_disabled' not in ab, "aiReply must not have bridge_writes_disabled"
    assert 'bridge_writes_disabled' in sb, "sendMessage must have bridge_writes_disabled"
    assert 'aiReplyEnabled' in c, "Must have aiReplyEnabled"
    assert 'sendMessageEnabled' in c, "Must have sendMessageEnabled"
    assert "status: 'ok'" in ab or 'status: "ok"' in ab or 'status = "ok"' in ab, "Must have ok status output"
    assert 'replyText' in ab, "Must have replyText"
    assert 'elapsedMs' in ab, "Must have elapsedMs"
    assert 'timeoutMs' in ab, "Must have timeoutMs"
print("Static guard pass")

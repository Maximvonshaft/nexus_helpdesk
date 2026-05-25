import assert from "node:assert/strict";
import test from "node:test";
import { TerminalTurnCollector } from "../src/terminal-turn-collector.js";

test("terminal collector handles agent message delta fallback", async () => {
  const collector = new TerminalTurnCollector();
  collector.handleNotification({
    method: "item/agentMessage/delta",
    params: { threadId: "t1", turnId: "u1", delta: "{\"reply\":\"ok\"" },
  });
  collector.setTurn("t1", "u1");
  collector.handleNotification({
    method: "item/agentMessage/delta",
    params: { threadId: "t1", turnId: "u1", delta: ",\"intent\":\"other\"}" },
  });
  collector.handleNotification({
    method: "turn/completed",
    params: { threadId: "t1", turn: { id: "u1", threadId: "t1", status: "completed", items: [] } },
  });

  const result = await collector.wait(10);

  assert.equal(result.terminal, true);
  assert.equal(result.assistantText, "{\"reply\":\"ok\",\"intent\":\"other\"}");
  assert.equal(result.extractionPath, "notification:item/agentMessage/delta.params.delta");
});

test("terminal collector extracts agentMessage text from turn completed", async () => {
  const collector = new TerminalTurnCollector();
  collector.setTurn("thread-a", "turn-a");
  collector.handleNotification({
    method: "turn/completed",
    params: {
      threadId: "thread-a",
      turn: {
        id: "turn-a",
        threadId: "thread-a",
        status: "completed",
        items: [{ type: "agentMessage", text: "{\"reply\":\"done\",\"intent\":\"other\"}" }],
      },
    },
  });

  const result = await collector.wait(10);

  assert.equal(result.terminal, true);
  assert.equal(result.assistantText, "{\"reply\":\"done\",\"intent\":\"other\"}");
});

test("terminal collector handles non retry error as terminal", async () => {
  const collector = new TerminalTurnCollector();
  collector.setTurn("thread-a", "turn-a");
  collector.handleNotification({
    method: "error",
    params: { threadId: "thread-a", turnId: "turn-a", willRetry: false, error: { message: "unauthorized" } },
  });

  const result = await collector.wait(10);

  assert.equal(result.terminal, true);
  assert.deepEqual(result.error, { message: "unauthorized" });
});

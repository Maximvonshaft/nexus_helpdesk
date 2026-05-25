import assert from "node:assert/strict";
import { join } from "node:path";
import { tmpdir } from "node:os";
import test from "node:test";
import { loadConfig } from "../src/env.js";
import type { RpcClient } from "../src/rpc-client.js";
import { runEphemeralThread } from "../src/thread-runner.js";
import type { ReplyRequest } from "../src/reply-contract.js";
import type { RpcNotification } from "../src/notification-correlation.js";

test("thread runner uses fast profile params and isolated workdir", async () => {
  const calls: Array<{ method: string; params: any }> = [];
  let handler: ((notification: RpcNotification) => void) | undefined;
  const client = {
    async request(method: string, params: any) {
      calls.push({ method, params });
      if (method === "thread/start") {
        return { thread: { id: "thread-1" } };
      }
      if (method === "turn/start") {
        setImmediate(() => {
          handler?.({
            method: "turn/completed",
            params: {
              threadId: "thread-1",
              turn: {
                id: "turn-1",
                threadId: "thread-1",
                status: "completed",
                items: [
                  {
                    type: "agentMessage",
                    text: JSON.stringify({
                      reply: "Please send your tracking number.",
                      intent: "tracking_missing_number",
                      tracking_number: null,
                      handoff_required: false,
                      handoff_reason: null,
                      recommended_agent_action: null,
                    }),
                  },
                ],
              },
            },
          });
        });
        return { turn: { id: "turn-1" } };
      }
      return {};
    },
    addNotificationHandler(next: (notification: RpcNotification) => void) {
      handler = next;
      return () => {
        handler = undefined;
      };
    },
  } as unknown as RpcClient;
  const workDir = join(tmpdir(), `nexus-codex-runtime-test-${Date.now()}`);
  const config = loadConfig({
    CODEX_APPSERVER_WORK_DIR: workDir,
    CODEX_APPSERVER_MODEL: "gpt-5.5",
    CODEX_APPSERVER_REASONING_EFFORT: "low",
    CODEX_APPSERVER_SERVICE_TIER: "priority",
  });
  const request: ReplyRequest = {
    login: {
      type: "chatgptAuthTokens",
      accessToken: "test-token",
      chatgptAccountId: "acct",
      chatgptPlanType: "plus",
    },
    body: "Where is my parcel?",
    messages: [],
    tracking_fact_summary: null,
    tracking_fact_evidence_present: false,
  };

  const result = await runEphemeralThread(client, config, request, 1000);

  const threadStart = calls.find((call) => call.method === "thread/start");
  const turnStart = calls.find((call) => call.method === "turn/start");
  assert.equal(result.assistantText.includes("tracking_missing_number"), true);
  assert.equal(threadStart?.params.cwd, workDir);
  assert.equal(threadStart?.params.serviceTier, "priority");
  assert.equal(turnStart?.params.serviceTier, "priority");
  assert.equal(turnStart?.params.effort, "low");
  assert.equal(turnStart?.params.collaborationMode.settings.reasoning_effort, "low");
  assert.deepEqual(turnStart?.params.dynamicTools, []);
});

import assert from "node:assert/strict";
import test from "node:test";
import { notificationMatchesTurn } from "../src/notification-correlation.js";

test("notification correlation matches flat thread and turn ids", () => {
  assert.equal(
    notificationMatchesTurn(
      { method: "item/agentMessage/delta", params: { threadId: "thread-1", turnId: "turn-1" } },
      { threadId: "thread-1", turnId: "turn-1" },
    ),
    true,
  );
});

test("notification correlation matches nested turn shape", () => {
  assert.equal(
    notificationMatchesTurn(
      { method: "turn/completed", params: { turn: { threadId: "thread-1", id: "turn-1" } } },
      { threadId: "thread-1", turnId: "turn-1" },
    ),
    true,
  );
});

test("notification correlation rejects another account turn", () => {
  assert.equal(
    notificationMatchesTurn(
      { method: "turn/completed", params: { threadId: "thread-2", turnId: "turn-1" } },
      { threadId: "thread-1", turnId: "turn-1" },
    ),
    false,
  );
});

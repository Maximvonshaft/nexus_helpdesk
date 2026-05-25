import { RuntimeError, classifyAuthFailure } from "./errors.js";
import type { RpcClient } from "./rpc-client.js";
import type { LoginPayload } from "./reply-contract.js";

export async function initializeClient(client: RpcClient, timeoutMs: number): Promise<void> {
  await client.request("initialize", {
    clientInfo: {
      name: "nexus-codex-runtime",
      title: "Nexus Codex Runtime",
      version: "0.1.0",
    },
    capabilities: {
      experimentalApi: true,
      optOutNotificationMethods: [],
    },
  }, timeoutMs);
}

export async function loginAccount(client: RpcClient, login: LoginPayload, timeoutMs: number): Promise<void> {
  try {
    await client.request("account/login/start", {
      type: "chatgptAuthTokens",
      accessToken: login.accessToken,
      chatgptAccountId: login.chatgptAccountId,
      chatgptPlanType: login.chatgptPlanType ?? null,
    }, timeoutMs);
  } catch (error) {
    if (classifyAuthFailure(error)) {
      throw new RuntimeError(401, "codex_login_failed", "codex_login_failed", "login");
    }
    throw new RuntimeError(502, "codex_login_failed", "codex_login_failed", "login");
  }
}

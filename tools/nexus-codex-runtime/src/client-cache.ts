import type { RuntimeConfig } from "./env.js";
import type { LoginPayload } from "./reply-contract.js";
import { fingerprintSecret, stableHash } from "./redaction.js";
import { startAppServer } from "./appserver-process.js";
import { initializeClient } from "./account-login.js";
import type { RpcClient } from "./rpc-client.js";

export type CacheLookup = {
  client: RpcClient;
  cache: "hit" | "miss";
  appserverStartMs: number;
  initializeMs: number;
};

type Entry = {
  client: RpcClient;
  expiresAt: number;
  promise?: Promise<CacheLookup>;
};

export function clientCacheKey(input: {
  tenantId: string;
  login: LoginPayload;
  model: string;
  runtimeStartOptionsHash: string;
}): string {
  return stableHash({
    tenant_id: input.tenantId,
    chatgptAccountId: input.login.chatgptAccountId,
    chatgptPlanType: input.login.chatgptPlanType ?? null,
    token_fingerprint: fingerprintSecret(input.login.accessToken),
    model: input.model,
    runtime_start_options_hash: input.runtimeStartOptionsHash,
  });
}

export class ClientCache {
  private readonly entries = new Map<string, Entry>();

  constructor(private readonly config: RuntimeConfig) {}

  async getOrCreate(key: string, timeoutMs: number): Promise<CacheLookup> {
    const now = Date.now();
    const existing = this.entries.get(key);
    if (existing && existing.expiresAt > now && !existing.promise) {
      existing.expiresAt = now + this.config.clientCacheTtlSeconds * 1000;
      return { client: existing.client, cache: "hit", appserverStartMs: 0, initializeMs: 0 };
    }
    if (existing?.promise) {
      return existing.promise;
    }
    if (existing?.client) {
      existing.client.close();
      this.entries.delete(key);
    }
    const promise = this.create(key, timeoutMs);
    this.entries.set(key, {
      client: undefined as unknown as RpcClient,
      expiresAt: now + this.config.clientCacheTtlSeconds * 1000,
      promise,
    });
    try {
      return await promise;
    } catch (error) {
      this.entries.delete(key);
      throw error;
    }
  }

  private async create(key: string, timeoutMs: number): Promise<CacheLookup> {
    const appStart = Date.now();
    const started = startAppServer(this.config, key);
    const appserverStartMs = Date.now() - appStart;
    const initStart = Date.now();
    await initializeClient(started.client, timeoutMs);
    const initializeMs = Date.now() - initStart;
    this.entries.set(key, {
      client: started.client,
      expiresAt: Date.now() + this.config.clientCacheTtlSeconds * 1000,
    });
    return { client: started.client, cache: "miss", appserverStartMs, initializeMs };
  }
}

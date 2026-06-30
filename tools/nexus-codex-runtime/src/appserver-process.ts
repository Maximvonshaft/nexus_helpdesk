import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { spawn } from "node:child_process";
import { RpcClient } from "./rpc-client.js";
import type { RuntimeConfig } from "./env.js";

const CLEARED_ENV = ["CODEX_API_KEY", "OPENAI_API_KEY", "OPENAI_ACCESS_TOKEN", "CODEX_ACCESS_TOKEN", "EXTERNAL_CHANNEL_HOME"];

export type StartedAppServer = {
  client: RpcClient;
  codexHome: string;
  nativeHome: string;
};

export function startAppServer(config: RuntimeConfig, cacheKeyHash: string): StartedAppServer {
  const codexHome = join(config.stateDir, cacheKeyHash, "codex-home");
  const nativeHome = join(config.stateDir, cacheKeyHash, "home");
  mkdirSync(codexHome, { recursive: true, mode: 0o700 });
  mkdirSync(nativeHome, { recursive: true, mode: 0o700 });
  const env = { ...process.env };
  for (const key of CLEARED_ENV) {
    delete env[key];
  }
  env.CODEX_HOME = codexHome;
  env.HOME = nativeHome;
  env.XDG_CONFIG_HOME = codexHome;
  const child = spawn(config.codexCommand, config.codexArgs, {
    env,
    shell: false,
    stdio: "pipe",
    windowsHide: true,
  });
  return { client: new RpcClient(child), codexHome, nativeHome };
}

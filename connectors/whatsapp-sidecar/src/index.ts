import { AccountRegistry } from "./accountRegistry.js";
import { loadConfig } from "./config.js";
import { createLogger } from "./logger.js";
import { createSidecarServer } from "./server.js";

const config = loadConfig();
const logger = createLogger(config.logLevel);
const registry = new AccountRegistry(config, logger);
const server = createSidecarServer(config, logger, registry);

server.listen(config.port, () => {
  logger.info({ port: config.port, mode: config.mode }, "whatsapp_sidecar_started");
  for (const accountId of config.autoStartAccounts) {
    void registry.start(accountId).catch((error) => {
      logger.error({ account_id: accountId, error }, "whatsapp_sidecar_auto_start_failed");
    });
  }
});

function shutdown(signal: string): void {
  logger.info({ signal }, "whatsapp_sidecar_stopping");
  server.close(() => process.exit(0));
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);

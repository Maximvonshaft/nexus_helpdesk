import { AccountRegistry } from "./accountRegistry.js";
import { loadConfig } from "./config.js";
import { createLogger } from "./logger.js";
import { createSidecarServer } from "./server.js";

const config = loadConfig();
const logger = createLogger(config.logLevel);
const registry = new AccountRegistry(config, logger);
const server = createSidecarServer(config, logger, registry);

let stopping = false;
let reconcileRunning = false;
let callbackDrainRunning = false;

async function reconcileOnce(): Promise<void> {
  if (stopping || reconcileRunning) return;
  reconcileRunning = true;
  try {
    const desired = await registry.backend.fetchDesiredAccounts();
    await registry.reconcile(desired.accounts);
    registry.recordAuthoritySuccess();
  } catch (error) {
    registry.recordAuthorityFailure(error);
    logger.warn(
      { error_code: registry.readiness().last_authority_error_code },
      "whatsapp_desired_state_reconciliation_failed"
    );
  } finally {
    reconcileRunning = false;
  }
}

async function drainCallbacksOnce(): Promise<void> {
  if (stopping || callbackDrainRunning) return;
  callbackDrainRunning = true;
  try {
    await registry.backend.flushCallbacks();
  } catch (error) {
    logger.warn(
      { error_name: error instanceof Error ? error.name : "UnknownError" },
      "whatsapp_callback_outbox_drain_failed"
    );
  } finally {
    callbackDrainRunning = false;
  }
}

const reconcileTimer = setInterval(() => {
  void reconcileOnce();
}, config.reconcileIntervalMs);
reconcileTimer.unref?.();

const callbackTimer = setInterval(() => {
  void drainCallbacksOnce();
}, config.callbackRetryIntervalMs);
callbackTimer.unref?.();

server.listen(config.port, () => {
  logger.info({ port: config.port, mode: config.mode }, "whatsapp_sidecar_started");
  void drainCallbacksOnce();
  void reconcileOnce();
});

async function shutdown(signal: string): Promise<void> {
  if (stopping) return;
  stopping = true;
  clearInterval(reconcileTimer);
  clearInterval(callbackTimer);
  logger.info({ signal }, "whatsapp_sidecar_stopping");
  await registry.reconcile([]).catch((error) => {
    logger.warn(
      { error_name: error instanceof Error ? error.name : "UnknownError" },
      "whatsapp_sidecar_listener_shutdown_failed"
    );
  });
  await registry.backend.flushCallbacks().catch((error) => {
    logger.warn(
      { error_name: error instanceof Error ? error.name : "UnknownError" },
      "whatsapp_sidecar_final_callback_flush_failed"
    );
  });
  await new Promise<void>((resolveClose) => server.close(() => resolveClose()));
}

for (const signal of ["SIGINT", "SIGTERM"] as const) {
  process.on(signal, () => {
    void shutdown(signal).finally(() => process.exit(0));
  });
}

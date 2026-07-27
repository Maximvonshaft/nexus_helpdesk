import { createHash, randomUUID } from "node:crypto";
import {
  mkdirSync,
  readdirSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync
} from "node:fs";
import { basename, dirname, join, resolve } from "node:path";
import type { Logger } from "pino";

export interface CallbackEnvelope {
  id: string;
  path: string;
  account_id: string;
  payload: unknown;
  attempts: number;
  next_attempt_at: number;
  created_at: string;
}

export class DurableCallbackOutbox {
  constructor(
    private readonly root: string,
    private readonly logger: Logger
  ) {
    mkdirSync(root, { recursive: true, mode: 0o700 });
  }

  enqueue(params: {
    path: string;
    accountId: string;
    payload: unknown;
    dedupeKey?: string;
  }): string {
    const digest = params.dedupeKey
      ? createHash("sha256").update(params.dedupeKey).digest("hex")
      : randomUUID();
    const id = `${Date.now()}-${digest}`;
    const envelope: CallbackEnvelope = {
      id,
      path: params.path,
      account_id: params.accountId,
      payload: params.payload,
      attempts: 0,
      next_attempt_at: Date.now(),
      created_at: new Date().toISOString()
    };
    this.write(envelope);
    return id;
  }

  async drain(
    sender: (envelope: CallbackEnvelope) => Promise<void>,
    limit = 100
  ): Promise<{ delivered: number; pending: number }> {
    const files = readdirSync(this.root)
      .filter((name) => name.endsWith(".json"))
      .sort()
      .slice(0, limit);
    let delivered = 0;
    let pending = 0;
    for (const file of files) {
      const path = resolve(join(this.root, file));
      let envelope: CallbackEnvelope;
      try {
        envelope = JSON.parse(readFileSync(path, "utf8")) as CallbackEnvelope;
      } catch {
        this.logger.error({ callback_id: file }, "callback_outbox_corrupt");
        rmSync(path, { force: true });
        continue;
      }
      if (envelope.next_attempt_at > Date.now()) {
        pending += 1;
        continue;
      }
      try {
        await sender(envelope);
        rmSync(path, { force: true });
        delivered += 1;
      } catch (error) {
        envelope.attempts += 1;
        const backoffMs = Math.min(1000 * 2 ** Math.min(envelope.attempts, 10), 300000);
        envelope.next_attempt_at = Date.now() + backoffMs;
        this.write(envelope);
        pending += 1;
        this.logger.warn(
          {
            callback_id: envelope.id,
            account_id: envelope.account_id,
            callback_path: envelope.path,
            attempts: envelope.attempts,
            error
          },
          "callback_outbox_delivery_failed"
        );
      }
    }
    return { delivered, pending };
  }

  count(): number {
    return readdirSync(this.root).filter((name) => name.endsWith(".json")).length;
  }

  private write(envelope: CallbackEnvelope): void {
    const finalPath = resolve(join(this.root, `${envelope.id}.json`));
    const temporary = join(
      dirname(finalPath),
      `.${basename(finalPath)}.${randomUUID()}.tmp`
    );
    writeFileSync(temporary, JSON.stringify(envelope), { mode: 0o600 });
    renameSync(temporary, finalPath);
  }
}

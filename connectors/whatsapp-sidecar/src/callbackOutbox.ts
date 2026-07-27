import {
  createCipheriv,
  createDecipheriv,
  createHash,
  hkdfSync,
  randomBytes,
  randomUUID
} from "node:crypto";
import {
  mkdirSync,
  readdirSync,
  readFileSync,
  renameSync,
  rmSync,
  statSync,
  writeFileSync
} from "node:fs";
import { basename, dirname, join, resolve } from "node:path";
import type { Logger } from "pino";
import { assertSafeAccountId } from "./sessionStore.js";

const STORED_SCHEMA = "nexus.whatsapp.callback.encrypted.v1";
const ENVELOPE_SCHEMA = "nexus.whatsapp.callback.v1";
const MAX_CALLBACK_FILE_BYTES = 256 * 1024;
const MAX_CALLBACK_ATTEMPTS = 20;

export type CallbackKind = "inbound" | "status" | "delivery";

export interface CallbackEnvelope {
  schema: typeof ENVELOPE_SCHEMA;
  id: string;
  kind: CallbackKind;
  account_id: string;
  payload: unknown;
  attempts: number;
  next_attempt_at: number;
  created_at: string;
}

interface StoredCallback {
  schema: typeof STORED_SCHEMA;
  id: string;
  nonce: string;
  ciphertext: string;
  auth_tag: string;
}

export class DurableCallbackOutbox {
  private readonly encryptionKey: Buffer;

  constructor(
    private readonly root: string,
    private readonly logger: Logger,
    integritySecret: string
  ) {
    if (integritySecret.trim().length < 32) {
      throw new Error("callback_outbox_secret_too_short");
    }
    this.encryptionKey = Buffer.from(
      hkdfSync(
        "sha256",
        Buffer.from(integritySecret, "utf8"),
        Buffer.from("nexus-whatsapp-callback-spool", "utf8"),
        Buffer.from("aes-256-gcm-v1", "utf8"),
        32
      )
    );
    mkdirSync(root, { recursive: true, mode: 0o700 });
  }

  enqueue(params: {
    kind: CallbackKind;
    accountId: string;
    payload: unknown;
    dedupeKey?: string;
  }): string {
    const accountId = assertSafeAccountId(params.accountId);
    const id = params.dedupeKey
      ? `replaceable-${createHash("sha256").update(params.dedupeKey).digest("hex")}`
      : `${Date.now()}-${randomUUID()}`;
    const envelope: CallbackEnvelope = {
      schema: ENVELOPE_SCHEMA,
      id,
      kind: assertCallbackKind(params.kind),
      account_id: accountId,
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
      .slice(0, Math.max(1, Math.min(limit, 1000)));
    let delivered = 0;
    let pending = 0;
    for (const file of files) {
      const path = resolve(join(this.root, file));
      let envelope: CallbackEnvelope;
      try {
        if (statSync(path).size > MAX_CALLBACK_FILE_BYTES) {
          throw new Error("callback_outbox_file_too_large");
        }
        envelope = this.decrypt(readFileSync(path, "utf8"));
      } catch (error) {
        this.logger.error(
          {
            callback_id: basename(file, ".json"),
            error_name: error instanceof Error ? error.name : "UnknownError"
          },
          "callback_outbox_quarantined"
        );
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
        if (envelope.attempts >= MAX_CALLBACK_ATTEMPTS) {
          this.logger.error(
            {
              callback_id: envelope.id,
              account_id: envelope.account_id,
              callback_kind: envelope.kind,
              attempts: envelope.attempts
            },
            "callback_outbox_dead"
          );
          rmSync(path, { force: true });
          continue;
        }
        const backoffMs = Math.min(
          1000 * 2 ** Math.min(envelope.attempts, 10),
          300000
        );
        envelope.next_attempt_at = Date.now() + backoffMs;
        this.write(envelope);
        pending += 1;
        this.logger.warn(
          {
            callback_id: envelope.id,
            account_id: envelope.account_id,
            callback_kind: envelope.kind,
            attempts: envelope.attempts,
            error_name: error instanceof Error ? error.name : "UnknownError"
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
    const serialized = JSON.stringify(envelope);
    if (Buffer.byteLength(serialized, "utf8") > MAX_CALLBACK_FILE_BYTES) {
      throw new Error("callback_outbox_payload_too_large");
    }
    const nonce = randomBytes(12);
    const cipher = createCipheriv("aes-256-gcm", this.encryptionKey, nonce);
    cipher.setAAD(Buffer.from(STORED_SCHEMA, "utf8"));
    const ciphertext = Buffer.concat([
      cipher.update(serialized, "utf8"),
      cipher.final()
    ]);
    const stored: StoredCallback = {
      schema: STORED_SCHEMA,
      id: envelope.id,
      nonce: nonce.toString("base64"),
      ciphertext: ciphertext.toString("base64"),
      auth_tag: cipher.getAuthTag().toString("base64")
    };
    writeFileSync(temporary, JSON.stringify(stored), { mode: 0o600 });
    renameSync(temporary, finalPath);
  }

  private decrypt(serialized: string): CallbackEnvelope {
    const stored = JSON.parse(serialized) as Partial<StoredCallback>;
    if (
      stored.schema !== STORED_SCHEMA ||
      typeof stored.id !== "string" ||
      typeof stored.nonce !== "string" ||
      typeof stored.ciphertext !== "string" ||
      typeof stored.auth_tag !== "string"
    ) {
      throw new Error("callback_outbox_schema_invalid");
    }
    const decipher = createDecipheriv(
      "aes-256-gcm",
      this.encryptionKey,
      Buffer.from(stored.nonce, "base64")
    );
    decipher.setAAD(Buffer.from(STORED_SCHEMA, "utf8"));
    decipher.setAuthTag(Buffer.from(stored.auth_tag, "base64"));
    const plaintext = Buffer.concat([
      decipher.update(Buffer.from(stored.ciphertext, "base64")),
      decipher.final()
    ]).toString("utf8");
    const candidate = JSON.parse(plaintext) as Partial<CallbackEnvelope>;
    if (
      candidate.schema !== ENVELOPE_SCHEMA ||
      candidate.id !== stored.id ||
      typeof candidate.account_id !== "string" ||
      typeof candidate.attempts !== "number" ||
      !Number.isInteger(candidate.attempts) ||
      candidate.attempts < 0 ||
      typeof candidate.next_attempt_at !== "number" ||
      !Number.isFinite(candidate.next_attempt_at) ||
      typeof candidate.created_at !== "string"
    ) {
      throw new Error("callback_outbox_envelope_invalid");
    }
    return {
      schema: ENVELOPE_SCHEMA,
      id: candidate.id,
      kind: assertCallbackKind(candidate.kind),
      account_id: assertSafeAccountId(candidate.account_id),
      payload: candidate.payload,
      attempts: candidate.attempts,
      next_attempt_at: candidate.next_attempt_at,
      created_at: candidate.created_at
    };
  }
}

function assertCallbackKind(value: unknown): CallbackKind {
  if (value === "inbound" || value === "status" || value === "delivery") {
    return value;
  }
  throw new Error("callback_outbox_kind_invalid");
}

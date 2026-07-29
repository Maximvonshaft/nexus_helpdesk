import {
  createCipheriv,
  createDecipheriv,
  createHash,
  hkdfSync,
  randomBytes,
  randomUUID
} from "node:crypto";
import {
  closeSync,
  existsSync,
  fstatSync,
  mkdirSync,
  openSync,
  readFileSync,
  readdirSync,
  renameSync,
  rmSync,
  writeFileSync
} from "node:fs";
import { basename, dirname, join, resolve } from "node:path";
import { proto } from "@whiskeysockets/baileys";
import type { Logger } from "pino";
import { assertSafeAccountId } from "./sessionStore.js";
import type { WhatsAppMediaKind } from "./types.js";

const MAGIC = Buffer.from("NXWAD01\0", "ascii");
const NONCE_BYTES = 12;
const TAG_BYTES = 16;
const LENGTH_BYTES = 4;
const MAX_MESSAGE_BYTES = 4 * 1024 * 1024;
const MAX_FILE_BYTES = MAX_MESSAGE_BYTES + 64 * 1024;
const MAX_ATTEMPTS = 20;
const ENVELOPE_SCHEMA = "nexus.whatsapp.media-download.v1";
const DEAD_SCHEMA = "nexus.whatsapp.media-download-dead.v1";

export interface MediaDownloadEnvelope {
  schema: typeof ENVELOPE_SCHEMA;
  id: string;
  account_id: string;
  external_message_id: string;
  media_kind: WhatsAppMediaKind;
  media_type: string;
  file_name: string | null;
  attempts: number;
  next_attempt_at: number;
  created_at: string;
  raw_message: proto.IWebMessageInfo;
}

interface StoredMetadata {
  schema: typeof ENVELOPE_SCHEMA;
  id: string;
  account_id: string;
  external_message_id: string;
  media_kind: WhatsAppMediaKind;
  media_type: string;
  file_name: string | null;
  attempts: number;
  next_attempt_at: number;
  created_at: string;
  raw_sha256: string;
}

interface DeadEvidence {
  id: string;
  accountId?: string;
  externalMessageId?: string;
  attempts?: number;
  reason: string;
}

export class DurableMediaDownloadOutbox {
  private readonly encryptionKey: Buffer;

  constructor(
    private readonly root: string,
    private readonly logger: Logger,
    integritySecret: string
  ) {
    if (integritySecret.trim().length < 32) {
      throw new Error("media_download_outbox_secret_too_short");
    }
    this.encryptionKey = Buffer.from(
      hkdfSync(
        "sha256",
        Buffer.from(integritySecret, "utf8"),
        Buffer.from("nexus-whatsapp-media-download-spool", "utf8"),
        Buffer.from("aes-256-gcm-v1", "utf8"),
        32
      )
    );
    mkdirSync(root, { recursive: true, mode: 0o700 });
  }

  enqueue(params: {
    accountId: string;
    externalMessageId: string;
    mediaKind: WhatsAppMediaKind;
    mediaType: string;
    fileName?: string | null;
    rawMessage: proto.IWebMessageInfo;
  }): string {
    const accountId = assertSafeAccountId(params.accountId);
    const externalMessageId = assertMessageId(params.externalMessageId);
    const mediaKind = assertMediaKind(params.mediaKind);
    const mediaType = assertMediaType(params.mediaType);
    const fileName = safeFileName(params.fileName);
    const id = createHash("sha256")
      .update(`${accountId}\n${externalMessageId}`)
      .digest("hex");
    const pendingPath = resolve(join(this.root, `${id}.download`));
    const deadPath = resolve(join(this.root, `${id}.dead`));
    if (existsSync(pendingPath) || existsSync(deadPath)) {
      return id;
    }
    const raw = Buffer.from(proto.WebMessageInfo.encode(params.rawMessage).finish());
    if (!raw.length || raw.length > MAX_MESSAGE_BYTES) {
      throw new Error("media_download_outbox_message_size_invalid");
    }
    const envelope: MediaDownloadEnvelope = {
      schema: ENVELOPE_SCHEMA,
      id,
      account_id: accountId,
      external_message_id: externalMessageId,
      media_kind: mediaKind,
      media_type: mediaType,
      file_name: fileName,
      attempts: 0,
      next_attempt_at: Date.now(),
      created_at: new Date().toISOString(),
      raw_message: params.rawMessage
    };
    this.write(envelope, raw);
    return id;
  }

  async drainAccount(
    accountId: string,
    sender: (envelope: MediaDownloadEnvelope) => Promise<void>,
    limit = 20,
    now = Date.now()
  ): Promise<{ delivered: number; pending: number; dead: number }> {
    const safeAccountId = assertSafeAccountId(accountId);
    const batchLimit = Math.max(1, Math.min(limit, 100));
    const files = readdirSync(this.root)
      .filter((name) => name.endsWith(".download"))
      .sort();
    let delivered = 0;
    let pending = 0;
    let dead = 0;
    let selectedDue = 0;
    for (const file of files) {
      if (selectedDue >= batchLimit) break;
      const path = resolve(join(this.root, file));
      let envelope: MediaDownloadEnvelope;
      try {
        envelope = this.read(path);
      } catch (error) {
        const id = basename(file, ".download");
        this.replaceWithDeadEvidence(path, {
          id,
          reason: "media_download_outbox_corrupt"
        });
        dead += 1;
        this.logger.error(
          {
            media_download_id: id,
            error_name: error instanceof Error ? error.name : "UnknownError"
          },
          "media_download_outbox_quarantined"
        );
        continue;
      }
      if (envelope.account_id !== safeAccountId) continue;
      if (envelope.next_attempt_at > now) {
        pending += 1;
        continue;
      }
      selectedDue += 1;
      try {
        await sender(envelope);
        rmSync(path, { force: true });
        delivered += 1;
      } catch (error) {
        envelope.attempts += 1;
        const retryable = (error as { retryable?: unknown })?.retryable !== false;
        if (!retryable || envelope.attempts >= MAX_ATTEMPTS) {
          this.replaceWithDeadEvidence(path, {
            id: envelope.id,
            accountId: envelope.account_id,
            externalMessageId: envelope.external_message_id,
            attempts: envelope.attempts,
            reason: retryable
              ? "media_download_attempts_exhausted"
              : "media_download_non_retryable"
          });
          dead += 1;
          this.logger.error(
            {
              media_download_id: envelope.id,
              account_id: envelope.account_id,
              external_message_id_hash: sha256(envelope.external_message_id),
              attempts: envelope.attempts,
              retryable
            },
            "media_download_outbox_dead"
          );
          continue;
        }
        const backoffMs = Math.min(
          1000 * 2 ** Math.min(envelope.attempts, 10),
          300000
        );
        envelope.next_attempt_at = now + backoffMs;
        this.write(envelope);
        pending += 1;
        this.logger.warn(
          {
            media_download_id: envelope.id,
            account_id: envelope.account_id,
            attempts: envelope.attempts,
            error_name: error instanceof Error ? error.name : "UnknownError"
          },
          "media_download_retry_scheduled"
        );
      }
    }
    return { delivered, pending, dead };
  }

  count(): number {
    return readdirSync(this.root).filter((name) => name.endsWith(".download")).length;
  }

  countDead(): number {
    return readdirSync(this.root).filter((name) => name.endsWith(".dead")).length;
  }

  private write(envelope: MediaDownloadEnvelope, encoded?: Buffer): void {
    const raw = encoded ?? Buffer.from(proto.WebMessageInfo.encode(envelope.raw_message).finish());
    if (!raw.length || raw.length > MAX_MESSAGE_BYTES) {
      throw new Error("media_download_outbox_message_size_invalid");
    }
    const metadata: StoredMetadata = {
      schema: ENVELOPE_SCHEMA,
      id: envelope.id,
      account_id: envelope.account_id,
      external_message_id: envelope.external_message_id,
      media_kind: envelope.media_kind,
      media_type: envelope.media_type,
      file_name: envelope.file_name,
      attempts: envelope.attempts,
      next_attempt_at: envelope.next_attempt_at,
      created_at: envelope.created_at,
      raw_sha256: createHash("sha256").update(raw).digest("hex")
    };
    const metadataBytes = Buffer.from(JSON.stringify(metadata), "utf8");
    if (!metadataBytes.length || metadataBytes.length > 32 * 1024) {
      throw new Error("media_download_outbox_metadata_size_invalid");
    }
    const length = Buffer.allocUnsafe(LENGTH_BYTES);
    length.writeUInt32BE(metadataBytes.length, 0);
    const plaintext = Buffer.concat([length, metadataBytes, raw]);
    if (plaintext.length > MAX_FILE_BYTES) {
      throw new Error("media_download_outbox_file_size_invalid");
    }
    const nonce = randomBytes(NONCE_BYTES);
    const cipher = createCipheriv("aes-256-gcm", this.encryptionKey, nonce);
    cipher.setAAD(MAGIC);
    const ciphertext = Buffer.concat([cipher.update(plaintext), cipher.final()]);
    const payload = Buffer.concat([MAGIC, nonce, cipher.getAuthTag(), ciphertext]);
    const finalPath = resolve(join(this.root, `${envelope.id}.download`));
    const temporary = join(
      dirname(finalPath),
      `.${basename(finalPath)}.${randomUUID()}.tmp`
    );
    writeFileSync(temporary, payload, { mode: 0o600 });
    renameSync(temporary, finalPath);
  }

  private read(path: string): MediaDownloadEnvelope {
    const descriptor = openSync(path, "r");
    let payload: Buffer;
    try {
      const stat = fstatSync(descriptor);
      if (!stat.isFile() || stat.size <= MAGIC.length + NONCE_BYTES + TAG_BYTES || stat.size > MAX_FILE_BYTES) {
        throw new Error("media_download_outbox_file_size_invalid");
      }
      payload = readFileSync(descriptor);
    } finally {
      closeSync(descriptor);
    }
    if (!payload.subarray(0, MAGIC.length).equals(MAGIC)) {
      throw new Error("media_download_outbox_magic_invalid");
    }
    const nonceStart = MAGIC.length;
    const tagStart = nonceStart + NONCE_BYTES;
    const ciphertextStart = tagStart + TAG_BYTES;
    const decipher = createDecipheriv(
      "aes-256-gcm",
      this.encryptionKey,
      payload.subarray(nonceStart, tagStart)
    );
    decipher.setAAD(MAGIC);
    decipher.setAuthTag(payload.subarray(tagStart, ciphertextStart));
    const plaintext = Buffer.concat([
      decipher.update(payload.subarray(ciphertextStart)),
      decipher.final()
    ]);
    if (plaintext.length <= LENGTH_BYTES) {
      throw new Error("media_download_outbox_plaintext_invalid");
    }
    const metadataLength = plaintext.readUInt32BE(0);
    if (metadataLength <= 0 || metadataLength > 32 * 1024 || LENGTH_BYTES + metadataLength >= plaintext.length) {
      throw new Error("media_download_outbox_metadata_size_invalid");
    }
    const candidate = JSON.parse(
      plaintext.subarray(LENGTH_BYTES, LENGTH_BYTES + metadataLength).toString("utf8")
    ) as Partial<StoredMetadata>;
    const raw = plaintext.subarray(LENGTH_BYTES + metadataLength);
    if (
      candidate.schema !== ENVELOPE_SCHEMA ||
      typeof candidate.id !== "string" ||
      typeof candidate.account_id !== "string" ||
      typeof candidate.external_message_id !== "string" ||
      typeof candidate.media_type !== "string" ||
      typeof candidate.raw_sha256 !== "string" ||
      typeof candidate.attempts !== "number" ||
      !Number.isInteger(candidate.attempts) ||
      candidate.attempts < 0 ||
      typeof candidate.next_attempt_at !== "number" ||
      !Number.isFinite(candidate.next_attempt_at) ||
      typeof candidate.created_at !== "string" ||
      !raw.length ||
      raw.length > MAX_MESSAGE_BYTES
    ) {
      throw new Error("media_download_outbox_envelope_invalid");
    }
    if (candidate.raw_sha256 !== createHash("sha256").update(raw).digest("hex")) {
      throw new Error("media_download_outbox_sha256_mismatch");
    }
    return {
      schema: ENVELOPE_SCHEMA,
      id: candidate.id,
      account_id: assertSafeAccountId(candidate.account_id),
      external_message_id: assertMessageId(candidate.external_message_id),
      media_kind: assertMediaKind(candidate.media_kind),
      media_type: assertMediaType(candidate.media_type),
      file_name: safeFileName(candidate.file_name),
      attempts: candidate.attempts,
      next_attempt_at: candidate.next_attempt_at,
      created_at: candidate.created_at,
      raw_message: proto.WebMessageInfo.decode(raw)
    };
  }

  private replaceWithDeadEvidence(path: string, evidence: DeadEvidence): void {
    const target = resolve(join(this.root, `${evidence.id}.dead`));
    const temporary = join(
      dirname(target),
      `.${basename(target)}.${randomUUID()}.tmp`
    );
    const payload = {
      schema: DEAD_SCHEMA,
      id: evidence.id,
      account_id: evidence.accountId || null,
      external_message_id_sha256: evidence.externalMessageId
        ? sha256(evidence.externalMessageId)
        : null,
      attempts: evidence.attempts ?? null,
      reason: evidence.reason,
      failed_at: new Date().toISOString()
    };
    writeFileSync(temporary, Buffer.from(JSON.stringify(payload), "utf8"), {
      mode: 0o600
    });
    renameSync(temporary, target);
    rmSync(path, { force: true });
  }
}

function sha256(value: string): string {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

function assertMessageId(value: unknown): string {
  const normalized = String(value || "").trim();
  if (!normalized || normalized.length > 180 || /[\r\n\x00]/.test(normalized)) {
    throw new Error("media_download_outbox_message_id_invalid");
  }
  return normalized;
}

function assertMediaKind(value: unknown): WhatsAppMediaKind {
  if (value === "image" || value === "video" || value === "audio" || value === "document" || value === "sticker") {
    return value;
  }
  throw new Error("media_download_outbox_kind_invalid");
}

function assertMediaType(value: unknown): string {
  const normalized = String(value || "application/octet-stream")
    .split(";", 1)[0]
    .trim()
    .toLowerCase();
  if (!normalized || normalized.length > 160 || !/^[a-z0-9.+-]+\/[a-z0-9.+-]+$/.test(normalized)) {
    throw new Error("media_download_outbox_type_invalid");
  }
  return normalized;
}

function safeFileName(value: unknown): string | null {
  const normalized = String(value || "")
    .replace(/\\/g, "/")
    .split("/")
    .pop()
    ?.replace(/[\r\n\x00]/g, "")
    .trim();
  return normalized ? normalized.slice(-200) : null;
}

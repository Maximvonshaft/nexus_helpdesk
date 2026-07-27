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
import type { Logger } from "pino";
import { assertSafeAccountId } from "./sessionStore.js";

const MAGIC = Buffer.from("NXWAM01\0", "ascii");
const NONCE_BYTES = 12;
const TAG_BYTES = 16;
const LENGTH_BYTES = 4;
const MAX_MEDIA_BYTES = 100 * 1024 * 1024;
const MAX_FILE_BYTES = MAX_MEDIA_BYTES + 64 * 1024;
const MAX_ATTEMPTS = 20;
const ENVELOPE_SCHEMA = "nexus.whatsapp.media.v1";

export type InboundMediaKind = "image" | "video" | "audio" | "document" | "sticker";

export interface InboundMediaEnvelope {
  schema: typeof ENVELOPE_SCHEMA;
  id: string;
  account_id: string;
  external_message_id: string;
  media_kind: InboundMediaKind;
  media_type: string;
  file_name: string | null;
  sha256: string;
  attempts: number;
  next_attempt_at: number;
  created_at: string;
  content: Buffer;
}

interface StoredMetadata {
  schema: typeof ENVELOPE_SCHEMA;
  id: string;
  account_id: string;
  external_message_id: string;
  media_kind: InboundMediaKind;
  media_type: string;
  file_name: string | null;
  sha256: string;
  attempts: number;
  next_attempt_at: number;
  created_at: string;
}

export class DurableMediaOutbox {
  private readonly encryptionKey: Buffer;

  constructor(
    private readonly root: string,
    private readonly logger: Logger,
    integritySecret: string
  ) {
    if (integritySecret.trim().length < 32) {
      throw new Error("media_outbox_secret_too_short");
    }
    this.encryptionKey = Buffer.from(
      hkdfSync(
        "sha256",
        Buffer.from(integritySecret, "utf8"),
        Buffer.from("nexus-whatsapp-media-spool", "utf8"),
        Buffer.from("aes-256-gcm-v1", "utf8"),
        32
      )
    );
    mkdirSync(root, { recursive: true, mode: 0o700 });
  }

  enqueue(params: {
    accountId: string;
    externalMessageId: string;
    mediaKind: InboundMediaKind;
    mediaType: string;
    fileName?: string | null;
    content: Buffer;
  }): string {
    const accountId = assertSafeAccountId(params.accountId);
    const externalMessageId = assertMessageId(params.externalMessageId);
    const mediaKind = assertMediaKind(params.mediaKind);
    const mediaType = assertMediaType(params.mediaType);
    const fileName = safeFileName(params.fileName);
    if (!Buffer.isBuffer(params.content) || params.content.length <= 0 || params.content.length > MAX_MEDIA_BYTES) {
      throw new Error("media_outbox_content_size_invalid");
    }
    const id = createHash("sha256")
      .update(`${accountId}\n${externalMessageId}`)
      .digest("hex");
    const envelope: InboundMediaEnvelope = {
      schema: ENVELOPE_SCHEMA,
      id,
      account_id: accountId,
      external_message_id: externalMessageId,
      media_kind: mediaKind,
      media_type: mediaType,
      file_name: fileName,
      sha256: createHash("sha256").update(params.content).digest("hex"),
      attempts: 0,
      next_attempt_at: Date.now(),
      created_at: new Date().toISOString(),
      content: params.content
    };
    this.write(envelope);
    return id;
  }

  async drain(
    sender: (envelope: InboundMediaEnvelope) => Promise<void>,
    limit = 20
  ): Promise<{ delivered: number; pending: number }> {
    const files = readdirSync(this.root)
      .filter((name) => name.endsWith(".media"))
      .sort()
      .slice(0, Math.max(1, Math.min(limit, 100)));
    let delivered = 0;
    let pending = 0;
    for (const file of files) {
      const path = resolve(join(this.root, file));
      let envelope: InboundMediaEnvelope;
      try {
        envelope = this.read(path);
      } catch (error) {
        this.logger.error(
          {
            media_id: basename(file, ".media"),
            error_name: error instanceof Error ? error.name : "UnknownError"
          },
          "media_outbox_quarantined"
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
        if (envelope.attempts >= MAX_ATTEMPTS) {
          this.logger.error(
            {
              media_id: envelope.id,
              account_id: envelope.account_id,
              external_message_id: envelope.external_message_id,
              attempts: envelope.attempts
            },
            "media_outbox_dead"
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
            media_id: envelope.id,
            account_id: envelope.account_id,
            attempts: envelope.attempts,
            error_name: error instanceof Error ? error.name : "UnknownError"
          },
          "media_outbox_delivery_failed"
        );
      }
    }
    return { delivered, pending };
  }

  count(): number {
    return readdirSync(this.root).filter((name) => name.endsWith(".media")).length;
  }

  private write(envelope: InboundMediaEnvelope): void {
    const metadata: StoredMetadata = {
      schema: ENVELOPE_SCHEMA,
      id: envelope.id,
      account_id: envelope.account_id,
      external_message_id: envelope.external_message_id,
      media_kind: envelope.media_kind,
      media_type: envelope.media_type,
      file_name: envelope.file_name,
      sha256: envelope.sha256,
      attempts: envelope.attempts,
      next_attempt_at: envelope.next_attempt_at,
      created_at: envelope.created_at
    };
    const metadataBytes = Buffer.from(JSON.stringify(metadata), "utf8");
    if (metadataBytes.length <= 0 || metadataBytes.length > 32 * 1024) {
      throw new Error("media_outbox_metadata_size_invalid");
    }
    const length = Buffer.allocUnsafe(LENGTH_BYTES);
    length.writeUInt32BE(metadataBytes.length, 0);
    const plaintext = Buffer.concat([length, metadataBytes, envelope.content]);
    if (plaintext.length > MAX_FILE_BYTES) {
      throw new Error("media_outbox_file_size_invalid");
    }
    const nonce = randomBytes(NONCE_BYTES);
    const cipher = createCipheriv("aes-256-gcm", this.encryptionKey, nonce);
    cipher.setAAD(MAGIC);
    const ciphertext = Buffer.concat([cipher.update(plaintext), cipher.final()]);
    const payload = Buffer.concat([MAGIC, nonce, cipher.getAuthTag(), ciphertext]);
    const finalPath = resolve(join(this.root, `${envelope.id}.media`));
    const temporary = join(
      dirname(finalPath),
      `.${basename(finalPath)}.${randomUUID()}.tmp`
    );
    writeFileSync(temporary, payload, { mode: 0o600 });
    renameSync(temporary, finalPath);
  }

  private read(path: string): InboundMediaEnvelope {
    const descriptor = openSync(path, "r");
    let payload: Buffer;
    try {
      const metadata = fstatSync(descriptor);
      if (!metadata.isFile() || metadata.size <= MAGIC.length + NONCE_BYTES + TAG_BYTES || metadata.size > MAX_FILE_BYTES) {
        throw new Error("media_outbox_file_size_invalid");
      }
      payload = readFileSync(descriptor);
    } finally {
      closeSync(descriptor);
    }
    if (!payload.subarray(0, MAGIC.length).equals(MAGIC)) {
      throw new Error("media_outbox_magic_invalid");
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
      throw new Error("media_outbox_plaintext_invalid");
    }
    const metadataLength = plaintext.readUInt32BE(0);
    if (metadataLength <= 0 || metadataLength > 32 * 1024 || LENGTH_BYTES + metadataLength >= plaintext.length) {
      throw new Error("media_outbox_metadata_size_invalid");
    }
    const candidate = JSON.parse(
      plaintext.subarray(LENGTH_BYTES, LENGTH_BYTES + metadataLength).toString("utf8")
    ) as Partial<StoredMetadata>;
    const content = plaintext.subarray(LENGTH_BYTES + metadataLength);
    if (
      candidate.schema !== ENVELOPE_SCHEMA ||
      typeof candidate.id !== "string" ||
      typeof candidate.account_id !== "string" ||
      typeof candidate.external_message_id !== "string" ||
      typeof candidate.media_type !== "string" ||
      typeof candidate.sha256 !== "string" ||
      typeof candidate.attempts !== "number" ||
      !Number.isInteger(candidate.attempts) ||
      candidate.attempts < 0 ||
      typeof candidate.next_attempt_at !== "number" ||
      !Number.isFinite(candidate.next_attempt_at) ||
      typeof candidate.created_at !== "string" ||
      content.length <= 0 ||
      content.length > MAX_MEDIA_BYTES
    ) {
      throw new Error("media_outbox_envelope_invalid");
    }
    const observedSha256 = createHash("sha256").update(content).digest("hex");
    if (candidate.sha256 !== observedSha256) {
      throw new Error("media_outbox_sha256_mismatch");
    }
    return {
      schema: ENVELOPE_SCHEMA,
      id: candidate.id,
      account_id: assertSafeAccountId(candidate.account_id),
      external_message_id: assertMessageId(candidate.external_message_id),
      media_kind: assertMediaKind(candidate.media_kind),
      media_type: assertMediaType(candidate.media_type),
      file_name: safeFileName(candidate.file_name),
      sha256: candidate.sha256,
      attempts: candidate.attempts,
      next_attempt_at: candidate.next_attempt_at,
      created_at: candidate.created_at,
      content
    };
  }
}

function assertMessageId(value: unknown): string {
  const normalized = String(value || "").trim();
  if (!normalized || normalized.length > 180 || /[\r\n\x00]/.test(normalized)) {
    throw new Error("media_outbox_message_id_invalid");
  }
  return normalized;
}

function assertMediaKind(value: unknown): InboundMediaKind {
  if (value === "image" || value === "video" || value === "audio" || value === "document" || value === "sticker") {
    return value;
  }
  throw new Error("media_outbox_kind_invalid");
}

function assertMediaType(value: unknown): string {
  const normalized = String(value || "application/octet-stream")
    .split(";", 1)[0]
    .trim()
    .toLowerCase();
  if (!normalized || normalized.length > 160 || !/^[a-z0-9.+-]+\/[a-z0-9.+-]+$/.test(normalized)) {
    throw new Error("media_outbox_type_invalid");
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

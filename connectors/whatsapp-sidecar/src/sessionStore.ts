import { createHash, randomUUID } from "node:crypto";
import {
  chmodSync,
  closeSync,
  existsSync,
  futimesSync,
  mkdirSync,
  openSync,
  readFileSync,
  renameSync,
  rmSync,
  statSync,
  writeFileSync
} from "node:fs";
import { basename, dirname, isAbsolute, join, relative, resolve } from "node:path";
import type { SendResult } from "./types.js";

const SAFE_ACCOUNT_ID = /^[a-zA-Z0-9._-]{1,160}$/;
const OWNER_STALE_MS = 5 * 60_000;
const OWNER_HEARTBEAT_MS = 60_000;

export function assertSafeAccountId(accountId: string): string {
  const cleaned = accountId.trim();
  if (!SAFE_ACCOUNT_ID.test(cleaned)) {
    throw new Error("invalid_account_id");
  }
  return cleaned;
}

export interface AccountOwnerLease {
  release(): void;
}

export class SessionStore {
  private readonly ownerHeartbeatMs: number;

  constructor(
    private readonly root: string,
    private readonly ownerStaleMs = OWNER_STALE_MS,
    ownerHeartbeatMs?: number
  ) {
    this.ownerHeartbeatMs = ownerHeartbeatMs ?? OWNER_HEARTBEAT_MS;
    if (
      !Number.isFinite(this.ownerStaleMs) ||
      this.ownerStaleMs <= 0 ||
      !Number.isFinite(this.ownerHeartbeatMs) ||
      this.ownerHeartbeatMs <= 0 ||
      this.ownerHeartbeatMs >= this.ownerStaleMs
    ) {
      throw new Error("invalid_owner_lease_timing");
    }
    mkdirSync(root, { recursive: true, mode: 0o700 });
  }

  accountPath(accountId: string): string {
    const path = this.safeAccountPath(accountId);
    mkdirSync(path, { recursive: true, mode: 0o700 });
    chmodSync(path, 0o700);
    return path;
  }

  credentialsPath(accountId: string): string {
    return join(this.accountPath(accountId), "creds.json");
  }

  credentialsPersisted(accountId: string): boolean {
    try {
      const raw = readFileSync(this.credentialsPath(accountId), "utf8");
      const parsed = JSON.parse(raw) as { me?: { id?: unknown } };
      return typeof parsed?.me?.id === "string" && parsed.me.id.trim().length > 0;
    } catch {
      return false;
    }
  }

  async waitForCredentials(accountId: string, timeoutMs: number): Promise<boolean> {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (this.credentialsPersisted(accountId)) return true;
      await new Promise((resolveDelay) => setTimeout(resolveDelay, 100));
    }
    return this.credentialsPersisted(accountId);
  }

  clearCredentials(accountId: string): void {
    const path = this.safeAccountPath(accountId);
    rmSync(path, { recursive: true, force: true });
    mkdirSync(path, { recursive: true, mode: 0o700 });
    chmodSync(path, 0o700);
  }

  resetAccount(accountId: string): void {
    this.clearCredentials(accountId);
  }

  acquireOwner(accountId: string): AccountOwnerLease {
    const safe = assertSafeAccountId(accountId);
    const ownerPath = resolve(join(this.root, `.owner-${safe}`));
    const ownerFile = join(ownerPath, "owner.json");
    this.assertInsideRoot(ownerPath);
    const token = randomUUID();
    const acquiredAt = new Date().toISOString();

    const acquire = (): number => {
      mkdirSync(ownerPath, { mode: 0o700 });
      try {
        writeFileSync(
          ownerFile,
          JSON.stringify({ pid: process.pid, token, acquired_at: acquiredAt }),
          { mode: 0o600 }
        );
        return openSync(ownerFile, "r+");
      } catch (error) {
        rmSync(ownerPath, { recursive: true, force: true });
        throw error;
      }
    };

    let descriptor: number;
    try {
      descriptor = acquire();
    } catch (error) {
      if (!existsSync(ownerPath)) throw error;
      let stale = false;
      try {
        const leaseMtime = existsSync(ownerFile)
          ? statSync(ownerFile).mtimeMs
          : statSync(ownerPath).mtimeMs;
        stale = Date.now() - leaseMtime > this.ownerStaleMs;
      } catch {
        stale = true;
      }
      if (!stale) throw new Error("whatsapp_connection_owner_busy");
      rmSync(ownerPath, { recursive: true, force: true });
      try {
        descriptor = acquire();
      } catch (retryError) {
        throw new Error("whatsapp_connection_owner_busy", { cause: retryError });
      }
    }

    let released = false;
    const heartbeat = setInterval(() => {
      if (released) return;
      try {
        const now = new Date();
        futimesSync(descriptor, now, now);
      } catch {
        clearInterval(heartbeat);
      }
    }, this.ownerHeartbeatMs);
    heartbeat.unref?.();

    return {
      release: () => {
        if (released) return;
        released = true;
        clearInterval(heartbeat);
        try {
          closeSync(descriptor);
        } catch {
          // Descriptor may already be closed after an unrecoverable filesystem error.
        }
        try {
          const current = JSON.parse(readFileSync(ownerFile, "utf8")) as { token?: unknown };
          if (current.token === token) {
            rmSync(ownerPath, { recursive: true, force: true });
          }
        } catch {
          // A newer owner may already have replaced the lease path.
        }
      }
    };
  }

  readSendResult(accountId: string, idempotencyKey: string, ttlMs: number): SendResult | null {
    const path = this.idempotencyPath(accountId, idempotencyKey);
    try {
      const raw = JSON.parse(readFileSync(path, "utf8")) as {
        created_at?: number;
        result?: SendResult;
      };
      if (
        typeof raw.created_at !== "number" ||
        Date.now() - raw.created_at > ttlMs ||
        !raw.result ||
        raw.result.retryable === true
      ) {
        rmSync(path, { force: true });
        return null;
      }
      return raw.result;
    } catch {
      return null;
    }
  }

  writeSendResult(accountId: string, idempotencyKey: string, result: SendResult): void {
    const path = this.idempotencyPath(accountId, idempotencyKey);
    if (result.retryable === true) {
      rmSync(path, { force: true });
      return;
    }
    const temporary = join(dirname(path), `.${basename(path)}.${randomUUID()}.tmp`);
    writeFileSync(
      temporary,
      JSON.stringify({ created_at: Date.now(), result }),
      { mode: 0o600 }
    );
    renameSync(temporary, path);
  }

  private idempotencyPath(accountId: string, key: string): string {
    const accountPath = this.accountPath(accountId);
    const directory = join(accountPath, "idempotency");
    mkdirSync(directory, { recursive: true, mode: 0o700 });
    const digest = createHash("sha256").update(key).digest("hex");
    const path = resolve(join(directory, `${digest}.json`));
    this.assertInside(accountPath, path);
    return path;
  }

  private safeAccountPath(accountId: string): string {
    const safe = assertSafeAccountId(accountId);
    const path = resolve(join(this.root, safe));
    this.assertInsideRoot(path);
    return path;
  }

  private assertInsideRoot(path: string): void {
    this.assertInside(resolve(this.root), path);
  }

  private assertInside(base: string, path: string): void {
    const relativePath = relative(resolve(base), resolve(path));
    if (relativePath.startsWith("..") || isAbsolute(relativePath)) {
      throw new Error("invalid_account_id");
    }
  }
}
